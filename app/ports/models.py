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
    file_path: str = ""
    error_message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentChunk:
    chunk_id: str
    doc_id: str
    chunk_index: int
    page_number: int = 1
    sheet_name: Optional[str] = None
    row_start: Optional[int] = None
    row_end: Optional[int] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    char_count: int = 0
    token_count: int = 0
    text_content: str = ""
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutboxJob:
    job_id: str
    doc_id: str
    stage: str  # extracting, chunking, embedding, done, failed
    status: str  # pending, in_progress, completed, failed
    attempts: int = 0
    last_error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class GateVerdict:
    gate_id: str  # G1, G2, G3, G4
    passed: bool
    confidence: float
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class KeywordSearchResult:
    chunk_id: str
    doc_id: str
    score: float
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CitationCoordinate:
    chunk_id: str
    doc_id: str
    filename: str
    page: Optional[int] = None
    line: Optional[str] = None
    sheet: Optional[str] = None
    row: Optional[str] = None
    excerpt: str = ""
    verified: bool = True


@dataclass
class AskResponse:
    answer: str
    citations: list[CitationCoordinate] = field(default_factory=list)
    confidence_score: float = 0.0
    abstention: bool = False
    strategy: str = "hybrid"

