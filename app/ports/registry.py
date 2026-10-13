"""Abstract Document Registry and Outbox Port interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional, Sequence

from app.ports.models import DocumentRecord


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
    def list_documents(self) -> list[DocumentRecord]:
        """List all registered documents."""
        ...

    @abstractmethod
    def update_document_state(
        self,
        doc_id: str,
        state: str,
        metadata_update: Optional[dict[str, Any]] = None,
    ) -> None:
        """Transition document state machine (e.g. uploaded -> extracting -> active)."""
        ...

    @abstractmethod
    def delete_document(self, doc_id: str) -> None:
        """Remove document record from the registry."""
        ...
