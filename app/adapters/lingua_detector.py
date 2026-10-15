"""Lingua-based language detector (offline, no network, deterministic)."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Sequence

from lingua import Language, LanguageDetectorBuilder

from app.ports.language import ForeignLanguage, LanguageDetectorPort

_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


_MARKUP_RE = re.compile(r"<[^>]{0,200}>")


@lru_cache(maxsize=4)
def _shared_detector(candidates: tuple[str, ...]):
    languages = [Language.from_str(name) for name in candidates]
    if Language.ENGLISH not in languages:
        raise ValueError("language_candidates must include English")
    # Low-accuracy mode keeps memory modest; the English-vs-rest decision does not need the large models.
    return LanguageDetectorBuilder.from_languages(*languages).with_low_accuracy_mode().build()


class LinguaLanguageDetector(LanguageDetectorPort):
    def __init__(self, min_confidence: float, candidates: Sequence[str]) -> None:
        self.min_confidence = min_confidence
        self._detector = _shared_detector(tuple(candidates))

    def foreign_language(self, text: str, min_words: int) -> ForeignLanguage | None:
        text = _MARKUP_RE.sub(" ", text)  # markup/codes carry no language signal
        if len(_WORD_RE.findall(text)) < min_words:
            return None
        values = self._detector.compute_language_confidence_values(text)
        if not values:
            return None
        top = values[0]
        if top.language == Language.ENGLISH or top.value < self.min_confidence:
            return None
        return ForeignLanguage(top.language.name.title(), round(top.value, 3))
