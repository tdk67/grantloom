"""Document extraction service for PDF, TXT, and XLSX files.

Adheres to:
- PDF: pypdf (text layer) + pdfplumber (table fallback)
- TXT: Line-aware text extraction
- XLSX: openpyxl multi-sheet tabular extraction with row coordinates
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import openpyxl
import pdfplumber
import pypdf

from app.core.errors import ExtractionError

logger = logging.getLogger("grantloom.services.extractor")


@dataclass
class ExtractedSection:
    page_number: int = 1
    sheet_name: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    row_start: Optional[int] = None
    row_end: Optional[int] = None
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractionResult:
    doc_id: str
    filename: str
    file_type: str
    page_count: int
    sections: list[ExtractedSection]
    structured_sheets: dict[str, dict[str, Any]] = field(default_factory=dict)



RUNNING_LINE_MIN_PAGES = 3
RUNNING_LINE_PAGE_SHARE = 0.5
RUNNING_LINE_EDGE = 3
_DIGITS_RE = re.compile(r"\d+")
_PAGE_NUMBER_RE = re.compile(r"(?i)\bpage\b|\b\d+\s*(?:of|/)\s*\d+\b|^\W*\d+\W*$")


def _signature(line: str) -> str:
    """Page-number lines compare with digits masked ("Page 3 of 25"); everything else must repeat exactly."""
    line = line.strip()
    return _DIGITS_RE.sub("#", line) if _PAGE_NUMBER_RE.search(line) else line


def _edge_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 2 * RUNNING_LINE_EDGE:  # too short to have a header/footer region distinct from the body
        return []
    return lines[:RUNNING_LINE_EDGE] + lines[-RUNNING_LINE_EDGE:]


def strip_running_headers(sections: list[ExtractedSection], total_pages: int) -> list[ExtractedSection]:
    """Remove running headers/footers: lines in the top or bottom RUNNING_LINE_EDGE lines of a page
    that (ignoring digits such as page numbers) recur on at least half of a multi-page document's
    pages. They carry no content but would otherwise become chunks that outrank real text for any
    query naming the document or organization. Only page-number lines are compared with digits masked.
    Limitation: a body sentence repeated verbatim within the outer lines of most pages is also removed."""
    if total_pages < RUNNING_LINE_MIN_PAGES:
        return sections
    page_lines: dict[int, set[str]] = {}
    for sec in sections:
        page_lines.setdefault(sec.page_number, set()).update(_signature(line) for line in _edge_lines(sec.text))
    counts: dict[str, int] = {}
    for lines in page_lines.values():
        for line in lines:
            counts[line] = counts.get(line, 0) + 1
    running = {line for line, n in counts.items() if n >= max(RUNNING_LINE_MIN_PAGES, total_pages * RUNNING_LINE_PAGE_SHARE)}
    if not running:
        return sections
    cleaned: list[ExtractedSection] = []
    for sec in sections:
        lines = [l for l in sec.text.splitlines() if l.strip()]
        edge = (
            set(range(RUNNING_LINE_EDGE)) | set(range(len(lines) - RUNNING_LINE_EDGE, len(lines)))
            if len(lines) > 2 * RUNNING_LINE_EDGE else set()
        )
        kept = [l for i, l in enumerate(lines) if i not in edge or _signature(l) not in running]
        text = "\n".join(kept).strip()
        if text:
            sec.text = text
            cleaned.append(sec)
    return cleaned


class DocumentExtractor:
    """Multi-format document extractor."""

    def __init__(self, max_pages_per_doc: int, rows_per_chunk: int) -> None:
        self.max_pages_per_doc = max_pages_per_doc
        self.rows_per_chunk = rows_per_chunk

    def extract(self, doc_id: str, file_path: str | Path, filename: str) -> ExtractionResult:
        """Dispatch extraction based on file extension."""
        path = Path(file_path)
        if not path.exists():
            raise ExtractionError(f"File not found on disk: {path}")

        ext = path.suffix.lower().lstrip(".")
        if ext == "pdf":
            return self.extract_pdf(doc_id, path, filename)
        elif ext == "txt":
            return self.extract_txt(doc_id, path, filename)
        elif ext in ("xlsx", "xls"):
            return self.extract_xlsx(doc_id, path, filename)
        elif ext == "csv":
            return self.extract_csv(doc_id, path, filename)
        else:
            raise ExtractionError(f"Unsupported file format: '.{ext}'")

    def extract_pdf(self, doc_id: str, path: Path, filename: str) -> ExtractionResult:
        """Extract PDF page-by-page using pypdf with pdfplumber table fallback."""
        try:
            reader = pypdf.PdfReader(str(path))
            total_pages = len(reader.pages)
            if total_pages > self.max_pages_per_doc:
                raise ExtractionError(
                    f"PDF page count ({total_pages}) exceeds limit of {self.max_pages_per_doc} pages."
                )

            sections: list[ExtractedSection] = []
            plumber_doc = None

            for page_idx, page in enumerate(reader.pages):
                page_num = page_idx + 1
                text = (page.extract_text() or "").strip()

                # If text is empty or very sparse, attempt pdfplumber extraction for tables
                if len(text) < 50:
                    if plumber_doc is None:
                        try:
                            plumber_doc = pdfplumber.open(str(path))
                        except Exception as exc:
                            logger.debug("pdfplumber fallback failed to open PDF: %s", exc)

                    if plumber_doc and page_idx < len(plumber_doc.pages):
                        plumber_page = plumber_doc.pages[page_idx]
                        tables = plumber_page.extract_tables()
                        if tables:
                            table_lines: list[str] = []
                            for tbl in tables:
                                for row in tbl:
                                    clean_row = [str(c or "").strip() for c in row]
                                    if any(clean_row):
                                        table_lines.append(" | ".join(clean_row))
                            if table_lines:
                                text = (text + "\n" + "\n".join(table_lines)).strip()
                        else:
                            plumber_text = (plumber_page.extract_text() or "").strip()
                            if plumber_text:
                                text = plumber_text

                if text:
                    sections.append(
                        ExtractedSection(
                            page_number=page_num,
                            text=text,
                            metadata={"filename": filename, "page": page_num},
                        )
                    )

            if plumber_doc:
                plumber_doc.close()

            sections = strip_running_headers(sections, total_pages)
            if not sections:
                raise ExtractionError(
                    f"PDF '{filename}' contains no extractable text or tables (scanned or image-only documents are unsupported)."
                )

            return ExtractionResult(
                doc_id=doc_id,
                filename=filename,
                file_type="pdf",
                page_count=total_pages,
                sections=sections,
            )
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError(f"Failed to extract text from PDF '{filename}': {exc}") from exc

    def extract_txt(self, doc_id: str, path: Path, filename: str) -> ExtractionResult:
        """Extract plain text tracking line boundaries."""
        try:
            # Try UTF-8 then fallback to latin-1
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = path.read_text(encoding="latin-1")

            lines = content.splitlines()
            sections: list[ExtractedSection] = []

            # Group lines into blocks of ~50 lines for manageable sectioning
            block_size = 50
            for i in range(0, max(1, len(lines)), block_size):
                block_lines = lines[i : i + block_size]
                text = "\n".join(block_lines).strip()
                if text:
                    sections.append(
                        ExtractedSection(
                            page_number=1,
                            line_start=i + 1,
                            line_end=min(i + block_size, len(lines)),
                            text=text,
                            metadata={"filename": filename, "line_start": i + 1, "line_end": min(i + block_size, len(lines))},
                        )
                    )

            return ExtractionResult(
                doc_id=doc_id,
                filename=filename,
                file_type="txt",
                page_count=1,
                sections=sections,
            )
        except Exception as exc:
            raise ExtractionError(f"Failed to extract text from TXT '{filename}': {exc}") from exc

    def extract_xlsx(self, doc_id: str, path: Path, filename: str) -> ExtractionResult:
        """Extract Excel sheets preserving sheet names and row coordinates."""
        try:
            wb = openpyxl.load_workbook(str(path), data_only=True)
            sections: list[ExtractedSection] = []

            structured_sheets: dict[str, dict[str, Any]] = {}
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                rows = list(ws.iter_rows(values_only=True))
                if not rows:
                    continue

                # First non-empty row as header
                headers: list[str] = []
                data_rows: list[tuple[int, list[Any]]] = []

                for row_idx, row in enumerate(rows, start=1):
                    non_empty = [c for c in row if c is not None and str(c).strip()]
                    if not non_empty:
                        continue
                    if not headers:
                        headers = [str(c or f"Col_{i+1}").strip() for i, c in enumerate(row)]
                    else:
                        data_rows.append((row_idx, list(row)))

                if headers and data_rows:
                    structured_sheets[sheet_name] = {
                        "columns": headers,
                        "rows": data_rows,
                    }

                if not data_rows and headers:
                    # Single header row with no data
                    sections.append(
                        ExtractedSection(
                            page_number=1,
                            sheet_name=sheet_name,
                            row_start=1,
                            row_end=1,
                            text=f"Sheet: {sheet_name} | Columns: {', '.join(headers)}",
                            metadata={"sheet": sheet_name, "row_start": 1, "row_end": 1},
                        )
                    )
                    continue

                # Batch data rows into chunks of 10 rows for clear tabular citations
                batch_size = self.rows_per_chunk
                for b_idx in range(0, len(data_rows), batch_size):
                    batch = data_rows[b_idx : b_idx + batch_size]
                    first_row_num = batch[0][0]
                    last_row_num = batch[-1][0]

                    row_texts: list[str] = []
                    for r_num, r_vals in batch:
                        cells = []
                        for col_idx, val in enumerate(r_vals):
                            if val is not None and str(val).strip():
                                col_name = headers[col_idx] if col_idx < len(headers) else f"Col_{col_idx+1}"
                                cells.append(f"{col_name}: {val}")
                        if cells:
                            row_texts.append(f"[Row {r_num}] " + ", ".join(cells))

                    if row_texts:
                        section_text = f"Sheet: {sheet_name}\n" + "\n".join(row_texts)
                        sections.append(
                            ExtractedSection(
                                page_number=1,
                                sheet_name=sheet_name,
                                row_start=first_row_num,
                                row_end=last_row_num,
                                text=section_text,
                                metadata={
                                    "sheet": sheet_name,
                                    "row_start": first_row_num,
                                    "row_end": last_row_num,
                                },
                            )
                        )

            return ExtractionResult(
                doc_id=doc_id,
                filename=filename,
                file_type="xlsx",
                page_count=max(1, len(wb.sheetnames)),
                sections=sections,
                structured_sheets=structured_sheets,
            )
        except Exception as exc:
            raise ExtractionError(f"Failed to extract spreadsheet '{filename}': {exc}") from exc

    def extract_csv(self, doc_id: str, path: Path, filename: str) -> ExtractionResult:
        """Extract CSV tabular data with line/row coordinates and structured table support."""
        import csv
        try:
            try:
                raw_text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raw_text = path.read_text(encoding="latin-1")

            lines = raw_text.splitlines()
            if not lines:
                raise ExtractionError(f"CSV file '{filename}' is empty.")

            reader = csv.reader(lines)
            rows = [row for row in reader if any(c.strip() for c in row)]
            if not rows:
                raise ExtractionError(f"CSV file '{filename}' contains no non-empty rows.")

            headers = [c.strip() or f"col_{idx+1}" for idx, c in enumerate(rows[0])]
            data_rows: list[tuple[int, list[Any]]] = []
            for r_idx, r in enumerate(rows[1:], start=2):
                data_rows.append((r_idx, [c.strip() for c in r]))

            structured_sheets = {
                "default": {
                    "columns": headers,
                    "rows": data_rows,
                }
            }

            sections: list[ExtractedSection] = []
            batch_size = self.rows_per_chunk
            for b_idx in range(0, max(1, len(data_rows)), batch_size):
                batch = data_rows[b_idx : b_idx + batch_size]
                if not batch:
                    break
                first_row = batch[0][0]
                last_row = batch[-1][0]
                row_texts = []
                for r_num, r_vals in batch:
                    cells = []
                    for c_idx, val in enumerate(r_vals):
                        if val:
                            c_name = headers[c_idx] if c_idx < len(headers) else f"col_{c_idx+1}"
                            cells.append(f"{c_name}: {val}")
                    if cells:
                        row_texts.append(f"[Row {r_num}] " + ", ".join(cells))

                if row_texts:
                    sections.append(
                        ExtractedSection(
                            page_number=1,
                            sheet_name="default",
                            row_start=first_row,
                            row_end=last_row,
                            text=f"Sheet: default\n" + "\n".join(row_texts),
                            metadata={"sheet": "default", "row_start": first_row, "row_end": last_row},
                        )
                    )

            if not sections and headers:
                sections.append(
                    ExtractedSection(
                        page_number=1,
                        sheet_name="default",
                        row_start=1,
                        row_end=1,
                        text=f"Sheet: default | Columns: {', '.join(headers)}",
                        metadata={"sheet": "default", "row_start": 1, "row_end": 1},
                    )
                )

            return ExtractionResult(
                doc_id=doc_id,
                filename=filename,
                file_type="csv",
                page_count=1,
                sections=sections,
                structured_sheets=structured_sheets,
            )
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError(f"Failed to extract CSV '{filename}': {exc}") from exc
