"""Chunker, running-header stripping, and eval value matching."""

from __future__ import annotations

from app.core.text import contains_value
from app.services.chunker import DocumentChunker
from app.services.extractor import ExtractedSection, ExtractionResult, strip_running_headers


def test_chunker_keeps_titles_with_names_and_never_duplicates_text():
    text = ("Introductory sentence about the program. " * 12) + "Principal Investigator: Dr. Elena Vance leads it. " + ("Closing remarks. " * 30)
    result = ExtractionResult(doc_id="d", filename="f.pdf", file_type="pdf", page_count=1,
                              sections=[ExtractedSection(page_number=1, text=text)])
    chunks = DocumentChunker(target_chunk_size=400, chunk_overlap=60).chunk_document(result)
    assert any("Dr. Elena Vance" in c.text_content for c in chunks)
    assert not any(c.text_content.rstrip().endswith("Dr.") for c in chunks)
    assert all(len(c.text_content) <= 400 + 60 for c in chunks)


def test_short_paragraphs_are_not_duplicated():
    paragraphs = "\n\n".join(f"Paragraph {i} " + "word " * 40 for i in range(6))
    result = ExtractionResult(doc_id="d", filename="f.txt", file_type="txt", page_count=1,
                              sections=[ExtractedSection(page_number=1, text=paragraphs)])
    chunks = DocumentChunker(target_chunk_size=300, chunk_overlap=0).chunk_document(result)
    joined = "\n".join(c.text_content for c in chunks)
    for i in range(6):
        assert joined.count(f"Paragraph {i} ") == 1


def test_running_headers_and_footers_are_stripped_but_body_repeats_kept():
    def page(n: int) -> ExtractedSection:
        body = [f"Body line {n}.{k} unique content." for k in range(3)] + ["Recipients must maintain federal compliance."] + [
            f"Body line {n}.{k} unique content." for k in range(3, 6)
        ]
        return ExtractedSection(page_number=n, text="\n".join(
            ["ACME Lab | Official Document", "Proposal CHIL-01"] + body + [f"Page {n} of 4"]
        ))

    cleaned = strip_running_headers([page(n) for n in range(1, 5)], total_pages=4)
    for sec in cleaned:
        assert "Official Document" not in sec.text and "Page" not in sec.text
        assert "Recipients must maintain federal compliance." in sec.text
        assert f"Body line {sec.page_number}.0 unique content." in sec.text


def test_contains_value_is_whole_value_not_substring():
    assert contains_value("The threshold is $50,000 per item.", "$50,000")
    assert contains_value("Total: 12450", "12,450")
    assert contains_value("A 20.0% cost share", "20%")
    assert not contains_value("Row 12 lists 2 travelers", "1")
    assert not contains_value("budgeted 120000", "12000")
    assert contains_value("Dr. Elena Vance is the PI", "elena vance")
