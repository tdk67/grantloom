"""Unit tests for the RAG evaluation framework and telemetry components."""

from __future__ import annotations

import re

from app.evaluation.synthesizer import QuestionSynthesizer
from app.evaluation.telemetry import PipelineLatencyProfile


def test_pipeline_latency_profile_to_dict():
    profile = PipelineLatencyProfile(
        guard_ms=12.34,
        route_ms=45.67,
        retrieve_ms=89.12,
        filter_ms=23.45,
        first_token_ms=105.2,
        generate_ms=500.0,
        verify_ms=150.0,
        total_ms=925.78,
        token_count=50,
        tokens_per_second=100.0,
        tool_timings={"dense_vector_search": 80.0, "bm25_keyword_search": 9.12},
    )
    d = profile.to_dict()
    assert d["guard_ms"] == 12.3
    assert d["route_ms"] == 45.7
    assert d["retrieve_ms"] == 89.1
    assert d["total_ms"] == 925.8
    assert d["token_count"] == 50
    assert d["tokens_per_second"] == 100.0
    assert d["tool_timings"]["dense_vector_search"] == 80.0


CORRECT_ANSWERS = {
    "TC01_KEYWORD_BM25": "NFPA 855 requires fire separation [x].",
    "TC02_SEMANTIC_VECTOR": "The microgrid transitions to islanding mode [x].",
    "TC03_GRAPH_TRAVERSE": "Marcus Holloway leads firmware [x].",
    "TC04_STRUCTURED_SQL": "The total is $151,000.",
    "TC05_MULTI_DOC_CROSS_SOLICITATION": "DOE requires 20.0% cost share; EPA requires 0.0% [x].",
    "TC06_MULTI_DOC_PROPOSAL_VS_SOLICITATION": "Yes, it falls within the allowable limits.",
    "TC07_MULTI_DOC_BUDGET_VS_COMMITMENT_LETTER": "The letter commits $312,500, which matches.",
    "TC08_GUARDRAIL_INJECTION": "Request blocked by the input guardrail.",
    "TC09_ABSTENTION_UNRELATED": "I cannot find sufficient evidence in the provided documents.",
}


def test_every_expected_regex_matches_a_correct_answer_and_rejects_a_wrong_one():
    suite = QuestionSynthesizer().get_canonical_suite()
    assert {tc.id for tc in suite} == set(CORRECT_ANSWERS)
    for tc in suite:
        assert re.search(tc.expected_regex, CORRECT_ANSWERS[tc.id]), tc.id
        assert not re.search(tc.expected_regex, "Totally unrelated sentence about weather."), tc.id


def test_canonical_nine_suite_coverage():
    synthesizer = QuestionSynthesizer()
    suite = synthesizer.get_canonical_suite()
    assert len(suite) == 9

    types = {tc.type for tc in suite}
    expected_types = {"keyword", "semantic", "graph", "structured", "composite", "adversarial", "unanswerable"}
    assert expected_types.issubset(types)

    # TC01 keyword: check source_docs include both past proposal and DOE solar
    tc01 = next(tc for tc in suite if tc.id == "TC01_KEYWORD_BM25")
    assert "Past_Proposal_2024_Sample.pdf" in tc01.source_docs
    assert "DOE_Solar_2026_Sample.pdf" in tc01.source_docs

    # TC04 structured: verify regex matches formatted currency
    tc04 = next(tc for tc in suite if tc.type == "structured")
    assert tc04.expected_sql is not None
    assert "SUM" in tc04.expected_sql
    assert re.search(tc04.expected_regex, "$151,000") is not None
    assert re.search(tc04.expected_regex, "151,000") is not None
    assert re.search(tc04.expected_regex, "151000") is not None

    # TC05 cross-solicitation: verify EPA resilience sample is targeted
    tc05 = next(tc for tc in suite if tc.id == "TC05_MULTI_DOC_CROSS_SOLICITATION")
    assert "EPA_Resilience_2026_Sample.pdf" in tc05.source_docs

    # TC07 budget vs letter: verify Maryland Clean Energy Center is targeted
    tc07 = next(tc for tc in suite if tc.id == "TC07_MULTI_DOC_BUDGET_VS_COMMITMENT_LETTER")
    assert "Maryland Clean Energy Center" in tc07.question
    assert "Cost_Share_Commitment_Letter_2025.pdf" in tc07.source_docs

    # Verify adversarial question is flagged
    adv_tc = next(tc for tc in suite if tc.type == "adversarial")
    assert adv_tc.adversarial is True

    # Verify unanswerable question is flagged
    unans_tc = next(tc for tc in suite if tc.type == "unanswerable")
    assert unans_tc.unanswerable is True


def test_percentile_linear_interpolation():
    from app.evaluation.harness import _percentile

    assert _percentile([], 95.0) == 0.0
    assert _percentile([10.0], 95.0) == 10.0

    # 9 elements: indices 0..8. k = 8 * 0.95 = 7.6.
    # Linear interpolation between s[7]=80.0 and s[8]=90.0:
    # 80.0 * 0.4 + 90.0 * 0.6 = 32.0 + 54.0 = 86.0 (not 90.0!)
    data = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]
    p95 = _percentile(data, 95.0)
    assert p95 == 86.0


def test_triad_score_average_with_none_components():
    from app.evaluation.harness import TriadScore

    # When faithfulness and answer_relevance are None (offline / abstention)
    triad = TriadScore(context_relevance=1.0, citation_resolvability=1.0)
    assert triad.faithfulness is None
    assert triad.answer_relevance is None
    assert triad.average_score() == 1.0

    # When all 4 are present
    triad_full = TriadScore(
        context_relevance=1.0,
        faithfulness=0.8,
        answer_relevance=0.6,
        citation_resolvability=1.0,
    )
    assert triad_full.average_score() == 0.85


async def test_harness_scoring_behavior():
    from unittest.mock import MagicMock
    from app.evaluation.harness import BenchmarkHarness
    from app.services.agent_loop import AgentRunResult

    ws = MagicMock()
    ws.evaluator = None
    ws.eval_client = None
    ws.eval_model = None

    harness = BenchmarkHarness(ws)

    # 1. Abstention should return None for faithfulness, not 1.0
    abstained_result = AgentRunResult(
        plan=None,
        tool_executions=[],
        final_answer="This information was not found in the indexed documents",
        citations=[],
        confidence_score=0.0,
        faithfulness=0.0,
        resolvability=0.0,
        abstention=True,
    )
    f_score = await harness._score_faithfulness(abstained_result)
    assert f_score is None

    # 2. Blocked result should return None for faithfulness
    blocked_result = AgentRunResult(
        plan=None,
        tool_executions=[],
        final_answer="Declined",
        citations=[],
        confidence_score=0.0,
        faithfulness=0.0,
        resolvability=0.0,
        blocked=True,
    )
    assert await harness._score_faithfulness(blocked_result) is None

    # 3. Offline answer relevance should return None when no eval_client
    ans_score = await harness._score_answer_relevance("Question?", "Answer text")
    assert ans_score is None

