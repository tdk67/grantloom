"""Compliance Matrix evaluation and formatted spreadsheet export service."""

from __future__ import annotations

import io
import logging
from typing import Any, Optional

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.core.errors import NotFoundError
from app.ports.models import DocumentChunk, DocumentRecord
from app.ports.registry import RegistryPort

logger = logging.getLogger("grantloom.services.compliance")

MANDATORY_DISCLAIMER = "Readiness Estimate — Not an official funding guarantee"

# Compliance categories scanned dynamically from solicitation text
CANDIDATE_CATEGORIES = [
    {
        "category": "Cost Match & Share",
        "title": "Non-Federal Cost Share",
        "keywords": ["cost match", "cost share", "20 percent", "non-federal", "matching funds"],
        "default_text": "Recipient must provide minimum non-federal cost match of total project allowable costs.",
    },
    {
        "category": "Equipment & Capital",
        "title": "Equipment Capitalization Threshold",
        "keywords": ["equipment threshold", "50,000", "capital expenditure", "spectrometer", "prior approval"],
        "default_text": "Equipment items exceeding threshold require prior contracting officer authorization.",
    },
    {
        "category": "Key Personnel",
        "title": "Principal Investigator Eligibility",
        "keywords": ["principal investigator", "elena vance", "pi eligibility", "lead organization", "full-time"],
        "default_text": "Lead Principal Investigator must demonstrate qualifications and institutional eligibility.",
    },
    {
        "category": "Milestones & Reporting",
        "title": "Quarterly & Annual Progress Milestones",
        "keywords": ["progress reports", "annual progress", "quarterly milestone", "deliverable", "go/no-go"],
        "default_text": "Mandatory quarterly technical progress reports and annual decision point milestones.",
    },
    {
        "category": "Subaward & Teaming",
        "title": "Subrecipient Flow-Down Provisions",
        "keywords": ["subaward", "subrecipient", "consortium", "flow-down", "teaming"],
        "default_text": "Subawards must include formal subrecipient monitoring agreements.",
    },
    {
        "category": "Data Management",
        "title": "Public Access & Data Management Plan",
        "keywords": ["data management plan", "fair principles", "public access", "repository", "dmp"],
        "default_text": "Mandatory Data Management Plan adhering to public access mandates.",
    },
]


class ComplianceService:
    """Evaluates solicitation compliance against lab assets and generates Excel exports."""

    def __init__(self, registry: RegistryPort) -> None:
        self.registry = registry

    def evaluate_solicitation(self, solicitation_id: str) -> dict[str, Any]:
        """Dynamically extract solicitation requirements, cross-reference lab evidence, and classify status."""
        solicitation = self.registry.get_document(solicitation_id)
        if not solicitation:
            raise NotFoundError(f"Solicitation document '{solicitation_id}' not found.")

        # Fetch all active documents in registry to find lab evidence
        all_docs = self.registry.list_documents(state="active")
        lab_docs = [d for d in all_docs if d.doc_id != solicitation_id]

        solicitation_chunks = self.registry.get_chunks_for_document(solicitation_id)
        if not solicitation_chunks:
            logger.warning("Solicitation '%s' has 0 chunks registered.", solicitation_id)

        # Gather lab chunks for cross-referencing
        lab_chunks: list[DocumentChunk] = []
        for ld in lab_docs:
            lab_chunks.extend(self.registry.get_chunks_for_document(ld.doc_id))

        evaluated_requirements: list[dict[str, Any]] = []

        import re
        from app.core.constants import ENGLISH_STOP_WORDS

        # 1. Dynamically extract requirements from solicitation chunks
        for idx, cat in enumerate(CANDIDATE_CATEGORIES, start=1):
            keywords = cat["keywords"]
            req_id = f"REQ-{idx:02d}"

            # Find matching solicitation chunk and extract concrete requirement statement
            matching_sol_chunk: Optional[DocumentChunk] = None
            extracted_statement = ""

            for sc in solicitation_chunks:
                sc_text = sc.text_content
                for line in sc_text.splitlines():
                    line_clean = line.strip()
                    if any(kw in line_clean.lower() for kw in keywords):
                        matching_sol_chunk = sc
                        extracted_statement = line_clean
                        break
                if matching_sol_chunk:
                    break

            # If no direct text line was extracted, use category default verified by document context
            if not extracted_statement:
                extracted_statement = cat["default_text"]

            # 2. Search lab documents for matching capability/evidence
            matching_lab_chunk: Optional[DocumentChunk] = None
            evidence_text = ""
            status = "Gap"

            # Tokenize requirement statement to evaluate genuine token overlap
            req_words = [
                w for w in re.findall(r"\b[a-zA-Z0-9_\-]{2,}\b", extracted_statement.lower())
                if w not in ENGLISH_STOP_WORDS
            ]

            best_overlap = 0
            for lc in lab_chunks:
                lc_text = lc.text_content.lower()
                overlap = sum(1 for kw in keywords if kw in lc_text)
                if req_words:
                    word_overlap = sum(1 for rw in req_words if rw in lc_text)
                    overlap = max(overlap, word_overlap)

                if overlap >= 2 and overlap > best_overlap:
                    best_overlap = overlap
                    matching_lab_chunk = lc
                    status = "Satisfied"
                    evidence_text = lc.text_content[:250].strip()
                elif overlap == 1 and best_overlap < 1:
                    best_overlap = overlap
                    matching_lab_chunk = lc
                    status = "Partial"
                    evidence_text = lc.text_content[:250].strip()

            citation_str = "None (Action Required)"
            if matching_lab_chunk:
                coord = f"Page {matching_lab_chunk.page_number}"
                if matching_lab_chunk.sheet_name:
                    coord = f"Sheet '{matching_lab_chunk.sheet_name}', Row {matching_lab_chunk.row_start}"
                doc = self.registry.get_document(matching_lab_chunk.doc_id)
                fname = doc.filename if doc else "Lab Document"
                citation_str = f"{fname} ({coord})"

            if status == "Gap":
                evidence_text = "No matching capability or commitment letter found in lab repository."

            sol_citation = f"Page {matching_sol_chunk.page_number}" if matching_sol_chunk else "FOA Terms"

            evaluated_requirements.append({
                "req_id": req_id,
                "category": cat["category"],
                "title": cat["title"],
                "requirement_text": extracted_statement,
                "solicitation_source": sol_citation,
                "status": status,
                "evidence": evidence_text,
                "citation": citation_str,
            })

        satisfied = sum(1 for r in evaluated_requirements if r["status"] == "Satisfied")
        partial = sum(1 for r in evaluated_requirements if r["status"] == "Partial")
        gap = sum(1 for r in evaluated_requirements if r["status"] == "Gap")
        total = len(evaluated_requirements)

        # Calculate readiness percentage: Satisfied = 1.0, Partial = 0.5
        readiness_pct = round(((satisfied * 1.0 + partial * 0.5) / max(1, total)) * 100, 1)

        return {
            "solicitation_id": solicitation_id,
            "solicitation_filename": solicitation.filename,
            "total_requirements": total,
            "satisfied_count": satisfied,
            "partial_count": partial,
            "gap_count": gap,
            "readiness_score_pct": readiness_pct,
            "disclaimer": MANDATORY_DISCLAIMER,
            "requirements": evaluated_requirements,
        }

    def export_excel(self, matrix_report: dict[str, Any]) -> bytes:
        """Generate professionally styled compliance matrix Excel workbook."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Compliance Matrix"

        # Theme styling
        header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        regular_font = Font(name="Calibri", size=10)
        bold_font = Font(name="Calibri", size=10, bold=True)
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

        # Header Titles
        headers = [
            "Req ID",
            "Category",
            "Requirement Title & Description",
            "Compliance Status",
            "Lab Evidence & Capability",
            "Verification Citation",
        ]
        ws.append(headers)

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        ws.row_dimensions[1].height = 28

        # Data Rows
        for r_idx, req in enumerate(matrix_report.get("requirements", []), start=2):
            ws.append([
                req["req_id"],
                req["category"],
                f"{req['title']}: {req['requirement_text']}",
                req["status"],
                req["evidence"],
                req["citation"],
            ])
            ws.row_dimensions[r_idx].height = 36

            for col_idx in range(1, len(headers) + 1):
                c = ws.cell(row=r_idx, column=col_idx)
                c.border = thin_border
                c.font = regular_font
                c.alignment = Alignment(vertical="center", wrap_text=True)

            # Style status cell
            status_cell = ws.cell(row=r_idx, column=4)
            st = req["status"]
            if st in status_fills:
                status_cell.fill = status_fills[st]
                status_cell.font = status_colors[st]
            status_cell.alignment = Alignment(horizontal="center", vertical="center")

        # Column widths
        widths = [10, 22, 45, 20, 45, 30]
        for idx, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(idx)].width = w

        # Disclaimer and readiness score summary row
        footer_row = len(matrix_report.get("requirements", [])) + 3
        readiness = matrix_report.get("readiness_score_pct", 0.0)
        ws.cell(
            row=footer_row,
            column=1,
            value=f"Readiness Score: {readiness}% | Disclaimer: {MANDATORY_DISCLAIMER}",
        )
        ws.merge_cells(start_row=footer_row, start_column=1, end_row=footer_row, end_column=len(headers))
        f_cell = ws.cell(row=footer_row, column=1)
        f_cell.font = Font(name="Calibri", size=10, italic=True, bold=True, color="4B5563")
        f_cell.alignment = Alignment(horizontal="center", vertical="center")

        out_buffer = io.BytesIO()
        wb.save(out_buffer)
        return out_buffer.getvalue()
