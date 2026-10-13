"""API routes for document upload, polling, and lifecycle management."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from fastapi.responses import JSONResponse

from app.api.deps import get_ingest_service, get_registry, get_vector_store
from app.core.errors import DocumentNotFoundError
from app.ports.registry import RegistryPort
from app.ports.vector_store import VectorStorePort
from app.services.ingest_service import IngestionService

logger = logging.getLogger("grantloom.api.documents")

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: UploadFile = File(...),
    ingest_service: IngestionService = Depends(get_ingest_service),
) -> Response:
    """Upload a document (PDF, TXT, XLSX). Computes SHA-256 for instant deduplication."""
    result = ingest_service.process_upload(
        filename=file.filename or "unknown",
        stream=file.file,
        content_length=file.size,
    )

    if result.is_duplicate and result.state == "active":
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "success",
                "doc_id": result.doc_id,
                "filename": result.filename,
                "content_hash": result.content_hash,
                "state": "active",
                "message": result.message,
            },
        )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "doc_id": result.doc_id,
            "filename": result.filename,
            "content_hash": result.content_hash,
            "state": result.state,
            "message": result.message,
        },
    )


@router.get("/{doc_id}")
async def get_document_status(
    doc_id: str,
    registry: RegistryPort = Depends(get_registry),
) -> dict[str, Any]:
    """Poll document status and extraction progress."""
    doc = registry.get_document(doc_id)
    if not doc:
        raise DocumentNotFoundError(f"Document with ID '{doc_id}' not found.")

    return {
        "doc_id": doc.doc_id,
        "filename": doc.filename,
        "content_hash": doc.content_hash,
        "file_type": doc.file_type,
        "page_count": doc.page_count,
        "state": doc.state,
        "version": doc.version,
        "created_at": doc.created_at,
        "error_message": doc.error_message,
        "metadata": doc.metadata,
    }


@router.get("")
async def list_documents(
    registry: RegistryPort = Depends(get_registry),
) -> dict[str, Any]:
    """List all registered documents."""
    docs = registry.list_documents()
    return {
        "documents": [
            {
                "doc_id": d.doc_id,
                "filename": d.filename,
                "content_hash": d.content_hash,
                "file_type": d.file_type,
                "page_count": d.page_count,
                "state": d.state,
                "version": d.version,
                "created_at": d.created_at,
                "error_message": d.error_message,
                "metadata": d.metadata,
            }
            for d in docs
        ],
        "total": len(docs),
    }


@router.delete("/{doc_id}")
async def delete_document(
    doc_id: str,
    registry: RegistryPort = Depends(get_registry),
    vector_store: VectorStorePort = Depends(get_vector_store),
) -> dict[str, str]:
    """Delete document and purge from vector store and registry."""
    doc = registry.get_document(doc_id)
    if not doc:
        raise DocumentNotFoundError(f"Document with ID '{doc_id}' not found.")

    vector_store.delete_document(doc_id)
    registry.delete_document(doc_id)
    registry.log_audit_event("document_deleted", doc_id=doc_id, details={"filename": doc.filename})

    return {"status": "deleted", "doc_id": doc_id}
