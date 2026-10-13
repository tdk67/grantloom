"""API routes for document upload, polling, and lifecycle management."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Header, Request, UploadFile, status
from fastapi.responses import JSONResponse

from app.api.deps import (
    get_inference_profile,
    get_ingest_service,
    get_key_vault,
    get_registry,
    verify_master_api_key,
)
from app.core.errors import DocumentNotFoundError
from app.core.rate_limit import limiter, upload_limit
from app.ports.models import DocumentRecord
from app.ports.registry import RegistryPort
from app.services.inference import InferenceProfile
from app.services.ingest_service import IngestionService
from app.services.key_vault import EphemeralKeyVault

logger = logging.getLogger("grantloom.api.documents")

router = APIRouter(prefix="/api/v1/documents", tags=["documents"], dependencies=[Depends(verify_master_api_key)])


def document_view(doc: DocumentRecord) -> dict[str, Any]:
    return {
        "doc_id": doc.doc_id,
        "filename": doc.filename,
        "content_hash": doc.content_hash,
        "file_type": doc.file_type,
        "page_count": doc.page_count,
        "state": doc.state,
        "stage": doc.metadata.get("stage", doc.state),
        "progress_pct": doc.metadata.get("progress_pct"),
        "version": doc.version,
        "created_at": doc.created_at,
        "error_message": doc.error_message or None,
        "metadata": doc.metadata,
    }


@router.post("", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(upload_limit)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    x_openrouter_key: Optional[str] = Header(None, alias="X-OpenRouter-Key"),
    ingest_service: IngestionService = Depends(get_ingest_service),
    profile: InferenceProfile = Depends(get_inference_profile),
    key_vault: EphemeralKeyVault = Depends(get_key_vault),
) -> JSONResponse:
    """Upload a document (PDF, TXT, CSV, XLSX). SHA-256 deduplication; same filename + new content = new version.

    In BYOK mode an optional X-OpenRouter-Key is validated and held in memory only for this
    document's Tier-2 injection scan; without it Tier 2 is recorded as skipped.
    """
    api_key = await profile.authorize(x_openrouter_key) if (x_openrouter_key and profile.requires_byok_key) else None

    result = ingest_service.process_upload(
        filename=file.filename or "unknown",
        stream=file.file,
        content_length=file.size,
    )
    if not result.is_duplicate or result.state == "uploaded":
        key_vault.put(result.doc_id, api_key)

    payload = {
        "status": "success" if result.state == "active" else "accepted",
        "doc_id": result.doc_id,
        "filename": result.filename,
        "content_hash": result.content_hash,
        "state": result.state,
        "version": result.record.version if result.record else 1,
        "message": result.message,
    }
    code = status.HTTP_200_OK if (result.is_duplicate and result.state == "active") else status.HTTP_202_ACCEPTED
    return JSONResponse(status_code=code, content=payload)


@router.get("/{doc_id}")
async def get_document_status(
    doc_id: str,
    registry: RegistryPort = Depends(get_registry),
) -> dict[str, Any]:
    """Poll document state, pipeline stage, progress percentage, and errors."""
    doc = registry.get_document(doc_id)
    if not doc:
        raise DocumentNotFoundError(f"Document with ID '{doc_id}' not found.")
    return document_view(doc)


@router.get("")
async def list_documents(registry: RegistryPort = Depends(get_registry)) -> dict[str, Any]:
    """List all registered documents with versions and states."""
    docs = registry.list_documents()
    return {"documents": [document_view(d) for d in docs], "total": len(docs)}


@router.delete("/{doc_id}", status_code=status.HTTP_202_ACCEPTED)
async def delete_document(
    doc_id: str,
    ingest_service: IngestionService = Depends(get_ingest_service),
) -> dict[str, str]:
    """FR-I6: start the deletion saga. The document leaves retrieval immediately; the worker purges
    vectors, tables, graph edges, registry rows and the stored file (retrying on failure)."""
    ingest_service.request_deletion(doc_id)
    return {"status": "deleting", "doc_id": doc_id}
