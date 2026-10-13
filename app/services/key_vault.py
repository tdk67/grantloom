"""Process-local holder for BYOK keys supplied with an upload (FR-Q1: never persisted or logged).

The ingestion worker needs the uploader's key for the Tier-2 semantic scan (FR-I5) of that document.
Keys live only in memory, keyed by doc_id, and are discarded when the job finishes. After a restart
they are gone; the scan then records Tier 2 as skipped instead of failing the document.
"""

from __future__ import annotations

import threading
from typing import Optional


class EphemeralKeyVault:
    def __init__(self) -> None:
        self._keys: dict[str, str] = {}
        self._lock = threading.Lock()

    def put(self, doc_id: str, key: Optional[str]) -> None:
        if key:
            with self._lock:
                self._keys[doc_id] = key

    def get(self, doc_id: str) -> Optional[str]:
        with self._lock:
            return self._keys.get(doc_id)

    def discard(self, doc_id: str) -> None:
        with self._lock:
            self._keys.pop(doc_id, None)

    def __repr__(self) -> str:  # never expose key material in logs or tracebacks
        return f"EphemeralKeyVault(entries={len(self._keys)})"
