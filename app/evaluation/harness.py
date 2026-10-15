"""RAG Triad and benchmark evaluation harness.

Evaluates RAG systems across:
1. Context Relevance (precision of retrieved context)
2. Grounded Faithfulness (claim verification against cited source coordinates)
3. Answer Relevance (query-answer alignment judged by an independent model)
4. Text-to-SQL Execution Equivalence (deterministic SQL execution comparison via SqlGuard)
5. Physical Citation Resolvability (verifiable registry coordinate mapping)
6. Per-stage Latency Profiling (Guard, Route, Retrieval, Filter, Gen, Verify)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from app.core.templates import render_template
from app.evaluation.synthesizer import EvalTestCase
from app.evaluation.workspace import EvalWorkspace
from app.ports.models import CitationCoordinate
from app.services.agent_loop import AgentRunResult
from app.services.guardrails import OutputGuard

logger = logging.getLogger("grantloom.evaluation.harness")


def _percentile(values: Sequence[float], p: float) -> float:
    """Compute the p-th percentile of values using linear interpolation (0 <= p <= 100)."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1
    if c < len(s):
        d0 = s[f] * (c - k)
        d1 = s[c] * (k - f)
        return d0 + d1
    return s[f]


@dataclass
class TriadScore:
    """The RAG Triad: Context Relevance, Faithfulness, and Answer Relevance."""
    context_relevance: float = 0.0
    faithfulness: Optional[float] = None
    answer_relevance: Optional[float] = None
    citation_resolvability: float = 0.0
    sql_execution_match: Optional[bool] = None

    def average_score(self) -> float:
        components = [self.context_relevance, self.citation_resolvability]
        if self.faithfulness is not None:
            components.append(self.faithfulness)
        if self.answer_relevance is not None:
            components.append(self.answer_relevance)
        return sum(components) / len(components) if components else 0.0


@dataclass
class TestCaseResult:
    """Detailed evaluation result for a single test case."""
    __test__ = False
    test_id: str
    question_type: str
    question: str
    expected: str
    actual_answer: str
    passed: bool
    triad: TriadScore
    timings: dict[str, Any] = field(default_factory=dict)
    retrieved_count: int = 0
    retained_count: int = 0
    citations: list[CitationCoordinate] = field(default_factory=list)
    unverified_claims: list[str] = field(default_factory=list)
    abstained: bool = False
    blocked: bool = False
    attempts: int = 0
    error: Optional[str] = None


@dataclass
class BenchmarkReport:
    """Complete summary report across all benchmark test cases and corpus scales."""
    solicitation_count: int
    document_count: int
    total_cases: int
    passed_cases: int
    accuracy_rate: float
    mean_faithfulness: float
    mean_context_relevance: float
    mean_answer_relevance: float
    mean_resolvability: float
    mean_latency_ms: float
    p95_latency_ms: float
    stage_latencies_ms: dict[str, float] = field(default_factory=dict)
    results: list[TestCaseResult] = field(default_factory=list)
    writer_model: str = ""
    judge_model: str = ""


class BenchmarkHarness:
    """Executes test cases against an EvalWorkspace and computes RAG Triad metrics."""

    def __init__(self, workspace: EvalWorkspace) -> None:
        self.ws = workspace

    async def evaluate_test_case(self, test: EvalTestCase) -> TestCaseResult:
        """Executes and scores a single test case through the complete pipeline."""
        try:
            run_result: AgentRunResult = await self.ws.ask_service.run(test.question, self.ws.api_key)
        except Exception as exc:
            logger.error("Test case %s failed with exception: %s", test.id, exc)
            return TestCaseResult(
                test_id=test.id,
                question_type=test.type,
                question=test.question,
                expected=test.expected,
                actual_answer="",
                passed=False,
                triad=TriadScore(),
                error=str(exc),
            )

        # 1. Check for expected guardrail refusal
        if test.adversarial:
            passed = run_result.blocked is True
            triad = TriadScore(
                context_relevance=1.0 if passed else 0.0,
                faithfulness=None,
                answer_relevance=1.0 if passed else 0.0,
                citation_resolvability=1.0,
            )
            return TestCaseResult(
                test_id=test.id,
                question_type=test.type,
                question=test.question,
                expected=test.expected,
                actual_answer=run_result.final_answer,
                passed=passed,
                triad=triad,
                timings=run_result.timings,
                blocked=run_result.blocked,
                attempts=run_result.attempts,
            )

        # 2. Check for expected out-of-domain abstention or G1 guard refusal
        if test.unanswerable:
            ans_lower = run_result.final_answer.lower()
            refused_or_abstained = (
                run_result.abstention is True
                or run_result.blocked is True
                or "cannot find sufficient evidence" in ans_lower
                or "declined" in ans_lower
                or "not found" in ans_lower
            )
            passed = refused_or_abstained
            triad = TriadScore(
                context_relevance=1.0 if passed else 0.0,
                faithfulness=None,
                answer_relevance=1.0 if passed else 0.0,
                citation_resolvability=1.0,
            )
            return TestCaseResult(
                test_id=test.id,
                question_type=test.type,
                question=test.question,
                expected=test.expected,
                actual_answer=run_result.final_answer,
                passed=passed,
                triad=triad,
                timings=run_result.timings,
                abstained=run_result.abstention,
                blocked=run_result.blocked,
                attempts=run_result.attempts,
            )

        # 3. Answer Matching
        actual = run_result.final_answer
        if test.expected_regex:
            answer_matched = re.search(test.expected_regex, actual) is not None
        else:
            answer_matched = test.expected.lower() in actual.lower()

        # 4. Context Relevance: Did retrieved chunks contain the ground-truth target?
        context_corpus = "\n".join(run_result.context_texts)
        if test.expected_regex:
            context_relevant = re.search(test.expected_regex, context_corpus) is not None
        else:
            context_relevant = test.expected.lower() in context_corpus.lower()
        context_relevance_score = 1.0 if context_relevant else 0.0

        # 5. Faithfulness via Independent Judge (abstentions return None)
        faithfulness_score = await self._score_faithfulness(run_result)

        # 6. Answer Relevance via Independent Judge (returns None when offline / unjudged)
        answer_relevance_score = await self._score_answer_relevance(test.question, actual)

        # 7. Citation Resolvability (100% of cited markers exist in active registry)
        resolvability_score = run_result.resolvability

        # 8. SQL Execution Equivalence for structured queries (guarded via SqlGuard)
        sql_match: Optional[bool] = None
        if test.expected_sql and run_result.tool_executions:
            sql_match = self._verify_sql_equivalence(test.expected_sql, run_result)

        # 9. Source Doc Provenance Validation
        provenance_match: bool = True
        if test.source_docs:
            cited_files = {c.filename.lower() for c in run_result.citations}
            provenance_match = any(
                any(src.lower() in fname for src in test.source_docs)
                for fname in cited_files
            )

        triad = TriadScore(
            context_relevance=context_relevance_score,
            faithfulness=faithfulness_score,
            answer_relevance=answer_relevance_score,
            citation_resolvability=resolvability_score,
            sql_execution_match=sql_match,
        )

        passed = answer_matched and (sql_match is not False) and (not run_result.abstention) and provenance_match

        return TestCaseResult(
            test_id=test.id,
            question_type=test.type,
            question=test.question,
            expected=test.expected,
            actual_answer=actual,
            passed=passed,
            triad=triad,
            timings=run_result.timings,
            retrieved_count=len(run_result.context_texts),
            retained_count=len(run_result.citations),
            citations=run_result.citations,
            unverified_claims=run_result.unverified_claims,
            abstained=run_result.abstention,
            blocked=run_result.blocked,
            attempts=run_result.attempts,
        )

    async def _score_faithfulness(self, result: AgentRunResult) -> Optional[float]:
        """Evaluates whether all claims in the generated draft are grounded in cited evidence.

        Abstentions or blocked responses produce no factual claims and return None so they do not
        falsely inflate or deflate the corpus faithfulness score.
        """
        if result.abstention or result.blocked:
            return None
        if not result.citations:
            return 0.0
        if not self.ws.evaluator:
            return result.faithfulness

        guard = OutputGuard(self.ws.registry, self.ws.evaluator, self.ws.settings.guardrails.citation_excerpt_chars)
        resolved_ids = [c.chunk_id for c in result.citations]
        claims = guard.extract_claims(result.final_answer, resolved_ids)
        if not claims:
            return 1.0

        payload = []
        for claim in claims:
            evidence_parts = []
            for cid in claim.cited_chunk_ids:
                chunk = self.ws.registry.get_chunk(cid)
                if chunk:
                    evidence_parts.append(chunk.text_content)
            payload.append((claim.text, claim.cited_chunk_ids, "\n".join(evidence_parts)))

        verdicts = await self.ws.evaluator.verify_claims(payload, self.ws.api_key)
        grounded = sum(1 for v in verdicts if v.verdict == "grounded")
        return grounded / len(verdicts) if verdicts else 1.0

    async def _score_answer_relevance(self, question: str, answer: str) -> Optional[float]:
        """Asks the independent evaluator if the answer directly answers the query."""
        if not answer:
            return 0.0
        if "cannot find sufficient evidence" in answer.lower():
            return 0.0
        if not self.ws.eval_client or not self.ws.eval_model:
            return None

        try:
            prompt = render_template("prompts/judge_answer_relevance.txt", question=question, answer=answer)
            raw = await self.ws.eval_client.generate_response(
                prompt=prompt,
                model=self.ws.eval_model,
                system_prompt="You are an evaluation judge. Respond strictly in valid JSON.",
                temperature=0.0,
                api_key=self.ws.api_key,
                json_output=True,
            )
            parsed = json.loads(raw)
            score = float(parsed.get("score", 1.0 if parsed.get("relevant") else 0.0))
            return max(0.0, min(1.0, score))
        except Exception as exc:
            logger.warning("Answer relevance evaluation failed: %s", exc)
            return None

    def _verify_sql_equivalence(self, expected_sql: str, result: AgentRunResult) -> bool:
        """Executes reference SQL and generated SQL in the structured store and compares results.

        Safely maps abstract sheet names (e.g. sheet_Equipment) to dynamic SQLite table names
        and routes execution exclusively through SqlGuard-confined StructuredStorePort.
        """
        sql_executions = [e for e in result.tool_executions if e.tool_name == "sql_tabular_query"]
        if not sql_executions:
            return False

        generated_sql = sql_executions[0].arguments.get("sql", "")
        if not generated_sql:
            return False

        try:
            tables = self.ws.structured_store.list_tables()
            resolved_sql = expected_sql
            for t in tables:
                pattern = re.compile(rf"\b(sheet_)?{re.escape(t.sheet_name)}\b", re.IGNORECASE)
                resolved_sql = pattern.sub(t.table_name, resolved_sql)

            exp_res = self.ws.structured_store.execute_query(resolved_sql)
            gen_res = self.ws.structured_store.execute_query(generated_sql)
            if exp_res == gen_res:
                return True
            exp_vals = [list(d.values()) for d in exp_res]
            gen_vals = [list(d.values()) for d in gen_res]
            return exp_vals == gen_vals and len(exp_vals) > 0
        except Exception as exc:
            logger.warning("SQL equivalence verification error: %s", exc)
            return False

    async def run_benchmark(
        self,
        test_suite: list[EvalTestCase],
        on_progress: Optional[Callable[[int, int, TestCaseResult], None]] = None,
    ) -> BenchmarkReport:
        """Runs all test cases and aggregates RAG Triad and latency metrics."""
        results: list[TestCaseResult] = []
        total_tests = len(test_suite)
        for idx, test in enumerate(test_suite, start=1):
            if on_progress:
                res = await self.evaluate_test_case(test)
                on_progress(idx, total_tests, res)
            else:
                print(f"  [{idx}/{total_tests}] Running {test.id} ({test.type})...", flush=True)
                res = await self.evaluate_test_case(test)
                status_str = "PASS" if res.passed else "FAIL"
                t = res.timings
                stages_str = (
                    f"Guard: {t.get('guard_ms', 0):.0f}ms | Route: {t.get('route_ms', 0):.0f}ms | "
                    f"Retr: {t.get('retrieve_ms', 0):.0f}ms | Filter: {t.get('filter_ms', 0):.0f}ms | "
                    f"Gen: {t.get('generate_ms', 0):.0f}ms | Verify: {t.get('verify_ms', 0):.0f}ms"
                )
                print(f"       -> {status_str} | Total: {t.get('total_ms', 0):.0f}ms [{stages_str}]", flush=True)
            results.append(res)

        total = len(results)
        passed = sum(1 for r in results if r.passed)
        accuracy = (passed / total) if total else 0.0

        faithfulness_vals = [r.triad.faithfulness for r in results if r.triad.faithfulness is not None]
        context_rel_vals = [r.triad.context_relevance for r in results if not r.blocked]
        ans_rel_vals = [r.triad.answer_relevance for r in results if r.triad.answer_relevance is not None]
        resolv_vals = [r.triad.citation_resolvability for r in results if not r.blocked]

        latencies = [r.timings.get("total_ms", 0.0) for r in results if r.timings.get("total_ms")]
        latencies.sort()
        mean_lat = (sum(latencies) / len(latencies)) if latencies else 0.0
        p95_lat = _percentile(latencies, 95.0)

        stage_avgs: dict[str, float] = {}
        for stage in ("guard_ms", "route_ms", "retrieve_ms", "filter_ms", "first_token_ms", "generate_ms", "verify_ms"):
            vals = [r.timings.get(stage, 0.0) for r in results if r.timings.get(stage) is not None]
            stage_avgs[stage] = (sum(vals) / len(vals)) if vals else 0.0

        doc_count = len(self.ws.registry.list_documents())

        return BenchmarkReport(
            solicitation_count=self.ws.solicitation_count,
            document_count=doc_count,
            total_cases=total,
            passed_cases=passed,
            accuracy_rate=accuracy,
            mean_faithfulness=(sum(faithfulness_vals) / len(faithfulness_vals)) if faithfulness_vals else 0.0,
            mean_context_relevance=(sum(context_rel_vals) / len(context_rel_vals)) if context_rel_vals else 0.0,
            mean_answer_relevance=(sum(ans_rel_vals) / len(ans_rel_vals)) if ans_rel_vals else 0.0,
            mean_resolvability=(sum(resolv_vals) / len(resolv_vals)) if resolv_vals else 0.0,
            mean_latency_ms=mean_lat,
            p95_latency_ms=p95_lat,
            stage_latencies_ms=stage_avgs,
            results=results,
            writer_model=self.ws.writer_label,
            judge_model=self.ws.evaluator_label,
        )
