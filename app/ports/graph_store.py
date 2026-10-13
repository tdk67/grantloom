"""Abstract Graph Store Port interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence

from app.ports.models import GraphEdge, GraphNode


class GraphStorePort(ABC):
    """Abstract port for embedded columnar graph persistence and Cypher template queries."""

    @abstractmethod
    def add_nodes(self, nodes: Sequence[GraphNode]) -> None:
        """Add graph nodes into the columnar store."""
        ...

    @abstractmethod
    def add_edges(self, edges: Sequence[GraphEdge]) -> None:
        """Add directed edges with mandatory source chunk provenance."""
        ...

    @abstractmethod
    def query_template(self, template_id: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute a predefined parameterized Cypher template without free-form injection."""
        ...

    @abstractmethod
    def delete_document(self, doc_id: str) -> None:
        """Purge all nodes and edges belonging to a given document ID."""
        ...
