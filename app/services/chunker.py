"""Deterministic, page-aware and section-aware document chunker."""

from __future__ import annotations

import hashlib
import re
from typing import Sequence

from app.ports.models import DocumentChunk
from app.services.extractor import ExtractionResult


class DocumentChunker:
    """Produces deterministic chunk IDs (doc_id:page:index) with strict page boundaries."""

    def __init__(self, target_chunk_size: int = 800, chunk_overlap: int = 100) -> None:
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

    def _split_text(self, text: str) -> list[str]:
        """Split text into chunks by paragraphs or sliding window."""
        if len(text) <= self.target_chunk_size:
            return [text]

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for p in paragraphs:
            if current_len + len(p) > self.target_chunk_size and current:
                chunks.append("\n\n".join(current))
                # Keep last paragraph for overlap if reasonable
                if len(p) < self.target_chunk_size // 2:
                    current = [p]
                    current_len = len(p)
                else:
                    current = []
                    current_len = 0
            if len(p) > self.target_chunk_size:
                # Long paragraph: sliding window by sentence or words
                sentences = re.split(r"(?<=[.!?])\s+", p)
                sub_current: list[str] = []
                sub_len = 0
                for s in sentences:
                    if sub_len + len(s) > self.target_chunk_size and sub_current:
                        chunks.append(" ".join(sub_current))
                        sub_current = []
                        sub_len = 0
                    sub_current.append(s)
                    sub_len += len(s) + 1
                if sub_current:
                    chunks.append(" ".join(sub_current))
            else:
                current.append(p)
                current_len += len(p) + 2

        if current:
            chunks.append("\n\n".join(current))

        return chunks
