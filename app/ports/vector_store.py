"""Abstract Vector Store Port interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from app.ports.models import ChunkVector, VectorSearchResult


class VectorStorePort(ABC):
    """Abstract port for dense vector persistence and similarity retrieval."""

    @abstractmethod
    def add_chunks(self, chunks: Sequence[ChunkVector]) -> None:
        """Persist chunk vectors and metadata into the vector index."""
        ...

    @abstractmethod
    def query(self, vector: Sequence[float], top_k: int) -> list[VectorSearchResult]:
        """Query top-k nearest neighbors by cosine similarity."""
        ...

    @abstractmethod
    def delete_document(self, doc_id: str) -> None:
        """Purge all chunk vectors associated with a document ID."""
        ...

    @abstractmethod
    def count(self) -> int:
        """Return total indexed vectors."""
        ...
