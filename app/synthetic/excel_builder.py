"""High-fidelity Excel workbook builder for multi-sheet grant budgets and compliance matrices."""

from pathlib import Path
from typing import Any, Dict, List, Optional
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


class SheetSpec:
    """Specification for an individual Excel worksheet."""

    def __init__(
        self,
        title: str,
        headers: List[str],
        rows: List[List[Any]],
        total_row: Optional[List[Any]] = None,
        column_formats: Optional[Dict[int, str]] = None,  # 1-indexed col -> format string
        note: Optional[str] = None,
    ):
        self.title = title
        self.headers = headers
        self.rows = rows
        self.total_row = total_row
        self.column_formats = column_formats or {}
        self.note = note


class SyntheticExcelBuilder:
    """Builds professional, styled multi-sheet Excel files with formulas and coordinate tracking."""

    def __init__(self):
        # Color palette
        self.header_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
        self.header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        self.subtotal_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
        self.subtotal_font = Font(name="Calibri", size=11, bold=True, color="0F172A")
        self.regular_font = Font(name="Calibri", size=11, color="1E293B")
        self.note_font = Font(name="Calibri", size=9, italic=True, color="64748B")

        self.thin_border = Border(
            left=Side(style="thin", color="CBD5E1"),
            right=Side(style="thin", color="CBD5E1"),
            top=Side(style="thin", color="CBD5E1"),
            bottom=Side(style="thin", color="CBD5E1"),
        )
        self.total_border = Border(
            left=Side(style="thin", color="CBD5E1"),
            right=Side(style="thin", color="CBD5E1"),
            top=Side(style="thin", color="0F172A"),
            bottom=Side(style="double", color="0F172A"),
        )

    def build_workbook(self, sheets: List[SheetSpec], output_path: Path) -> Path:
        """Generates an Excel workbook with multiple styled sheets."""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        wb = openpyxl.Workbook()
        # Remove default sheet
        wb.remove(wb.active)

        for sheet_spec in sheets:
            ws = wb.create_sheet(title=sheet_spec.title)
            ws.views.sheetView[0].showGridLines = True

            curr_row = 1

            # Header row (Row 1)
            for col_idx, header_text in enumerate(sheet_spec.headers, start=1):
                cell = ws.cell(row=curr_row, column=col_idx, value=header_text)
                cell.fill = self.header_fill
                cell.font = self.header_font
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                cell.border = self.thin_border
            ws.row_dimensions[curr_row].height = 26
            curr_row += 1

            # Data rows (Row 2 .. N)
            for r_data in sheet_spec.rows:
                for col_idx, val in enumerate(r_data, start=1):
                    cell = ws.cell(row=curr_row, column=col_idx, value=val)
                    cell.font = self.regular_font
                    cell.border = self.thin_border

                    # Apply column format if specified
                    fmt = sheet_spec.column_formats.get(col_idx)
                    if fmt:
                        cell.number_format = fmt
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                    elif isinstance(val, (int, float)):
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                    else:
                        cell.alignment = Alignment(horizontal="left", vertical="center")
                curr_row += 1

            # Subtotal / Total row
            if sheet_spec.total_row:
                for col_idx, val in enumerate(sheet_spec.total_row, start=1):
                    cell = ws.cell(row=curr_row, column=col_idx, value=val)
                    cell.fill = self.subtotal_fill
                    cell.font = self.subtotal_font
                    cell.border = self.total_border

                    fmt = sheet_spec.column_formats.get(col_idx)
                    if fmt:
                        cell.number_format = fmt
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                    elif isinstance(val, (int, float)):
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                    else:
                        cell.alignment = Alignment(horizontal="left", vertical="center")
                ws.row_dimensions[curr_row].height = 22
                curr_row += 1

            # Optional sheet note placed cleanly below the table
            if sheet_spec.note:
                curr_row += 1
                cell = ws.cell(row=curr_row, column=1, value=f"* Note: {sheet_spec.note}")
                cell.font = self.note_font

            # Auto-fit column widths with padding
            for col in ws.columns:
                max_len = 0
                col_letter = get_column_letter(col[0].column)
                for cell in col:
                    if cell.value:
                        val_str = str(cell.value)
                        # don't measure formulas as full len if starting with =
                        if val_str.startswith("="):
                            val_str = "123,456.78"
                        max_len = max(max_len, len(val_str))
                ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

        wb.save(str(output_path))
        return output_path
