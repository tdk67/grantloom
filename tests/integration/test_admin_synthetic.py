"""Admin synthetic data endpoint indexes the full demo corpus."""

from __future__ import annotations

import httpx
import pytest

from tests.helpers import ScriptedLLM


@pytest.mark.asyncio
async def test_admin_generate_synthetic_data_endpoint(stack, tmp_path):
    stack.settings = stack.settings.model_copy(update={
        "database": stack.settings.database.model_copy(update={"synthetic_path": str(tmp_path / "synthetic")})
    })
    app = stack.app(ScriptedLLM())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/admin/generate-synthetic-data")
    assert resp.status_code == 200
    data = resp.json()
    assert data["generated_count"] >= 3
    for d in data["documents"]:
        assert d["state"] == "active", d
        assert stack.registry.get_document(d["doc_id"]).page_count >= 1
    # Generic extraction finds the corpus' people without any hardcoded names
    people = {r["name"] for r in stack.graph_store.query_template("list_people", {})}
    assert {"Dr. Elena Vance", "Marcus Holloway"} <= people
