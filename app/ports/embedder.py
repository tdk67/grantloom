"""Abstract Embedder Port interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence


class EmbedderPort(ABC):
    """Abstract port for dense vector generation."""

    @abstractmethod
    async def embed_text(self, text: str) -> list[float]:
        """Generate dense vector embedding for a single text chunk."""
        ...

    @abstractmethod
    async def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Generate dense vector embeddings for a sequence of texts."""
        ...
