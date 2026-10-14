"""Unit tests for the RAG evaluation framework and telemetry components."""

from __future__ import annotations

import time
import pytest

from app.evaluation.harness import TriadScore, TestCaseResult, BenchmarkReport
from app.evaluation.synthesizer import QuestionSynthesizer
from app.evaluation.telemetry import PipelineLatencyProfile, StageTimer


def test_stage_timer_records_duration():
    with StageTimer() as timer:
        time.sleep(0.01)
    assert timer.elapsed_ms >= 8.0


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


def test_canonical_nine_suite_coverage():
    synthesizer = QuestionSynthesizer()
    suite = synthesizer.get_canonical_suite()
    assert len(suite) == 9

    types = {tc.type for tc in suite}
    expected_types = {"keyword", "semantic", "graph", "structured", "composite", "adversarial", "unanswerable"}
    assert expected_types.issubset(types)

    # Verify structured question has an SQL reference
    structured_tc = next(tc for tc in suite if tc.type == "structured")
    assert structured_tc.expected_sql is not None
    assert "SUM" in structured_tc.expected_sql

    # Verify adversarial question is flagged
    adv_tc = next(tc for tc in suite if tc.type == "adversarial")
    assert adv_tc.adversarial is True

    # Verify unanswerable question is flagged
    unans_tc = next(tc for tc in suite if tc.type == "unanswerable")
    assert unans_tc.unanswerable is True


def test_triad_score_averaging():
    score = TriadScore(
        context_relevance=1.0,
        faithfulness=0.9,
        answer_relevance=0.8,
        citation_resolvability=1.0,
    )
    assert round(score.average_score(), 3) == 0.925
