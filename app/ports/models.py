"""Data transfer objects and models used by hexagonal ports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ChunkVector:
    chunk_id: str
    doc_id: str
    embedding: list[float]
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class VectorSearchResult:
    chunk_id: str
    doc_id: str
    score: float
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphNode:
    node_id: str
    label: str  # Document, Requirement, Person, Project, Funder
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    source_id: str
    target_id: str
    rel_type: str
    source_chunk_id: str  # Mandatory provenance trace
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentRecord:
    doc_id: str
    filename: str
    content_hash: str
    file_type: str
    page_count: int
    state: str  # uploaded, extracting, chunking, embedding, active, quarantined, deleting
    version: int
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateVerdict:
    gate_id: str  # G1, G2, G3, G4
    passed: bool
    confidence: float
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)
