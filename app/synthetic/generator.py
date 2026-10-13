"""Orchestrates generation of high-fidelity synthetic grant documents and evaluation manifests."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Dict
import pypdf

from app.synthetic.excel_builder import SyntheticExcelBuilder
from app.synthetic.fixtures import (
    build_compliance_matrix_spec,
    build_cost_share_letter_spec,
    build_doe_solar_nofo_spec,
    build_facilities_capability_spec,
    build_lab_budget_specs,
    build_past_proposal_spec,
    get_planted_facts,
)
from app.synthetic.models import GroundTruthManifest
from app.synthetic.pdf_builder import SyntheticPDFBuilder


class SyntheticDataGenerator:
    """Generates synthetic grant solicitations, proposals, spreadsheets, and ground-truth manifests."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.pdf_builder = SyntheticPDFBuilder()
        self.excel_builder = SyntheticExcelBuilder()

    def generate_all(self, quiet: bool = False) -> Dict[str, any]:
        """Generates all synthetic documents and writes the ground-truth manifest."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        results: Dict[str, any] = {"files": [], "manifest_path": None, "fact_count": 0}

        # 1. Generate DOE Solar NOFO Solicitation PDF (~25 pages)
        nofo_spec = build_doe_solar_nofo_spec()
        nofo_path = self.output_dir / nofo_spec.filename
        self.pdf_builder.build_pdf(nofo_spec, nofo_path)
        nofo_pages = self._count_pdf_pages(nofo_path)
        results["files"].append(
            {
                "filename": nofo_spec.filename,
                "type": "solicitation_pdf",
                "path": str(nofo_path),
                "pages": nofo_pages,
                "size_bytes": nofo_path.stat().st_size,
            }
        )
        if not quiet:
            print(f"[SyntheticData] Generated {nofo_spec.filename}: {nofo_pages} pages ({nofo_path.stat().st_size:,} bytes)")

        # 2. Generate Past Proposal PDF (~15 pages)
        prop_spec = build_past_proposal_spec()
        prop_path = self.output_dir / prop_spec.filename
        self.pdf_builder.build_pdf(prop_spec, prop_path)
        prop_pages = self._count_pdf_pages(prop_path)
        results["files"].append(
            {
                "filename": prop_spec.filename,
                "type": "proposal_pdf",
                "path": str(prop_path),
                "pages": prop_pages,
                "size_bytes": prop_path.stat().st_size,
            }
        )
        if not quiet:
            print(f"[SyntheticData] Generated {prop_spec.filename}: {prop_pages} pages ({prop_path.stat().st_size:,} bytes)")

        # 3. Generate Multi-Sheet Lab Budget XLSX (4 sheets)
        budget_sheets = build_lab_budget_specs()
        budget_path = self.output_dir / "Lab_Budget_2025_Sample.xlsx"
        self.excel_builder.build_workbook(budget_sheets, budget_path)
        results["files"].append(
            {
                "filename": budget_path.name,
                "type": "budget_xlsx",
                "path": str(budget_path),
                "sheets": len(budget_sheets),
                "size_bytes": budget_path.stat().st_size,
            }
        )
        if not quiet:
            print(f"[SyntheticData] Generated {budget_path.name}: {len(budget_sheets)} sheets ({budget_path.stat().st_size:,} bytes)")

        # 4. Generate Compliance Checklist XLSX (1 sheet)
        compliance_spec = build_compliance_matrix_spec()
        compliance_path = self.output_dir / "Compliance_Matrix_Sample.xlsx"
        self.excel_builder.build_workbook([compliance_spec], compliance_path)
        results["files"].append(
            {
                "filename": compliance_path.name,
                "type": "compliance_xlsx",
                "path": str(compliance_path),
                "sheets": 1,
                "size_bytes": compliance_path.stat().st_size,
            }
        )
        if not quiet:
            print(f"[SyntheticData] Generated {compliance_path.name}: 1 sheet ({compliance_path.stat().st_size:,} bytes)")

        # 5. Generate Cost-Share Commitment Letter PDF (compliance evidence)
        letter_spec = build_cost_share_letter_spec()
        letter_path = self.output_dir / letter_spec.filename
        self.pdf_builder.build_pdf(letter_spec, letter_path)
        letter_pages = self._count_pdf_pages(letter_path)
        results["files"].append(
            {
                "filename": letter_spec.filename,
                "type": letter_spec.doc_type,
                "path": str(letter_path),
                "pages": letter_pages,
                "size_bytes": letter_path.stat().st_size,
            }
        )
        if not quiet:
            print(f"[SyntheticData] Generated {letter_spec.filename}: {letter_pages} pages ({letter_path.stat().st_size:,} bytes)")

        # 6. Generate Facilities & Capability Statement PDF (lab evidence)
        fac_spec = build_facilities_capability_spec()
        fac_path = self.output_dir / fac_spec.filename
        self.pdf_builder.build_pdf(fac_spec, fac_path)
        fac_pages = self._count_pdf_pages(fac_path)
        results["files"].append(
            {
                "filename": fac_spec.filename,
                "type": fac_spec.doc_type,
                "path": str(fac_path),
                "pages": fac_pages,
                "size_bytes": fac_path.stat().st_size,
            }
        )
        if not quiet:
            print(f"[SyntheticData] Generated {fac_spec.filename}: {fac_pages} pages ({fac_path.stat().st_size:,} bytes)")

        # 7. Build Ground-Truth Evaluation Manifest
        planted_facts = get_planted_facts()
        manifest = GroundTruthManifest(
            version="1.0.0",
            generated_at=datetime.now(timezone.utc).isoformat(),
            documents=[f["filename"] for f in results["files"]],
            planted_facts=planted_facts,
        )
        manifest_path = self.output_dir / "ground_truth_manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest.model_dump(), f, indent=2)

        results["manifest_path"] = str(manifest_path)
        results["fact_count"] = len(planted_facts)
        if not quiet:
            print(f"[SyntheticData] Generated {manifest_path.name}: {len(planted_facts)} evaluation facts recorded.")

        return results

    def _count_pdf_pages(self, pdf_path: Path) -> int:
        try:
            reader = pypdf.PdfReader(str(pdf_path))
            return len(reader.pages)
        except Exception:
            return 0
