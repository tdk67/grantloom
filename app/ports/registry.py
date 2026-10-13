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
    def search_bm25(self, query: str, top_k: int) -> list[KeywordSearchResult]:
        """Perform full-text BM25 search over chunks of active documents. Raises StorageError on failure."""
        ...

    @abstractmethod
    def delete_chunks_for_document(self, doc_id: str) -> None:
        """Remove a document's chunks and FTS rows (stage-idempotent re-ingestion, NFR-5)."""
        ...

    @abstractmethod
    def get_latest_document_by_filename(self, filename: str) -> Optional[DocumentRecord]:
        """Return the highest-version document registered under this filename (FR-I2 versioning)."""
        ...

    @abstractmethod
    def update_job_stage(self, job_id: str, stage: str) -> None:
        """Record the pipeline stage an in-progress job has reached."""
        ...

    @abstractmethod
    def retry_or_fail_job(self, job_id: str, error: str, max_attempts: int) -> bool:
        """Return the job to 'pending' if attempts remain (True), else mark it 'failed' (False)."""
        ...

    @abstractmethod
    def requeue_stale_jobs(self, older_than_seconds: Optional[int]) -> int:
        """Reclaim 'in_progress' jobs (all of them when older_than_seconds is None). Returns count."""
        ...

    @abstractmethod
    def list_documents_older_than(self, state: str, days: int) -> list[DocumentRecord]:
        """Documents in the given state whose last update is older than N days."""
        ...

    @abstractmethod
    def count_chunks(self) -> int:
        """Total chunks registered across all documents."""
        ...

    @abstractmethod
    def log_audit_event(
        self,
        event_type: str,
        doc_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """Persist an audit log entry."""
        ...

    @abstractmethod
    def ping(self) -> None:
        """Raise StorageError if the registry is not reachable (readiness probe)."""
        ...


    @abstractmethod
    def replace_requirements(self, solicitation_id: str, requirements: Sequence[dict[str, Any]]) -> None:
        """Persist the evaluated requirement records of a solicitation (FR-C1), replacing earlier ones."""
        ...

    @abstractmethod
    def list_requirements(self, solicitation_id: str) -> list[dict[str, Any]]:
        """Requirement records of a solicitation in extraction order."""
        ...
