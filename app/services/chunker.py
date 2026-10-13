"""Deterministic, page-aware and section-aware document chunker."""

from __future__ import annotations

import hashlib
import re

from app.ports.models import DocumentChunk
from app.services.extractor import ExtractionResult


SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
SECTION_NUMBER_RE = re.compile(r"(?:^|\s)\d+(?:\.\d+)*\.$")  # "1." / "3.2." headings stay with their title
ABBREVIATIONS = (
    "Dr.", "Prof.", "Mr.", "Mrs.", "Ms.", "St.", "No.", "Inc.", "Ltd.", "Co.", "U.S.", "e.g.", "i.e.",
    "vs.", "Ph.D.", "M.S.", "M.D.", "Fig.", "Sec.", "approx.",
)


class DocumentChunker:
    """Produces deterministic chunk IDs (doc_id:page:index) with strict page boundaries."""

    def __init__(self, target_chunk_size: int, chunk_overlap: int) -> None:
        self.target_chunk_size = target_chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_document(self, result: ExtractionResult) -> list[DocumentChunk]:
        """Convert extracted sections into deterministic DocumentChunk objects."""
        all_chunks: list[DocumentChunk] = []
        global_index = 0

        # Group sections by page number to guarantee chunks NEVER span pages
        pages: dict[int, list] = {}
        for section in result.sections:
            pages.setdefault(section.page_number, []).append(section)

        for page_num in sorted(pages.keys()):
            sections_on_page = pages[page_num]
            page_chunk_idx = 0

            for sec in sections_on_page:
                raw_text = sec.text.strip()
                if not raw_text:
                    continue

                pieces = self._split_text(raw_text)
                for piece in pieces:
                    clean_piece = piece.strip()
                    if not clean_piece:
                        continue

                    chunk_id = f"{result.doc_id}:{page_num}:{page_chunk_idx}"
                    content_hash = hashlib.sha256(clean_piece.encode("utf-8")).hexdigest()
                    token_approx = len(clean_piece.split())

                    metadata = {
                        "doc_id": result.doc_id,
                        "filename": result.filename,
                        "file_type": result.file_type,
                        "page_number": page_num,
                        "chunk_index": page_chunk_idx,
                        "global_index": global_index,
                    }
                    if sec.sheet_name:
                        metadata["sheet_name"] = sec.sheet_name
                    if sec.row_start is not None:
                        metadata["row_start"] = sec.row_start
                        metadata["row_end"] = sec.row_end
                    if sec.line_start is not None:
                        metadata["line_start"] = sec.line_start
                        metadata["line_end"] = sec.line_end

                    chunk = DocumentChunk(
                        chunk_id=chunk_id,
                        doc_id=result.doc_id,
                        chunk_index=global_index,
                        page_number=page_num,
                        sheet_name=sec.sheet_name,
                        row_start=sec.row_start,
                        row_end=sec.row_end,
                        line_start=sec.line_start,
                        line_end=sec.line_end,
                        char_count=len(clean_piece),
                        token_count=token_approx,
                        text_content=clean_piece,
                        content_hash=content_hash,
                        metadata=metadata,
                    )
                    all_chunks.append(chunk)
                    page_chunk_idx += 1
                    global_index += 1

        return all_chunks

    @staticmethod
    def _sentences(text: str) -> list[str]:
        """Sentence split that does not break after abbreviations such as 'Dr.' or 'U.S.'."""
        merged: list[str] = []
        for piece in SENTENCE_END_RE.split(text):
            if merged and (merged[-1].endswith(ABBREVIATIONS) or SECTION_NUMBER_RE.search(merged[-1])):
                merged[-1] = f"{merged[-1]} {piece}"
            else:
                merged.append(piece)
        return merged

    def _split_text(self, text: str) -> list[str]:
        """Pack paragraphs (or, for long paragraphs, sentences) into windows of at most
        target_chunk_size characters; consecutive windows share up to chunk_overlap characters."""
        if len(text) <= self.target_chunk_size:
            return [text]

        units: list[tuple[str, str]] = []  # (text, separator used before it)
        for p in (p.strip() for p in re.split(r"\n\s*\n", text)):
            if not p:
                continue
            if len(p) <= self.target_chunk_size:
                units.append((p, "\n\n"))
            else:
                units.extend((s, " ") for s in self._sentences(p) if s.strip())

        windows: list[str] = []
        current: list[tuple[str, str]] = []

        def length(items: list[tuple[str, str]]) -> int:
            return sum(len(t) + len(sep) for t, sep in items)

        for unit in units:
            if current and length(current) + len(unit[0]) > self.target_chunk_size:
                windows.append(self._join(current))
                overlap: list[tuple[str, str]] = []
                for prev in reversed(current[1:]):
                    if length(overlap) + len(prev[0]) > self.chunk_overlap:
                        break
                    overlap.insert(0, prev)
                current = overlap
            current.append(unit)
        if current:
            windows.append(self._join(current))
        return windows

    @staticmethod
    def _join(items: list[tuple[str, str]]) -> str:
        return "".join((sep if i else "") + t for i, (t, sep) in enumerate(items))
