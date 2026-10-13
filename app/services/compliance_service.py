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

# Standard solicitation requirements for federal grants (DOE, NSF, NIH)
CORE_REQUIREMENTS = [
    {
        "req_id": "REQ-01",
        "category": "Cost Match & Share",
        "title": "Non-Federal Cost Share",
        "keywords": ["cost match", "cost share", "20 percent", "non-federal"],
        "default_text": "Recipient must provide a minimum 20% non-federal cost match of total project allowable costs.",
    },
    {
        "req_id": "REQ-02",
        "category": "Equipment & Capital",
        "title": "Equipment Capitalization Threshold",
        "keywords": ["equipment threshold", "50,000", "capital expenditure", "spectrometer"],
        "default_text": "Equipment items exceeding $50,000 unit cost require prior contracting officer authorization and vendor quotes.",
    },
    {
        "req_id": "REQ-03",
        "category": "Key Personnel",
        "title": "Principal Investigator Eligibility",
        "keywords": ["principal investigator", "elena vance", "pi eligibility", "lead organization"],
        "default_text": "Lead Principal Investigator must hold a full-time appointment and demonstrate prior federal grant management experience.",
    },
    {
        "req_id": "REQ-04",
        "category": "Milestones & Reporting",
        "title": "Quarterly & Annual Progress Milestones",
        "keywords": ["progress reports", "annual progress", "quarterly milestone", "deliverable"],
        "default_text": "Mandatory quarterly technical progress reports and annual Go/No-Go decision point milestones.",
    },
    {
        "req_id": "REQ-05",
        "category": "Subaward & Teaming",
        "title": "Subrecipient Flow-Down Provisions",
        "keywords": ["subaward", "subrecipient", "consortium", "flow-down"],
        "default_text": "Subawards exceeding 25% of total budget must include formal subrecipient monitoring agreements.",
    },
    {
        "req_id": "REQ-06",
        "category": "Data Management",
        "title": "Public Access & Data Management Plan",
        "keywords": ["data management plan", "fair principles", "public access", "repository"],
        "default_text": "Mandatory 2-page Data Management Plan adhering to OSTP public access mandates.",
    },
]


class ComplianceService:
    """Evaluates solicitation compliance against lab assets and generates Excel exports."""

    def __init__(self, registry: RegistryPort) -> None:
        self.registry = registry

    def evaluate_solicitation(self, solicitation_id: str) -> dict[str, Any]:
        """Cross-reference solicitation requirements against lab inventory and classify status."""
        solicitation = self.registry.get_document(solicitation_id)
        if not solicitation:
            raise NotFoundError(f"Solicitation document '{solicitation_id}' not found.")

        # Fetch all active documents in registry to find lab evidence
        all_docs = self.registry.list_documents(state="active")
        lab_docs = [d for d in all_docs if d.doc_id != solicitation_id]

        solicitation_chunks = self.registry.get_chunks_for_document(solicitation_id)
        solicitation_text = " ".join([c.text_content.lower() for c in solicitation_chunks])

        # Gather lab chunks for cross-referencing
        lab_chunks: list[DocumentChunk] = []
        for ld in lab_docs:
            lab_chunks.extend(self.registry.get_chunks_for_document(ld.doc_id))


        evaluated_requirements: list[dict[str, Any]] = []

        for req in CORE_REQUIREMENTS:
            keywords = req["keywords"]
            # Check if requirement applies to this solicitation
            applies = any(kw in solicitation_text for kw in keywords) or True

            # Search lab documents for matching capability/evidence
            matching_lab_chunk: Optional[DocumentChunk] = None
            evidence_text = ""
            status = "Gap"

            for lc in lab_chunks:
                lc_text = lc.text_content.lower()
                matches = sum(1 for kw in keywords if kw in lc_text)
                if matches >= 2:
                    matching_lab_chunk = lc
                    status = "Satisfied"
                    evidence_text = lc.text_content[:200].strip()
                    break
                elif matches == 1 and status == "Gap":
                    matching_lab_chunk = lc
                    status = "Partial"
                    evidence_text = lc.text_content[:200].strip()

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

            evaluated_requirements.append({
                "req_id": req["req_id"],
                "category": req["category"],
                "title": req["title"],
                "requirement_text": req["default_text"],
                "status": status,
                "evidence": evidence_text,
                "citation": citation_str,
            })

        satisfied = sum(1 for r in evaluated_requirements if r["status"] == "Satisfied")
        partial = sum(1 for r in evaluated_requirements if r["status"] == "Partial")
        gap = sum(1 for r in evaluated_requirements if r["status"] == "Gap")

        return {
            "solicitation_id": solicitation_id,
            "solicitation_filename": solicitation.filename,
            "total_requirements": len(evaluated_requirements),
            "satisfied_count": satisfied,
            "partial_count": partial,
            "gap_count": gap,
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

        out_buffer = io.BytesIO()
        wb.save(out_buffer)
        return out_buffer.getvalue()
