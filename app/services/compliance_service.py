"""Compliance matrix (FR-C1..C4): requirement extraction, evidence assessment, and Excel export.

1. Requirements are the solicitation's own requirement sentences (must / shall / required / not to
   exceed ...), de-duplicated, categorized via the lexicon, and persisted with their source chunk IDs.
2. Lab evidence for each requirement is retrieved from the other active documents only (BM25 + dense).
3. The decision backend classifies Satisfied / Partial / Gap and names the evidence chunk it relied on.
The readiness score is an estimate, never a funding guarantee (FR-C3).
"""

from __future__ import annotations

import io
import logging
import re
from typing import Any, Optional

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.core.config import Settings
from app.core.errors import NotFoundError, ValidationError
from app.core.templates import load_json_template
from app.core.text import lexicon_hits, split_sentences
from app.ports.decision_backend import DecisionBackendPort
from app.ports.embedder import EmbedderPort
from app.ports.models import DocumentChunk
from app.ports.registry import RegistryPort
from app.ports.vector_store import VectorStorePort

logger = logging.getLogger("grantloom.services.compliance")

MANDATORY_DISCLAIMER = "Readiness Estimate — Not an official funding guarantee"
MAX_REQUIREMENT_CHARS = 400
NUMBER_TOKEN_RE = re.compile(r"\b\d+(?:\.\d+)*\b")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
WHITESPACE_RE = re.compile(r"\s+")


class ComplianceService:
    """Evaluates solicitation compliance against lab documents and generates Excel exports."""

    def __init__(
        self,
        registry: RegistryPort,
        vector_store: VectorStorePort,
        embedder: EmbedderPort,
        decision_backend: DecisionBackendPort,
        settings: Settings,
    ) -> None:
        self.registry = registry
        self.vector_store = vector_store
        self.embedder = embedder
        self.decision_backend = decision_backend
        self.max_requirements = settings.compliance.max_requirements
        self.evidence_top_k = settings.compliance.evidence_top_k
        self.evidence_candidates = settings.compliance.evidence_candidates
        self.rrf_k = settings.retrieval.rrf_k
        lexicon = load_json_template("decision/heuristic_lexicon.json")
        self.markers: list[str] = lexicon["requirement_markers"]
        self.categories: dict[str, list[str]] = lexicon["requirement_categories"]

    def _category(self, sentence: str) -> str:
        lowered = sentence.lower()
        for category, keywords in self.categories.items():
            if any(k in lowered for k in keywords):
                return category
        return "General"

    def extract_requirements(self, chunks: list[DocumentChunk]) -> list[dict[str, Any]]:
        """Requirement sentences of the solicitation, each linked to the chunk it came from."""
        seen: set[str] = set()
        requirements: list[dict[str, Any]] = []
        for chunk in chunks:
            for sentence in split_sentences(chunk.text_content):
                sentence = WHITESPACE_RE.sub(" ", sentence).strip(" -•")
                if len(sentence) > MAX_REQUIREMENT_CHARS or not lexicon_hits(sentence, self.markers):
                    continue
                # Numbered boilerplate repeated on many pages ("checkpoint 7.2", "checkpoint 8.2") counts once
                key = NUMBER_TOKEN_RE.sub("#", NON_ALNUM_RE.sub(" ", sentence.lower()).strip())
                if key in seen:
                    continue
                seen.add(key)
                requirements.append({"text": sentence, "chunk": chunk, "category": self._category(sentence)})
                if len(requirements) >= self.max_requirements:
                    return requirements
        return requirements

    async def _evidence(self, requirement: str, lab_doc_ids: set[str]) -> list[DocumentChunk]:
        """Top lab chunks for a requirement by reciprocal-rank fusion of BM25 and dense results."""
        scores: dict[str, float] = {}
        bm25 = [h for h in self.registry.search_bm25(requirement, self.evidence_candidates) if h.doc_id in lab_doc_ids]
        for rank, hit in enumerate(bm25, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (self.rrf_k + rank)
        vector = await self.embedder.embed_text(requirement)
        dense = [h for h in self.vector_store.query(vector, self.evidence_candidates) if h.doc_id in lab_doc_ids]
        for rank, hit in enumerate(dense, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (self.rrf_k + rank)
        best = sorted(scores, key=lambda cid: scores[cid], reverse=True)[: self.evidence_top_k]
        chunks = [self.registry.get_chunk(cid) for cid in best]
        return [c for c in chunks if c is not None and not c.metadata.get("tainted")]

    async def evaluate_solicitation(self, solicitation_id: str, api_key: Optional[str] = None) -> dict[str, Any]:
        """Extract requirements, assess lab evidence, persist the records, and return the matrix."""
        solicitation = self.registry.get_document(solicitation_id)
        if not solicitation:
            raise NotFoundError(f"Solicitation document '{solicitation_id}' not found.")
        if solicitation.state != "active":
            raise ValidationError(f"Solicitation '{solicitation_id}' is '{solicitation.state}', not active.")

        lab_doc_ids = {d.doc_id for d in self.registry.list_documents(state="active") if d.doc_id != solicitation_id}
        extracted = self.extract_requirements(self.registry.get_chunks_for_document(solicitation_id))

        records: list[dict[str, Any]] = []
        for idx, req in enumerate(extracted, start=1):
            evidence = await self._evidence(req["text"], lab_doc_ids) if lab_doc_ids else []
            assessment = await self.decision_backend.assess_requirement(req["text"], evidence, api_key)
            records.append({
                "req_id": f"{solicitation_id}:REQ-{idx:02d}",
                "solicitation_id": solicitation_id,
                "source_chunk_id": req["chunk"].chunk_id,
                "category": req["category"],
                "requirement_text": req["text"],
                "status": assessment.status,
                "evidence_chunk_id": assessment.evidence_chunk_id,
                "rationale": assessment.rationale,
                "decision_backend": assessment.backend,
            })
        self.registry.replace_requirements(solicitation_id, records)
        logger.info("Evaluated %d requirements for solicitation '%s'.", len(records), solicitation_id)
        return self.build_report(solicitation_id)

    def build_report(self, solicitation_id: str) -> dict[str, Any]:
        """Matrix view over the persisted requirement records."""
        solicitation = self.registry.get_document(solicitation_id)
        if not solicitation:
            raise NotFoundError(f"Solicitation document '{solicitation_id}' not found.")
        records = self.registry.list_requirements(solicitation_id)
        if not records:
            raise NotFoundError(
                f"No compliance evaluation stored for '{solicitation_id}'. Run POST /api/v1/compliance/{solicitation_id} first."
            )

        requirements = []
        for rec in records:
            source = self.registry.get_chunk(rec["source_chunk_id"])
            evidence = self.registry.get_chunk(rec["evidence_chunk_id"]) if rec["evidence_chunk_id"] else None
            evidence_doc = self.registry.get_document(evidence.doc_id) if evidence else None
            if evidence and evidence_doc:
                coord = (
                    f"Sheet '{evidence.sheet_name}', Rows {evidence.row_start}-{evidence.row_end}"
                    if evidence.sheet_name else f"Page {evidence.page_number}"
                )
                citation = f"{evidence_doc.filename} ({coord}) [{evidence.chunk_id}]"
                evidence_text = evidence.text_content[:250].strip()
            else:
                citation = "None (Action Required)"
                evidence_text = "No supporting lab evidence found."
            requirements.append({
                "req_id": rec["req_id"].rsplit(":", 1)[-1],
                "category": rec["category"],
                "title": rec["category"],
                "requirement_text": rec["requirement_text"],
                "solicitation_source": f"Page {source.page_number} [{source.chunk_id}]" if source else rec["source_chunk_id"],
                "source_chunk_id": rec["source_chunk_id"],
                "status": rec["status"],
                "evidence": evidence_text,
                "evidence_chunk_id": rec["evidence_chunk_id"],
                "rationale": rec["rationale"],
                "citation": citation,
                "decision_backend": rec["decision_backend"],
            })

        satisfied = sum(1 for r in requirements if r["status"] == "Satisfied")
        partial = sum(1 for r in requirements if r["status"] == "Partial")
        gap = sum(1 for r in requirements if r["status"] == "Gap")
        total = len(requirements)
        return {
            "solicitation_id": solicitation_id,
            "solicitation_filename": solicitation.filename,
            "total_requirements": total,
            "satisfied_count": satisfied,
            "partial_count": partial,
            "gap_count": gap,
            "readiness_score_pct": round((satisfied + 0.5 * partial) / max(1, total) * 100, 1),
            "disclaimer": MANDATORY_DISCLAIMER,
            "requirements": requirements,
        }

    def export_excel(self, matrix_report: dict[str, Any]) -> bytes:
        """Generate professionally styled compliance matrix Excel workbook."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Compliance Matrix"

        header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        regular_font = Font(name="Calibri", size=10)
        thin_border = Border(
            left=Side(style="thin", color="D1D5DB"),
            right=Side(style="thin", color="D1D5DB"),
            top=Side(style="thin", color="D1D5DB"),
            bottom=Side(style="thin", color="D1D5DB"),
        )
        status_fills = {
            "Satisfied": PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid"),
            "Partial": PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid"),
            "Gap": PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),
        }
        status_colors = {
            "Satisfied": Font(name="Calibri", size=10, bold=True, color="065F46"),
            "Partial": Font(name="Calibri", size=10, bold=True, color="92400E"),
            "Gap": Font(name="Calibri", size=10, bold=True, color="991B1B"),
        }

        headers = [
            "Req ID",
            "Category",
            "Requirement (Solicitation Source)",
            "Compliance Status",
            "Lab Evidence & Rationale",
            "Verification Citation",
        ]
        ws.append(headers)
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[1].height = 28

        requirements = matrix_report.get("requirements", [])
        for r_idx, req in enumerate(requirements, start=2):
            ws.append([
                req["req_id"],
                req["category"],
                f"{req['requirement_text']} ({req['solicitation_source']})",
                req["status"],
                f"{req['evidence']}\nRationale: {req['rationale']}",
                req["citation"],
            ])
            ws.row_dimensions[r_idx].height = 48
            for col_idx in range(1, len(headers) + 1):
                c = ws.cell(row=r_idx, column=col_idx)
                c.border = thin_border
                c.font = regular_font
                c.alignment = Alignment(vertical="center", wrap_text=True)
            status_cell = ws.cell(row=r_idx, column=4)
            if req["status"] in status_fills:
                status_cell.fill = status_fills[req["status"]]
                status_cell.font = status_colors[req["status"]]
            status_cell.alignment = Alignment(horizontal="center", vertical="center")

        for idx, width in enumerate([10, 22, 50, 18, 50, 36], start=1):
            ws.column_dimensions[get_column_letter(idx)].width = width

        footer_row = len(requirements) + 3
        readiness = matrix_report.get("readiness_score_pct", 0.0)
        ws.cell(row=footer_row, column=1, value=f"Readiness Score: {readiness}% | Disclaimer: {MANDATORY_DISCLAIMER}")
        ws.merge_cells(start_row=footer_row, start_column=1, end_row=footer_row, end_column=len(headers))
        f_cell = ws.cell(row=footer_row, column=1)
        f_cell.font = Font(name="Calibri", size=10, italic=True, bold=True, color="4B5563")
        f_cell.alignment = Alignment(horizontal="center", vertical="center")

        out_buffer = io.BytesIO()
        wb.save(out_buffer)
        return out_buffer.getvalue()
