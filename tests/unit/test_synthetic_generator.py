"""Unit tests for synthetic grant document generator and ground-truth manifests."""

import json
from pathlib import Path
import openpyxl
import pypdf
import pytest

from app.cli.generate_synthetic import parse_args
from app.synthetic.generator import SyntheticDataGenerator
from app.synthetic.models import GroundTruthManifest


@pytest.fixture
def temp_output_dir(tmp_path: Path) -> Path:
    """Fixture providing temporary output directory for synthetic generation."""
    out_dir = tmp_path / "synthetic_test_output"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def test_synthetic_data_generation_e2e(temp_output_dir: Path):
    """Verifies that SyntheticDataGenerator creates all expected artifacts."""
    generator = SyntheticDataGenerator(output_dir=temp_output_dir)
    results = generator.generate_all(quiet=True)

    # 1. Assert file entries
    file_names = [f["filename"] for f in results["files"]]
    assert "DOE_Solar_2026_Sample.pdf" in file_names
    assert "Past_Proposal_2024_Sample.pdf" in file_names
    assert "Lab_Budget_2025_Sample.xlsx" in file_names
    assert "Compliance_Matrix_Sample.xlsx" in file_names
    assert results["fact_count"] == 10

    # 2. Verify files exist on disk
    nofo_path = temp_output_dir / "DOE_Solar_2026_Sample.pdf"
    prop_path = temp_output_dir / "Past_Proposal_2024_Sample.pdf"
    budget_path = temp_output_dir / "Lab_Budget_2025_Sample.xlsx"
    compliance_path = temp_output_dir / "Compliance_Matrix_Sample.xlsx"
    manifest_path = temp_output_dir / "ground_truth_manifest.json"

    assert nofo_path.is_file() and nofo_path.stat().st_size > 10000
    assert prop_path.is_file() and prop_path.stat().st_size > 10000
    assert budget_path.is_file() and budget_path.stat().st_size > 2000
    assert compliance_path.is_file() and compliance_path.stat().st_size > 2000
    assert manifest_path.is_file()


def test_doe_solar_pdf_text_layer_and_planted_facts(temp_output_dir: Path):
    """Verifies page count and text layer content of DOE Solar NOFO PDF."""
    generator = SyntheticDataGenerator(output_dir=temp_output_dir)
    generator.generate_all(quiet=True)

    nofo_path = temp_output_dir / "DOE_Solar_2026_Sample.pdf"
    reader = pypdf.PdfReader(str(nofo_path))

    # Must be 25 pages as specified in M5 / data strategy
    assert len(reader.pages) >= 25

    # Extract all text
    full_text = " ".join([page.extract_text() for page in reader.pages])

    # Assert planted facts are present in the text layer
    assert "20.0%" in full_text
    assert "$50,000" in full_text
    assert "5 years" in full_text
    assert "November 15, 2026" in full_text
    assert "$45,000,000" in full_text
    assert "DE-FOA-0003291" in full_text


def test_past_proposal_pdf_text_layer_and_planted_facts(temp_output_dir: Path):
    """Verifies page count and text layer content of Past Proposal PDF."""
    generator = SyntheticDataGenerator(output_dir=temp_output_dir)
    generator.generate_all(quiet=True)

    prop_path = temp_output_dir / "Past_Proposal_2024_Sample.pdf"
    reader = pypdf.PdfReader(str(prop_path))

    # Must be at least 15 pages
    assert len(reader.pages) >= 15

    # Extract text
    full_text = " ".join([page.extract_text() for page in reader.pages])

    # Assert planted facts
    assert "Dr. Elena Vance" in full_text
    assert "Baltimore Innovation Center" in full_text
    assert "Grid Edge Testing Laboratory" in full_text
    assert "99.4%" in full_text
    assert "DE-EE0009871" in full_text


def test_budget_xlsx_sheets_and_formulas(temp_output_dir: Path):
    """Verifies sheet names, formula structures, and values in Lab_Budget_2025_Sample.xlsx."""
    generator = SyntheticDataGenerator(output_dir=temp_output_dir)
    generator.generate_all(quiet=True)

    budget_path = temp_output_dir / "Lab_Budget_2025_Sample.xlsx"
    wb = openpyxl.load_workbook(str(budget_path), data_only=False)

    expected_sheets = ["Personnel", "Travel", "Equipment", "Summary_Budget"]
    assert wb.sheetnames == expected_sheets

    # Inspect Travel sheet
    ws_travel = wb["Travel"]
    # Row 5 is total row in Travel sheet
    assert ws_travel.cell(row=5, column=1).value == "Travel Subtotal"
    assert ws_travel.cell(row=5, column=8).value == "=SUM(H2:H4)"

    # Inspect Equipment sheet
    ws_equip = wb["Equipment"]
    assert ws_equip.cell(row=5, column=1).value == "Equipment Subtotal"
    assert ws_equip.cell(row=5, column=5).value == "=SUM(E2:E4)"

    # Load with data_only=True or verify item values
    wb_val = openpyxl.load_workbook(str(budget_path), data_only=True)
    ws_equip_val = wb_val["Equipment"]
    # Unit costs: $18,000, $32,000, $18,000 (rows 2, 3, 4)
    unit_costs = [ws_equip_val.cell(row=r, column=4).value for r in range(2, 5)]
    assert unit_costs == [18000, 32000, 18000]
    # Assert none exceed $50,000
    assert all(c <= 50000 for c in unit_costs)


def test_ground_truth_manifest_validation(temp_output_dir: Path):
    """Validates that ground_truth_manifest.json conforms strictly to schema."""
    generator = SyntheticDataGenerator(output_dir=temp_output_dir)
    generator.generate_all(quiet=True)

    manifest_path = temp_output_dir / "ground_truth_manifest.json"
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    manifest = GroundTruthManifest.model_validate(data)
    assert manifest.version == "1.0.0"
    assert len(manifest.planted_facts) == 10

    # Ensure all planted facts have questions, answers, and locations
    for fact in manifest.planted_facts:
        assert fact.fact_id.startswith("FACT_")
        assert len(fact.question) > 10
        assert len(fact.expected_answer) > 10
        assert fact.document_name in manifest.documents


def test_cli_argument_parsing():
    """Tests CLI argument parsing defaults and flags."""
    args = parse_args(["--output-dir", "custom/dir", "--quiet"])
    assert args.output_dir == Path("custom/dir")
    assert args.quiet is True
