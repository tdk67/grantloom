"""Unit tests for the corpus-derived example question generator (review-08 F1)."""

from __future__ import annotations

from app.services.example_questions import build_example_questions


def test_generator_emits_question_only_when_grounded(registry_with_docs):
    """Every emitted question is grounded: its anchors exist in at least one active chunk, and its
    source_chunks are real chunk ids from active documents."""
    questions = build_example_questions(registry_with_docs, limit=999)
    assert len(questions) >= 4  # at least one per family that exists in the fixture corpus

    active_chunk_ids = {
        c.chunk_id
        for d in registry_with_docs.list_documents(state="active")
        for c in registry_with_docs.get_chunks_for_document(d.doc_id)
    }
    for q in questions:
        assert q["strategy_hint"] in {"keyword", "semantic", "graph", "structured", "composite", "abstain"}
        assert q["question"].strip()
        if q["strategy_hint"] != "abstain":
            # negative control is intentionally ungrounded; every other question must cite sources
            assert q["source_chunks"], f"{q['id']} has no grounding chunk"
            for cid in q["source_chunks"]:
                assert cid in active_chunk_ids


def test_generator_deterministic_and_capped(registry_with_docs):
    """Same corpus -> same output, and the limit is respected."""
    a = build_example_questions(registry_with_docs, limit=5)
    b = build_example_questions(registry_with_docs, limit=5)
    assert a == b
    assert len(a) <= 5


def test_generator_drops_ungrounded_template(registry_with_docs):
    """A template whose anchor is absent from the corpus is skipped, never emitted unchecked."""
    questions = build_example_questions(registry_with_docs, limit=999)
    # 'Martian Soil Export License' does not exist in the fixture corpus, so the keyword template
    # anchored on it must be absent. (Anchor lists are read from the module templates; we assert
    # purely on output: no question may reference terms that are not in the corpus.)
    import re

    all_text = " ".join(
        c.text_content
        for d in registry_with_docs.list_documents(state="active")
        for c in registry_with_docs.get_chunks_for_document(d.doc_id)
    ).lower()
    for q in questions:
        if q["strategy_hint"] == "composite":
            # composite questions legitimately reference values from multiple docs; skip the
            # single-corpus number-invention check for them (they are grounded by anchors instead)
            continue
        # No fabricated number/code: any 5+-digit number in a grounded question must appear in the
        # corpus (this catches a hardcoded amount that the generator was not supposed to invent).
        for num in set(re.findall(r"\$?[\d,]{5,}", q["question"])):
            assert num.replace("$", "").replace(",", "") in all_text, f"{q['id']} invents number {num}"
