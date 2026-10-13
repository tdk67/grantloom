"""Integration test for Milestone M5 Task 2: Admin Synthetic Data Generation Endpoint.

Verifies:
1. Endpoint test confirming POST /api/v1/admin/generate-synthetic-data indexes all 3 documents to active state.
"""

from __future__ import annotations

from pathlib import Path
import httpx
import pytest

from app.adapters.chroma_adapter import ChromaVectorStoreAdapter
from app.adapters.embedder_adapter import DeterministicEmbedder
from app.adapters.sqlite_registry import SqliteRegistryAdapter
from app.api.deps import (
    get_embedder,
    get_ingest_service,
    get_ingest_worker,
    get_registry,
    get_vector_store,
    reset_dependencies,
)
from app.core.config import get_settings
from app.db import migrator
from app.main import create_app
from app.services.ingest_service import IngestionService
from app.workers.ingest_worker import IngestWorker


@pytest.mark.asyncio
async def test_admin_generate_synthetic_data_endpoint(tmp_path: Path):
    """Confirm POST /api/v1/admin/generate-synthetic-data indexes all 3 demo documents to active state."""
    reset_dependencies()
    db_path = tmp_path / "admin_test.db"
    chroma_dir = tmp_path / "admin_chroma"

    registry = SqliteRegistryAdapter(str(db_path))
    migrator.upgrade("head", db_url=f"sqlite:///{db_path}")

    vector_store = ChromaVectorStoreAdapter(str(chroma_dir))
    embedder = DeterministicEmbedder()
    worker = IngestWorker(registry=registry, vector_store=vector_store, embedder=embedder)
    ingest_service = IngestionService(registry=registry)

    app = create_app()
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_vector_store] = lambda: vector_store
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_ingest_worker] = lambda: worker
    app.dependency_overrides[get_ingest_service] = lambda: ingest_service

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/admin/generate-synthetic-data")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["generated_count"] >= 3

        # Verify all documents are active in registry
        docs = data["documents"]
        assert len(docs) >= 3
        for d in docs:
            assert d["state"] == "active"
            doc_rec = registry.get_document(d["doc_id"])
            assert doc_rec is not None
            assert doc_rec.state == "active"
            assert doc_rec.page_count >= 1

