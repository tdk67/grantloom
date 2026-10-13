"""Abstract Document Registry and Outbox Port interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional, Sequence

from app.ports.models import DocumentChunk, DocumentRecord, KeywordSearchResult, OutboxJob


class RegistryPort(ABC):
    """Abstract port for metadata registry, idempotency hashes, and outbox state machine."""

    @abstractmethod
    def save_document(self, record: DocumentRecord) -> None:
        """Register a new document record."""
        ...

    @abstractmethod
    def get_document(self, doc_id: str) -> Optional[DocumentRecord]:
        """Fetch document record by unique document ID."""
        ...

    @abstractmethod
    def get_document_by_hash(self, content_hash: str) -> Optional[DocumentRecord]:
        """Fetch document record by canonical SHA-256 content hash."""
        ...

    @abstractmethod
    def list_documents(self, state: Optional[str] = None) -> list[DocumentRecord]:
        """List all registered documents, optionally filtered by state."""
        ...


    @abstractmethod
    def update_document_state(
        self,
        doc_id: str,
        state: str,
        metadata_update: Optional[dict[str, Any]] = None,
        error_message: Optional[str] = None,
        page_count: Optional[int] = None,
    ) -> None:
        """Transition document state machine (e.g. uploaded -> extracting -> active)."""
        ...

    @abstractmethod
    def delete_document(self, doc_id: str) -> None:
        """Remove document record from the registry."""
        ...

    @abstractmethod
    def count_documents(self) -> int:
        """Return total active/registered document count."""
        ...

    @abstractmethod
    def create_outbox_job(self, doc_id: str, stage: str = "extracting") -> str:
        """Enqueue a background outbox processing job."""
        ...

    @abstractmethod
    def claim_next_job(self) -> Optional[OutboxJob]:
        """Claim the next pending outbox job atomically."""
        ...

    @abstractmethod
    def update_job_status(
        self,
        job_id: str,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        """Update outbox job status and error if any."""
        ...

    @abstractmethod
    def complete_job(self, job_id: str) -> None:
        """Mark outbox job completed and remove from pending queue."""
        ...

    @abstractmethod
    def save_chunks(self, chunks: Sequence[DocumentChunk]) -> None:
        """Batch persist extracted document chunks with coordinates."""
        ...

    @abstractmethod
    def get_chunks_for_document(self, doc_id: str) -> list[DocumentChunk]:
        """Fetch all chunks belonging to a document."""
        ...

    @abstractmethod
    def get_chunk(self, chunk_id: str) -> Optional[DocumentChunk]:
        """Fetch single chunk by chunk ID."""
        ...

    @abstractmethod
    def search_bm25(self, query: str, top_k: int = 10) -> list[KeywordSearchResult]:
        """Perform full-text BM25 search over indexed chunks."""
        ...

