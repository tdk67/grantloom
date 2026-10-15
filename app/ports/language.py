"""Port: language identification outside the model.

A prompt instruction such as "answer only in English" is not a control: the attacker's text is in
the same channel as the instruction. Language is therefore decided by a detector that cannot be
talked out of its answer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ForeignLanguage:
    language: str
    confidence: float


class LanguageDetectorPort(ABC):
    @abstractmethod
    def foreign_language(self, text: str, min_words: int) -> ForeignLanguage | None:
        """Return the language when `text` is confidently NOT English, else None.

        None also covers "too short or too ambiguous to tell" (fewer than `min_words` alphabetic
        words, acronym/number-heavy text): callers must not treat None as proof of English.
        """
