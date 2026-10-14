"""XLSX extraction must recover values of simple formulas stored without cached results.

Generator-written (and many tool-written) workbooks store '=B2*C2' / '=SUM(B2:B4)' with no
cached value; a data_only reader then sees None and entire total columns vanish from
retrieval. The extractor re-evaluates the safe arithmetic subset and leaves everything else
(IF/VLOOKUP/cross-sheet) as None.
"""

from __future__ import annotations

import openpyxl
from app.services.extractor import DocumentExtractor


def _build_formula_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws.append(["Item", "Qty", "Unit Cost", "Total Cost"])
    ws.append(["Analyzer", 2, 9000, "=B2*C2"])          # 18000
    ws.append(["Supply", 1, 7500.5, "=B3*C3"])           # 7500.5
    ws.append(["Enclosure", 3, 1000, "=B4*C4"])          # 3000
    ws.append(["Subtotal", None, None, "=SUM(D2:D4)"])   # 28500.5
    ws.append(["WithIF", None, None, '=IF(D5>1000,"big","small")'])  # unsupported -> None
    wb.save(path)
    return path


def test_extractor_evaluates_simple_formulas_without_cached_values(tmp_path):
    path = _build_formula_workbook(tmp_path / "formulas.xlsx")
    result = DocumentExtractor(max_pages_per_doc=10, rows_per_chunk=10).extract("doc1", path, "formulas.xlsx")

    sheet = result.structured_sheets["Budget"]
    rows = {r[0]: r[1] for r in sheet["rows"]}
    assert rows[2][3] == 18000        # =B2*C2
    assert rows[3][3] == 7500.5       # float preserved
    assert rows[5][3] == 28500.5      # =SUM over a formula column (recursive resolve)
    assert rows[6][3] is None         # unsupported function stays None, never guessed

    # The computed values must also be visible in the extracted text (chunks feed retrieval/G4)
    text = " ".join(s.text for s in result.sections)
    assert "18000" in text and "28500.5" in text


def test_extractor_leaves_plain_workbooks_untouched(tmp_path):
    path = tmp_path / "plain.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "S"
    ws.append(["A", "B"])
    ws.append([1, 2])
    wb.save(path)
    result = DocumentExtractor(max_pages_per_doc=10, rows_per_chunk=10).extract("d", path, "plain.xlsx")
    assert {r[0]: r[1] for r in result.structured_sheets["S"]["rows"]}[2] == [1, 2]
