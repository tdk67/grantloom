"""API routes for document upload, polling, and lifecycle management."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from fastapi.responses import JSONResponse

from app.api.deps import (
    get_graph_store,
    get_ingest_service,
    get_registry,
    get_structured_store,
    get_vector_store,
    verify_master_api_key,
)
from app.core.errors import DocumentNotFoundError
from app.ports.graph_store import GraphStorePort
from app.ports.registry import RegistryPort
from app.ports.structured_store import StructuredStorePort
from app.ports.vector_store import VectorStorePort
from app.services.ingest_service import IngestionService

logger = logging.getLogger("grantloom.api.documents")

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


@router.post("", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(verify_master_api_key)])
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


@router.delete("/{doc_id}", dependencies=[Depends(verify_master_api_key)])
async def delete_document(
    doc_id: str,
    registry: RegistryPort = Depends(get_registry),
    vector_store: VectorStorePort = Depends(get_vector_store),
    structured_store: StructuredStorePort = Depends(get_structured_store),
    graph_store: GraphStorePort = Depends(get_graph_store),
) -> dict[str, str]:
    """Delete document and purge from vector store, registry, tabular store, graph store, and disk."""
    doc = registry.get_document(doc_id)
    if not doc:
        raise DocumentNotFoundError(f"Document with ID '{doc_id}' not found.")

    # 1. Purge vectors from Chroma
    vector_store.delete_document(doc_id)

    # 2. Purge tabular tables from SQLite
    try:
        structured_store.delete_document_tables(doc_id)
    except Exception as t_err:
        logger.warning("Tabular tables purge notice for doc '%s': %s", doc_id, t_err)

    # 3. Purge graph nodes/edges from KùzuDB
    try:
        graph_store.delete_document(doc_id)
    except Exception as g_err:
        logger.warning("Graph nodes purge notice for doc '%s': %s", doc_id, g_err)

    # 4. Purge from document registry, chunks, and outbox
    registry.delete_document(doc_id)

    # 5. Purge physical file from disk
    if doc.file_path and Path(doc.file_path).exists():
        try:
            Path(doc.file_path).unlink(missing_ok=True)
            logger.info("Purged physical file '%s' for doc '%s'.", doc.file_path, doc_id)
        except Exception as f_err:
            logger.warning("Could not unlink physical file '%s': %s", doc.file_path, f_err)

    registry.log_audit_event("document_deleted", doc_id=doc_id, details={"filename": doc.filename})

    return {"status": "deleted", "doc_id": doc_id}
