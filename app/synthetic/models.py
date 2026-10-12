"""Data models for synthetic grant document generation and ground-truth manifests."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class PlantedFact(BaseModel):
    """A planted ground truth fact for deterministic benchmark evaluation."""

    fact_id: str = Field(..., description="Unique identifier for the planted fact")
    question: str = Field(..., description="Natural language question testing this fact")
    expected_answer: str = Field(..., description="Canonical expected answer or key proposition")
    document_name: str = Field(..., description="Filename where the fact is planted")
    location_hint: str = Field(..., description="Page, section, or sheet/row where the fact is located")
    expected_page: Optional[int] = Field(None, description="Expected 1-based page number in PDF if applicable")
    expected_sheet: Optional[str] = Field(None, description="Expected sheet name in XLSX if applicable")
    expected_cell: Optional[str] = Field(None, description="Expected cell reference in XLSX if applicable")
    keywords: List[str] = Field(default_factory=list, description="Mandatory keywords that must be present in retrieval")
    numeric_value: Optional[float] = Field(None, description="Numeric value for exact calculation checks")


class SectionContent(BaseModel):
    """Structured content for a document section."""

    title: str
    level: int = 1
    paragraphs: List[str] = Field(default_factory=list)
    bullet_points: List[str] = Field(default_factory=list)
    table_data: Optional[List[List[str]]] = None
    callout_box: Optional[str] = None
    page_break_after: bool = False


class DocumentDefinition(BaseModel):
    """Specification for generating a synthetic document."""

    doc_id: str
    filename: str
    title: str
    subtitle: Optional[str] = None
    author_or_agency: str
    date_str: str
    doc_type: str  # "solicitation_pdf", "proposal_pdf", "budget_xlsx", "compliance_xlsx"
    sections: List[SectionContent] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GroundTruthManifest(BaseModel):
    """Manifest of all synthetic documents and planted evaluation facts."""

    version: str = "1.0.0"
    generated_at: str
    documents: List[str] = Field(default_factory=list)
    planted_facts: List[PlantedFact] = Field(default_factory=list)
