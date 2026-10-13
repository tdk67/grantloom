"""Offline evaluation run: exercises the real pipeline in an isolated workspace.

This is a pipeline regression check, not a quality claim: the offline writer is an extractive stub
(faithfulness is deliberately not measured) and embeddings are lexical. Quality numbers come from
`python -m app.cli.run_eval --writer openrouter --decision llm_judge` (see README).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.cli.run_eval import DEFAULT_GOLDEN, DEFAULT_HELDOUT, run_evaluation
from app.core.config import get_settings


@pytest.mark.asyncio
async def test_offline_eval_pipeline_regression(tmp_path: Path):
    assert DEFAULT_GOLDEN.exists() and DEFAULT_HELDOUT.exists()  # tracked in git, no data/ dependency
    live_db = Path(get_settings().database.sqlite_path)
    live_mtime = live_db.stat().st_mtime if live_db.exists() else None

    report = await run_evaluation(workspace=tmp_path / "eval")
    m = report["metrics"]

    assert report["counts"] == {**report["counts"], "answerable": 38, "unanswerable": 7, "adversarial_canonical": 5, "adversarial_heldout": 15}
    assert m["faithfulness"] is None  # never reported for the extractive stub (would be circular)
    assert m["citation_resolvability"] == 1.0
    assert m["injection_block_rate_canonical"] == 1.0
    assert m["abstention_accuracy"] >= 0.85
    assert m["recall_at_k"] >= 0.85  # regression floor for lexical retrieval on the synthetic corpus
    assert report["tool_counts"]["dense_vector_search"]["ok"] > 0
    assert report["tool_counts"]["bm25_keyword_search"]["ok"] > 0
    # The live database is never touched by the evaluation
    assert (live_db.stat().st_mtime if live_db.exists() else None) == live_mtime


@pytest.mark.asyncio
async def test_openrouter_eval_requires_server_key(tmp_path: Path, monkeypatch):
    from app.cli.run_eval import build_eval_run
    from tests.helpers import make_settings

    monkeypatch.setattr("app.cli.run_eval.get_settings", lambda: make_settings())
    with pytest.raises(SystemExit, match="OPENROUTER_API_KEY"):
        await build_eval_run(tmp_path / "ws", writer="openrouter", decision="llm_judge")
