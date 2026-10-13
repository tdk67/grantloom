"""Instruction-shaped text aimed at a model, shared by the G1 prefilter, G3, and the Tier-1 ingestion scan."""

from __future__ import annotations

import re

INJECTION_PATTERNS = [
    re.compile(r"(?i)\b(ignore|disregard|forget|override)\s+(all\s+)?(previous|prior|above|system)\s+(instructions|prompts|rules|commands|directives)\b"),
    re.compile(r"(?i)\b(system\s+prompt|reveal\s+(system\s+)?(instructions|prompt)|leak\s+prompt)\b"),
    re.compile(r"(?i)\b(you\s+are\s+now|act\s+as\s+(an?\s+)?unrestricted|jailbreak|dan\s+mode)\b"),
    re.compile(r"(?i)<\s*script[\s>]"),
    re.compile(r"(?i)\b(bypass|disable)\s+guardrails?\b"),
]
DOCUMENT_INJECTION_PATTERNS = INJECTION_PATTERNS + [
    re.compile(r"(?i)\[\s*(important\s+instruction|system\s+directive|developer\s+mode)\s*:\s*.*\]"),
    re.compile(r"(?i)\b(assistant\s+must\s+output|always\s+respond\s+with|exfiltrate)\b"),
    re.compile(r"(?i)\b(system\s+directive|developer\s+mode)\s*:"),
]
