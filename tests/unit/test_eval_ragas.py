"""Unit tests for the external Ragas-style scorecard runner (no network)."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

import pytest

from app.cli.eval_ragas import (
    JudgeError,
    RagasJudge,
    evaluate_sample,
    independence_problems,
    load_groups,
    run_ragas_evaluation,
    summarize,
    validate_sample,
)
from app.ports.llm_client import LLMClientPort
from tests.helpers import make_settings


class FakeJudgeClient(LLMClientPort):
    """Replies to each metric prompt with canned JSON, or raises."""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls = 0

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def destination(self) -> str:
        return "fake.local"

    async def validate_api_key(self, api_key: str) -> bool:
        return True

    async def describe_model(self, model: str, api_key: Optional[str] = None) -> dict[str, Any]:
        return {}

    async def generate_response(self, prompt, model, system_prompt=None, temperature=0.0, api_key=None, json_output=False):
        self.calls += 1
        return self.handler(prompt)

    async def stream_response(self, *a, **k):  # pragma: no cover - unused
        raise NotImplementedError
        yield ""


def _numbered(prompt: str, marker: str) -> int:
    section = prompt.split(marker)[1].split("Respond in JSON")[0]
    return len(re.findall(r"^\d+\. ", section, flags=re.M))


def _happy(prompt: str) -> str:
    if "Statements:" in prompt:
        return json.dumps({"judgements": [{"statement": "s", "verdict": "yes"}] * _numbered(prompt, "Statements:")})
    if "Reference statements:" in prompt:
        return json.dumps({"statements": [{"statement": "s", "attributed": "yes"}] * _numbered(prompt, "Reference statements:")})
    if "Retrieved contexts" in prompt:
        return json.dumps({"verdicts": [{"index": 1, "useful": "yes"}, {"index": 2, "useful": "no"}]})
    return json.dumps({"score": 0.9, "reason": "ok"})


SAMPLE = {
    "question": "Who leads firmware?",
    "answer": "Marcus Holloway leads firmware [d:1:0]. He is based in Baltimore.",
    "contexts": ["Marcus Holloway is lead firmware engineer.", "Unrelated chunk."],
    "ground_truth": "Marcus Holloway is the Lead Firmware Engineer.",
}


def judge(handler=_happy) -> RagasJudge:
    return RagasJudge(FakeJudgeClient(handler), "google/gemini-2.0-flash-001", "k")


async def test_happy_path_scores_all_four_metrics():
    row = await evaluate_sample(judge(), SAMPLE)
    assert row["status"] == "evaluated"
    assert row["faithfulness"] == 1.0
    assert row["answer_relevance"] == 0.9
    assert row["context_recall"] == 1.0
    assert row["context_precision"] == 1.0  # useful context ranked first


async def test_judge_exception_is_an_error_not_a_score():
    def boom(prompt: str) -> str:
        raise RuntimeError("upstream 500")

    row = await evaluate_sample(judge(boom), SAMPLE)
    assert row["status"] == "error"
    assert all(row[m] is None for m in ("faithfulness", "answer_relevance", "context_recall", "context_precision"))


async def test_empty_or_malformed_verdicts_are_errors_not_perfect_scores():
    for reply in ('{"judgements": []}', "not json", '{"score": "high"}', "[]"):
        row = await evaluate_sample(judge(lambda p, r=reply: r), SAMPLE)
        assert row["status"] == "error", reply
        assert row["faithfulness"] is None


async def test_dropped_statements_cannot_inflate_faithfulness():
    # answer has 2 claims; judge returns a single "yes" -> 0.5, not 1.0
    reply = json.dumps({"judgements": [{"statement": "s", "verdict": "yes"}]})
    score = await judge(lambda p: reply).faithfulness(SAMPLE["answer"], SAMPLE["contexts"])
    assert score == 0.5


async def test_refusals_and_abstentions_are_skipped_and_excluded_from_means():
    client = FakeJudgeClient(_happy)
    j = RagasJudge(client, "google/gemini-2.0-flash-001", "k")
    blocked = {**SAMPLE, "contexts": [], "blocked": True}
    abstained = {**SAMPLE, "abstained": True}
    rows = [await evaluate_sample(j, s) for s in (SAMPLE, blocked, abstained)]
    assert [r["status"] for r in rows] == ["evaluated", "skipped", "skipped"]
    summary = summarize(rows)
    assert (summary["evaluated"], summary["skipped"], summary["errors"]) == (1, 2, 0)
    assert summary["mean_faithfulness"] == 1.0
    assert client.calls == 4  # only the evaluated sample reached the judge


def test_summary_mean_is_none_when_nothing_was_evaluated():
    assert summarize([])["mean_faithfulness"] is None


def test_dilution_format_is_split_into_scale_groups():
    raw = [
        {"scale": 1, "writer_model": "w", "dataset": [SAMPLE]},
        {"scale": 5, "writer_model": "w", "dataset": [SAMPLE, SAMPLE]},
    ]
    groups = load_groups(raw)
    assert [(g["scale"], len(g["dataset"])) for g in groups] == [(1, 1), (5, 2)]
    # flat list and single dict still load
    assert len(load_groups([SAMPLE])[0]["dataset"]) == 1
    assert load_groups({"scale": 3, "dataset": [SAMPLE]})[0]["scale"] == 3


def test_malformed_samples_are_rejected_loudly():
    with pytest.raises(ValueError, match="missing required key 'answer'"):
        validate_sample({"question": "q", "contexts": [], "ground_truth": "g"}, 1)
    with pytest.raises(ValueError, match="empty question"):
        validate_sample({**SAMPLE, "question": " "}, 1)
    with pytest.raises(ValueError):
        load_groups([{"scale": 1, "dataset": "nope"}])


def test_independence_problems_by_family():
    assert independence_problems("openai/gpt-4o", {"pipeline judge": "openai/gpt-4o-mini"})
    assert not independence_problems("google/gemini-2.0-flash-001", {"writer": "deepseek/deepseek-v4-flash", "x": None})


async def test_run_refuses_a_judge_that_shares_a_family_with_the_writer(tmp_path):
    settings = make_settings()
    same_family = RagasJudge(FakeJudgeClient(_happy), "deepseek/deepseek-chat", "k")
    with pytest.raises(ValueError, match="independence"):
        await run_ragas_evaluation([SAMPLE], same_family, settings)


async def test_end_to_end_report_counts_and_file(tmp_path):
    settings = make_settings()
    out = tmp_path / "card.json"
    raw = [{"scale": 1, "dataset": [SAMPLE, {**SAMPLE, "blocked": True}]}]
    report = await run_ragas_evaluation(raw, judge(), settings, out)
    rep = report["reports"][0]
    assert (rep["evaluated"], rep["skipped"], rep["errors"]) == (1, 1, 0)
    assert json.loads(out.read_text(encoding="utf-8"))["judge_model"] == "google/gemini-2.0-flash-001"


def test_judge_error_type_is_runtime_error():
    assert issubclass(JudgeError, RuntimeError)
