"""Delimiter hygiene for untrusted text placed inside model prompts.

Documents, filenames, queries and tool output are data written by third parties. Prompts wrap
them in fences and tell the model to treat the fenced text as data. A fence only helps if the
untrusted text cannot close it, so every untrusted string passes through `neutralize` first and
the writer fence carries a per-request random id the author of a document cannot know.
"""

from __future__ import annotations

import re
import secrets

REMOVED = "[delimiter removed]"
_FENCE_TAG_RE = re.compile(r"(?i)<\s*/?\s*untrusted[^>]*>?")
_FENCE_MARKER_RE = re.compile(r"(?i)<<<\s*/?\s*UNTRUSTED_CONTEXT[^>\n]*>*")


def neutralize(text: str) -> str:
    """Remove anything that looks like one of our fence delimiters from untrusted text."""
    return _FENCE_MARKER_RE.sub(REMOVED, _FENCE_TAG_RE.sub(REMOVED, text))


def new_fence_id() -> str:
    return secrets.token_hex(6)


def fence_markers(fence_id: str) -> tuple[str, str]:
    return f"<<<UNTRUSTED_CONTEXT_{fence_id} BEGIN>>>", f"<<<UNTRUSTED_CONTEXT_{fence_id} END>>>"
