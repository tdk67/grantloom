"""Shared lexical helpers (tokenization, sentence splitting, lexicon matching, value normalization)."""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from app.core.constants import ENGLISH_STOP_WORDS

TOKEN_RE = re.compile(r"\b[a-zA-Z0-9_\-]{2,}\b")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
NUMBER_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")
COMMON_ABBREVIATIONS = {
    "dr.", "prof.", "mr.", "mrs.", "ms.", "vs.", "approx.", "dept.",
    "fig.", "no.", "al.", "inc.", "corp.", "ltd.", "co.", "gov.", "e.g.", "i.e."
}


def content_tokens(text: str) -> list[str]:
    """Lowercased tokens of length >= 2 with English stop words removed."""
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in ENGLISH_STOP_WORDS]


def overlap_ratio(query: str, text: str) -> float:
    """Share of the query's content tokens that appear in text (0.0 when the query has none)."""
    q_tokens = set(content_tokens(query))
    if not q_tokens:
        return 0.0
    t_tokens = set(TOKEN_RE.findall(text.lower()))
    return len(q_tokens & t_tokens) / len(q_tokens)


def split_sentences(text: str) -> list[str]:
    """Split text into sentences, preserving honorifics (e.g. Dr., Prof.) and abbreviations."""
    raw_splits = [s.strip() for s in SENTENCE_SPLIT_RE.split(text) if s and s.strip()]
    merged: list[str] = []
    for s in raw_splits:
        if merged:
            last_token = merged[-1].split()[-1].lower().strip("*_`~()[]\"'")
            if last_token in COMMON_ABBREVIATIONS:
                merged[-1] = f"{merged[-1]} {s}"
                continue
        merged.append(s)
    return merged


def normalize_number(raw: str) -> str:
    """'$50,000' -> '50000', '20.0%' -> '20', '12' -> '12'."""
    cleaned = raw.replace("$", "").replace(",", "").rstrip("%")
    if "." in cleaned:
        cleaned = cleaned.rstrip("0").rstrip(".")
    return cleaned


def numbers_in(text: str) -> set[str]:
    return {normalize_number(m) for m in NUMBER_RE.findall(text)}


def lexicon_hits(text: str, terms: Iterable[str]) -> list[str]:
    """Lexicon entries present in text. 'stem*' matches word prefixes; multi-word entries match as phrases."""
    lowered = f" {text.lower()} "
    words = re.findall(r"[a-z0-9][a-z0-9/&.\-]*", text.lower())
    hits = []
    for term in terms:
        if term.endswith("*"):
            stem = term[:-1]
            if " " in stem:
                if stem in lowered:
                    hits.append(term)
            elif any(w.startswith(stem) for w in words):
                hits.append(term)
        elif " " in term:
            if f" {term} " in lowered or f" {term}" in lowered:
                hits.append(term)
        elif term in words:
            hits.append(term)
    return hits


def contains_value(haystack: str, expected: str) -> bool:
    """Whole-value match used by the eval: numbers compare normalized as whole tokens; text compares
    case-insensitively on word boundaries (no single-digit substring luck)."""
    expected = expected.strip()
    if not expected:
        return False
    if NUMBER_RE.fullmatch(expected):
        return normalize_number(expected) in numbers_in(haystack)
    pattern = r"(?<![a-z0-9])" + re.escape(expected.lower()) + r"(?![a-z0-9])"
    return re.search(pattern, haystack.lower()) is not None


def first_match(patterns: Sequence[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None
