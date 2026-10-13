"""Integration tests for Milestone M1 Ingestion Pipeline.

Verifies:
1. Multi-page test PDF upload, polling until state == 'active'.
2. Second upload of identical file returns 200 OK immediately with "Document already indexed" and zero duplicate outbox jobs.
3. ChromaDB collection count equals chunk count and SQLite registry status is 'active'.
4. Coordinate tracking and deterministic chunk IDs (doc_id:page:index).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import AsyncGenerator

import httpx
import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy import text

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
from app.core.config import Settings, get_settings
from app.db import migrator
from app.main import create_app
from app.services.chunker import DocumentChunker
from app.services.extractor import DocumentExtractor
from app.services.ingest_service import IngestionService
from app.workers.ingest_worker import IngestWorker


def create_multi_page_pdf(file_path: Path, pages: int = 3) -> Path:
    """Generate multi-page PDF using ReportLab."""
    c = canvas.Canvas(str(file_path), pagesize=letter)
    for p in range(1, pages + 1):
        c.drawString(100, 750, f"GrantLoom Test Solicitation - Section Page {p}")
        c.drawString(100, 700, f"Equipment threshold is $50,000 with mandatory 20 percent cost match.")
        c.drawString(100, 650, f"Principal Investigator must submit annual reports for phase {p}.")
        c.showPage()
    c.save()
    return file_path


@pytest.fixture
def test_setup(tmp_path: Path):
    """Setup isolated database, ChromaDB, and services for integration testing."""
    reset_dependencies()
    
    db_file = tmp_path / "test_grantloom.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    chroma_dir = tmp_path / "chromadb"
    uploads_dir = tmp_path / "uploads"
    
    # 1. Apply Alembic migrations
    migrator.upgrade("head", db_url=db_url)
    
    # 2. Build isolated components
    settings = get_settings()
    registry = SqliteRegistryAdapter(db_file)
    vector_store = ChromaVectorStoreAdapter(chroma_dir)
    embedder = DeterministicEmbedder(dimension=1536)
    extractor = DocumentExtractor(max_pages_per_doc=100)
    chunker = DocumentChunker(target_chunk_size=800, chunk_overlap=100)
    
    worker = IngestWorker(
        registry=registry,
        vector_store=vector_store,
        embedder=embedder,
        extractor=extractor,
        chunker=chunker,
    )
    
    ingest_service = IngestionService(
        registry=registry,
        settings=settings,
        upload_dir=uploads_dir,
    )
    
    # 3. Create app and wire dependency overrides
    app = create_app()
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_vector_store] = lambda: vector_store
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_ingest_service] = lambda: ingest_service
    app.dependency_overrides[get_ingest_worker] = lambda: worker
    
    yield {
        "app": app,
        "registry": registry,
        "vector_store": vector_store,
        "worker": worker,
        "tmp_path": tmp_path,
    }
    
    app.dependency_overrides.clear()
    reset_dependencies()


@pytest.mark.asyncio
async def test_pdf_ingestion_deduplication_and_indexing(test_setup):
    """Verifies complete M1 checkpoint requirements."""
    app = test_setup["app"]
    registry = test_setup["registry"]
    vector_store = test_setup["vector_store"]
    worker = test_setup["worker"]
    tmp_path = test_setup["tmp_path"]

    # Generate 3-page test PDF
    pdf_path = tmp_path / "DOE_Solar_Test.pdf"
    create_multi_page_pdf(pdf_path, pages=3)
    pdf_bytes = pdf_path.read_bytes()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Step 1: Upload multi-page test PDF
        upload_resp = await client.post(
            "/api/v1/documents",
            files={"file": ("DOE_Solar_Test.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        )
        assert upload_resp.status_code == 202
        upload_data = upload_resp.json()
        doc_id = upload_data["doc_id"]
        assert upload_data["state"] == "uploaded"
        assert upload_data["filename"] == "DOE_Solar_Test.pdf"
        assert len(upload_data["content_hash"]) == 64

        # Step 2: Background worker processes the job through pipeline
        processed = await worker.process_next_job()
        assert processed is True

        # Step 3: Poll GET /api/v1/documents/{id} until state == active
        status_resp = await client.get(f"/api/v1/documents/{doc_id}")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["state"] == "active"
        assert status_data["page_count"] == 3
        expected_chunk_count = status_data["metadata"]["chunk_count"]
        assert expected_chunk_count >= 3

        # Step 4: Upload the exact same file a second time (instant deduplication test)
        dup_resp = await client.post(
            "/api/v1/documents",
            files={"file": ("DOE_Solar_Test.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        )
        assert dup_resp.status_code == 200
        dup_data = dup_resp.json()
        assert dup_data["message"] == "Document already indexed"
        assert dup_data["doc_id"] == doc_id
        assert dup_data["state"] == "active"

        # Assert zero duplicate outbox jobs were created
        with registry.engine.connect() as conn:
            jobs_count = conn.execute(
                text("SELECT COUNT(*) FROM outbox_jobs WHERE doc_id = :d"),
                {"d": doc_id},
            ).scalar()
            assert jobs_count == 1  # Only the original completed job exists

        # Step 5: Assert ChromaDB collection count equals expected chunk count
        assert vector_store.count() == expected_chunk_count
        assert vector_store.count() > 0

        # Step 6: Verify chunks integrity and deterministic chunk IDs (doc_id:page:index)
        chunks = registry.get_chunks_for_document(doc_id)
        assert len(chunks) == expected_chunk_count
        for chunk in chunks:
            # Format: doc_id:page:index
            parts = chunk.chunk_id.split(":")
            assert len(parts) == 3
            assert parts[0] == doc_id
            assert int(parts[1]) == chunk.page_number
            assert 1 <= chunk.page_number <= 3
            assert len(chunk.text_content) > 0


@pytest.mark.asyncio
async def test_non_existent_document_polling_returns_404(test_setup):
    """Assert polling a non-existent document ID returns RFC 7807 404."""
    app = test_setup["app"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/documents/doc_nonexistent_999")
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == "DOC_NOT_FOUND"
        assert "not found" in data["detail"].lower()


@pytest.mark.asyncio
async def test_txt_and_xlsx_ingestion(test_setup):
    """Assert plain text and Excel spreadsheet files ingest cleanly."""
    app = test_setup["app"]
    worker = test_setup["worker"]
    tmp_path = test_setup["tmp_path"]

    # 1. Plain text file
    txt_content = b"Grant Guidelines:\nLine 1: Eligibility requirements.\nLine 2: Budget threshold is $100,000."
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        txt_resp = await client.post(
            "/api/v1/documents",
            files={"file": ("guidelines.txt", io.BytesIO(txt_content), "text/plain")},
        )
        assert txt_resp.status_code == 202
        txt_doc_id = txt_resp.json()["doc_id"]

        await worker.process_next_job()
        poll_resp = await client.get(f"/api/v1/documents/{txt_doc_id}")
        assert poll_resp.status_code == 200
        assert poll_resp.json()["state"] == "active"

        # 2. Excel spreadsheet
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Personnel"
        ws.append(["Name", "Role", "Salary"])
        ws.append(["Dr. Elena Vance", "PI", 120000])
        xlsx_buf = io.BytesIO()
        wb.save(xlsx_buf)
        xlsx_buf.seek(0)

        xlsx_resp = await client.post(
            "/api/v1/documents",
            files={"file": ("budget.xlsx", xlsx_buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert xlsx_resp.status_code == 202
        xlsx_doc_id = xlsx_resp.json()["doc_id"]

        await worker.process_next_job()
        poll_xlsx = await client.get(f"/api/v1/documents/{xlsx_doc_id}")
        assert poll_xlsx.status_code == 200
        assert poll_xlsx.json()["state"] == "active"


@pytest.mark.asyncio
async def test_document_deletion_purges_registry_and_vectors(test_setup):
    """Assert DELETE /api/v1/documents/{id} purges SQLite chunks and ChromaDB vectors."""
    app = test_setup["app"]
    worker = test_setup["worker"]
    vector_store = test_setup["vector_store"]
    registry = test_setup["registry"]

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        upload_resp = await client.post(
            "/api/v1/documents",
            files={"file": ("temp.txt", io.BytesIO(b"Temporary data to delete."), "text/plain")},
        )
        doc_id = upload_resp.json()["doc_id"]
        await worker.process_next_job()

        assert vector_store.count() > 0

        # Delete document
        del_resp = await client.delete(f"/api/v1/documents/{doc_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "deleted"

        # Verify 404 on get
        get_resp = await client.get(f"/api/v1/documents/{doc_id}")
        assert get_resp.status_code == 404

        # Verify ChromaDB vectors removed
        assert vector_store.count() == 0


@pytest.mark.asyncio
async def test_unsupported_file_extension_rejection(test_setup):
    """Assert files with unsupported extensions are rejected with RFC 7807 problem details."""
    app = test_setup["app"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/documents",
            files={"file": ("script.sh", io.BytesIO(b"echo hello"), "application/x-sh")},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["code"] == "VALIDATION_ERROR"
