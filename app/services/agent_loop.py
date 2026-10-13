"""Multi-step agent reasoning loop — the single query pipeline behind /api/v1/ask and the eval runner.

PlannerAgent    G1 input guard + G2 router; selects discrete tools.
RetrieverAgent  dense_vector_search + bm25_keyword_search (+ sql_tabular_query / graph_traverse_query),
                then the G3 context filter.
WriterAgent     drafts an answer citing [chunk_id] markers (streamed).
ValidatorAgent  G4: resolves citations, verifies each claim, and drives the bounded rewrite loop
                with a diagnostic payload (FR-A3, FR-Q7).

`AgentLoopOrchestrator.stream()` yields SSE events; `run()` consumes the same stream, so the API and
the evaluation exercise identical code.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional, Sequence

from app.core.config import Settings
from app.core.errors import GrantLoomError
from app.core.metrics import REWRITES
from app.core.templates import load_template, render_template
from app.ports.llm_client import LLMClientPort
from app.ports.models import CitationCoordinate, DocumentChunk
from app.ports.registry import RegistryPort
from app.services.guardrails import (
    STRATEGIES,
    ContextFilter,
    InputGuard,
    OutputGuard,
    StrategyRouter,
    VerificationResult,
    gate_event,
)
from app.services.retrieval_service import ABSTENTION_MESSAGE, HybridRetrievalService, RetrievalResult
from app.services.retriever_tools import GraphQueryTool, StructuredQueryTool, ToolOutcome

logger = logging.getLogger("grantloom.services.agent_loop")

UNVERIFIED_WARNING = "Some statements could not be verified against the cited sources after the maximum number of rewrites."


@dataclass
class AgentToolExecution:
    tool_name: str
    arguments: dict[str, Any]
    result_count: int
    status: str = "ok"
    duration_ms: float = 0.0


@dataclass
class AgentPlan:
    intent: str
    strategy: str
    selected_tools: list[str]


@dataclass
class AgentRunResult:
    plan: Optional[AgentPlan]
    tool_executions: list[AgentToolExecution]
    final_answer: str
    citations: list[CitationCoordinate]
    confidence_score: float
    faithfulness: float
    resolvability: float
    abstention: bool = False
    blocked: bool = False
    refusal_reason: str = ""
    verified: bool = False
    unverified_claims: list[str] = field(default_factory=list)
    attempts: int = 0
    context_texts: list[str] = field(default_factory=list)
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


@dataclass
class RetrievalOutcome:
    chunks: list[DocumentChunk]
    tool_evidence: list[str]
    tool_executions: list[AgentToolExecution]
    retrieval: RetrievalResult
    dropped: list[dict[str, str]]
    filter_backend: str

    @property
    def abstention(self) -> bool:
        return not self.chunks


def citation_dict(c: CitationCoordinate) -> dict[str, Any]:
    return {
        "chunk_id": c.chunk_id,
        "doc_id": c.doc_id,
        "filename": c.filename,
        "page": c.page,
        "line": c.line,
        "sheet": c.sheet,
        "row": c.row,
        "excerpt": c.excerpt,
        "verified": c.verified,
    }


class PlannerAgent:
    """G1 + G2, then selects the discrete tools for the chosen strategy."""

    def __init__(
        self,
        input_guard: InputGuard,
        strategy_router: StrategyRouter,
        structured_tool: Optional[StructuredQueryTool] = None,
        graph_tool: Optional[GraphQueryTool] = None,
        semantic_embeddings: bool = False,
    ) -> None:
        self.input_guard = input_guard
        self.strategy_router = strategy_router
        self.structured_tool = structured_tool
        self.graph_tool = graph_tool
        self.semantic_embeddings = semantic_embeddings

    def available_strategies(self) -> list[str]:
        # Dense-only "semantic" retrieval is offered only when the embedder is actually semantic.
        available = ["hybrid", "keyword"] + (["semantic"] if self.semantic_embeddings else [])
        if self.structured_tool is not None and self.structured_tool.available():
            available.append("structured")
        if self.graph_tool is not None:
            available.append("graph")
        return [s for s in STRATEGIES if s in available]

    def tools_for(self, strategy: str) -> list[str]:
        tools = []
        if strategy in ("hybrid", "semantic", "structured", "graph"):
            tools.append("dense_vector_search")
        if strategy in ("hybrid", "keyword", "structured", "graph"):
            tools.append("bm25_keyword_search")
        if strategy == "structured":
            tools.append("sql_tabular_query")
        if strategy == "graph":
            tools.append("graph_traverse_query")
        return tools


class RetrieverAgent:
    """Executes the planned tools, then applies the G3 context filter."""

    def __init__(
        self,
        retrieval_service: HybridRetrievalService,
        context_filter: ContextFilter,
        structured_tool: Optional[StructuredQueryTool] = None,
        graph_tool: Optional[GraphQueryTool] = None,
    ) -> None:
        self.retrieval_service = retrieval_service
        self.context_filter = context_filter
        self.structured_tool = structured_tool
        self.graph_tool = graph_tool

    async def execute(self, query: str, plan: AgentPlan, api_key: Optional[str] = None) -> RetrievalOutcome:
        cfg = self.retrieval_service.settings.retrieval
        retrieval = await self.retrieval_service.retrieve(query=query, strategy=plan.strategy)
        executions: list[AgentToolExecution] = []
        errors = {e.split(":", 1)[0]: e for e in retrieval.errors}
        if "dense_vector_search" in plan.selected_tools:
            executions.append(AgentToolExecution(
                "dense_vector_search", {"query": query, "top_k": cfg.dense_top_k}, retrieval.dense_count,
                f"error: {errors['dense_vector_search']}" if "dense_vector_search" in errors else "ok", retrieval.dense_ms,
            ))
        if "bm25_keyword_search" in plan.selected_tools:
            executions.append(AgentToolExecution(
                "bm25_keyword_search", {"query": query, "top_k": cfg.bm25_top_k}, retrieval.bm25_count,
                f"error: {errors['bm25_keyword_search']}" if "bm25_keyword_search" in errors else "ok", retrieval.bm25_ms,
            ))

        outcomes: list[ToolOutcome] = []
        if "sql_tabular_query" in plan.selected_tools and self.structured_tool is not None:
            outcomes.append(await self.structured_tool.run(query, api_key))
        if "graph_traverse_query" in plan.selected_tools and self.graph_tool is not None:
            outcomes.append(self.graph_tool.run(query))
        for o in outcomes:
            executions.append(AgentToolExecution(o.tool_name, o.arguments, o.result_count, o.status, o.duration_ms))

        filtered = await self.context_filter.filter(query, retrieval.chunks, retrieval.dense_scores, api_key)
        tool_chunks, tool_dropped = self.context_filter.screen_tool_evidence(
            [c for o in outcomes for c in o.evidence_chunks]
        )
        merged: dict[str, DocumentChunk] = {}
        for c in [*tool_chunks, *filtered.chunks]:
            merged.setdefault(c.chunk_id, c)
        tool_evidence = [t for o in outcomes for t in o.evidence_text] if tool_chunks else []
        return RetrievalOutcome(
            chunks=list(merged.values()),
            tool_evidence=tool_evidence,
            tool_executions=executions,
            retrieval=retrieval,
            dropped=filtered.dropped + tool_dropped,
            filter_backend=filtered.backend,
        )


class WriterAgent:
    """Drafts an answer grounded in the context, citing [chunk_id] markers."""

    def __init__(self, llm_client: LLMClientPort, model: str, registry: RegistryPort) -> None:
        self.llm_client = llm_client
        self.model = model
        self.registry = registry

    def build_prompt(
        self,
        query: str,
        chunks: Sequence[DocumentChunk],
        tool_evidence: Sequence[str],
        issues: Sequence[str] = (),
    ) -> str:
        blocks = []
        for c in chunks:
            doc = self.registry.get_document(c.doc_id)
            source = f"{doc.filename} (version {doc.version})" if doc else "unknown"
            coord = f"Sheet '{c.sheet_name}', Rows {c.row_start}-{c.row_end}" if c.sheet_name else f"Page {c.page_number}"
            blocks.append(f"[{c.chunk_id}]\nSource: {source}, {coord}\nText: {c.text_content.strip()}")
        prompt = "Context Documents:\n" + "\n\n".join(blocks)
        if tool_evidence:
            prompt += (
                "\n\nTool Results (computed from the context chunks above; cite the underlying [chunk_id]):\n"
                + "\n".join(f"- {t}" for t in tool_evidence)
            )
        prompt += f"\n\nQuestion: {query}\n"
        if issues:
            prompt += "\n" + render_template("prompts/writer_rewrite_feedback.txt", issues="\n".join(issues)) + "\n"
        return prompt + "\nAnswer with explicit [chunk_id] citations:"

    def stream(self, prompt: str, api_key: Optional[str]) -> AsyncIterator[str]:
        return self.llm_client.stream_response(
            prompt=prompt,
            model=self.model,
            system_prompt=load_template("prompts/writer_system.txt"),
            temperature=0.0,
            api_key=api_key,
        )


class ValidatorAgent:
    """G4 verification of a draft."""

    def __init__(self, output_guard: OutputGuard) -> None:
        self.output_guard = output_guard

    async def validate(
        self,
        draft: str,
        context_chunk_ids: Sequence[str],
        tool_evidence: Sequence[str],
        api_key: Optional[str] = None,
    ) -> VerificationResult:
        return await self.output_guard.verify(draft, context_chunk_ids, tool_evidence, api_key)


class AgentLoopOrchestrator:
    """Planner -> Retriever -> Writer -> Validator with a bounded rewrite loop (1 + max_rewrite_attempts drafts)."""

    def __init__(
        self,
        planner: PlannerAgent,
        retriever: RetrieverAgent,
        writer: WriterAgent,
        validator: ValidatorAgent,
        settings: Settings,
        egress: dict[str, Any],
    ) -> None:
        self.planner = planner
        self.retriever = retriever
        self.writer = writer
        self.validator = validator
        self.settings = settings
        self.egress = egress

    async def stream(self, query: str, api_key: Optional[str] = None) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        try:
            async for event in self._stream(query, api_key):
                yield event
        except GrantLoomError as exc:
            logger.error("Agent pipeline failed: %s", exc.detail)
            yield ("error", exc.to_problem_details())

    async def _stream(self, query: str, api_key: Optional[str]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        # 0. FR-Q10 egress / provenance: where data goes before any model call
        yield ("egress", self.egress)

        # 1+2. G1 input guard and G2 strategy router depend only on the query: run both judge
        # calls in parallel (latency). If G1 refuses, the routing result is discarded.
        available = self.planner.available_strategies()
        g1_task = asyncio.create_task(self.planner.input_guard.evaluate(query, api_key))
        g2_task = asyncio.create_task(self.planner.strategy_router.evaluate(query, available, api_key))
        try:
            g1 = await g1_task
        except BaseException:
            g2_task.cancel()
            raise
        yield ("guard", gate_event(g1))
        if not g1.passed:
            g2_task.cancel()
            # CancelledError is a BaseException in 3.12 — suppress it explicitly or the stream dies
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await g2_task
            refusal = g1.metadata.get("refusal") or g1.metadata.get("message") or "Your query was declined by the input guardrail."
            yield ("generate", {"token": refusal, "attempt": 0})
            yield ("done", {
                "answer": refusal, "citations": [], "confidence_score": 0.0, "abstention": False,
                "refusal": True, "refusal_reason": g1.reason, "verified": False,
            })
            return
        sanitized = g1.metadata.get("sanitized_query", query)
        g2 = await g2_task
        strategy = g2.metadata["strategy"]
        plan = AgentPlan(intent=g2.reason, strategy=strategy, selected_tools=self.planner.tools_for(strategy))
        yield ("route", gate_event(g2, strategy=strategy, available_strategies=available))
        yield ("plan", {"strategy": strategy, "tools": plan.selected_tools})

        # 3. Retriever agent (tools + G3)
        outcome = await self.retriever.execute(sanitized, plan, api_key)
        for execution in outcome.tool_executions:
            yield ("tool", {
                "tool": execution.tool_name, "status": execution.status, "result_count": execution.result_count,
                "duration_ms": execution.duration_ms, "arguments": execution.arguments,
            })
        yield ("retrieve", {
            "chunks_retrieved": len(outcome.retrieval.chunks),
            "max_relevance": round(outcome.retrieval.max_relevance, 3),
            "strategy": strategy,
            "dense_count": outcome.retrieval.dense_count,
            "bm25_count": outcome.retrieval.bm25_count,
            "errors": outcome.retrieval.errors,
        })
        yield ("filter", {
            "gate": "G3",
            "chunks_retained": len(outcome.chunks),
            "dropped": outcome.dropped,
            "abstention": outcome.abstention,
            "backend": outcome.filter_backend,
        })

        # FR-Q8 abstention contract
        if outcome.abstention:
            yield ("generate", {"token": ABSTENTION_MESSAGE, "attempt": 0})
            yield ("verify", {"gate": "G4", "verified": True, "confidence_score": 1.0, "citations_count": 0})
            yield ("done", {
                "answer": ABSTENTION_MESSAGE, "citations": [], "confidence_score": 1.0, "abstention": True,
                "strategy": strategy, "verified": True, "tool_executions": [e.__dict__ for e in outcome.tool_executions],
            })
            return

        # 4-5. Writer + validator with bounded rewrite loop
        context_ids = [c.chunk_id for c in outcome.chunks]
        max_drafts = 1 + self.settings.guardrails.max_rewrite_attempts
        issues: list[str] = []
        result: Optional[VerificationResult] = None
        draft = ""
        attempt = 0
        for attempt in range(1, max_drafts + 1):
            prompt = self.writer.build_prompt(sanitized, outcome.chunks, outcome.tool_evidence, issues)
            tokens: list[str] = []
            async for token in self.writer.stream(prompt, api_key):
                tokens.append(token)
                yield ("generate", {"token": token, "attempt": attempt})
            draft = "".join(tokens)

            if ABSTENTION_MESSAGE.lower() in draft.lower() and "[" not in draft:
                yield ("verify", {"gate": "G4", "verified": True, "confidence_score": 1.0, "citations_count": 0, "attempt": attempt})
                yield ("done", {
                    "answer": ABSTENTION_MESSAGE, "citations": [], "confidence_score": 1.0, "abstention": True,
                    "strategy": strategy, "verified": True, "attempts": attempt,
                    "context_chunk_ids": context_ids, "tool_executions": [e.__dict__ for e in outcome.tool_executions],
                })
                return

            result = await self.validator.validate(draft, context_ids, outcome.tool_evidence, api_key)
            yield ("verify", {
                "gate": "G4",
                "attempt": attempt,
                "verified": result.passed,
                "citations_count": len(result.citations),
                "unresolved_citations": result.unresolved_ids,
                "resolvability": result.resolvability,
                "faithfulness": result.faithfulness,
                "unsupported_claims": [c.claim for c in result.unsupported_claims],
                "confidence_score": result.confidence_score,
                "backend": result.backend,
            })
            if result.passed or attempt == max_drafts:
                break
            issues = result.issues()
            reason = "unresolved_citations" if (result.unresolved_ids or not result.citations) else "unsupported_claims"
            REWRITES.labels(reason=reason).inc()
            yield ("rewrite", {
                "attempt": attempt,
                "max_attempts": self.settings.guardrails.max_rewrite_attempts,
                "reason": reason,
                "issues": issues,
                "discard_draft": True,
            })

        assert result is not None
        verified = result.passed
        done: dict[str, Any] = {
            "answer": result.cleaned_text,
            "citations": [citation_dict(c) for c in result.citations],
            "confidence_score": result.confidence_score,
            "faithfulness": result.faithfulness,
            "resolvability": result.resolvability,
            "abstention": False,
            "strategy": strategy,
            "verified": verified,
            "attempts": attempt,
            "unverified_claims": [c.claim for c in result.unsupported_claims],
            "context_chunk_ids": context_ids,
            "tool_executions": [e.__dict__ for e in outcome.tool_executions],
        }
        if not verified:
            done["warning"] = UNVERIFIED_WARNING
        yield ("done", done)

    async def run(self, query: str, api_key: Optional[str] = None) -> AgentRunResult:
        """Consume the event stream into a result object (used by the evaluation runner)."""
        events: list[tuple[str, dict[str, Any]]] = []
        plan: Optional[AgentPlan] = None
        executions: list[AgentToolExecution] = []
        context_texts: list[str] = []
        async for name, payload in self.stream(query, api_key):
            events.append((name, payload))
            if name == "plan":
                plan = AgentPlan(intent="", strategy=payload["strategy"], selected_tools=payload["tools"])
            elif name == "tool":
                executions.append(AgentToolExecution(
                    payload["tool"], payload["arguments"], payload["result_count"], payload["status"], payload["duration_ms"],
                ))
            elif name == "error":
                raise RuntimeError(f"Agent pipeline error: {payload.get('detail')}")

        done = next(p for n, p in events if n == "done")
        for cid in done.get("context_chunk_ids", []):
            chunk = self.writer.registry.get_chunk(cid)
            if chunk is not None:
                context_texts.append(chunk.text_content)
        citations = [CitationCoordinate(**c) for c in done.get("citations", [])]
        return AgentRunResult(
            plan=plan,
            tool_executions=executions,
            final_answer=done["answer"],
            citations=citations,
            confidence_score=done.get("confidence_score", 0.0),
            faithfulness=done.get("faithfulness", 1.0 if done.get("abstention") else 0.0),
            resolvability=done.get("resolvability", 1.0 if done.get("abstention") else 0.0),
            abstention=done.get("abstention", False),
            blocked=bool(done.get("refusal")),
            refusal_reason=done.get("refusal_reason", ""),
            verified=done.get("verified", False),
            unverified_claims=done.get("unverified_claims", []),
            attempts=done.get("attempts", 0),
            context_texts=context_texts,
            events=events,
        )
