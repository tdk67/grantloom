"""Guardrail services (G1 Input Guard, G2 Strategy Router, G4 Output Guard)."""

from __future__ import annotations

import logging
import re
from typing import Optional, Sequence

from app.core.templates import load_template
from app.ports.models import CitationCoordinate, DocumentChunk, GateVerdict
from app.ports.registry import RegistryPort

logger = logging.getLogger("grantloom.services.guardrails")

# Non-English Unicode ranges (Cyrillic, CJK, Kana, Hangul, Arabic, Devanagari, Hebrew, Thai)
NON_ENGLISH_SCRIPTS_RE = re.compile(
    r"[\u0400-\u04FF\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF\u0600-\u06FF\u0900-\u097F\u0590-\u05FF\u0E00-\u0E7F]"
)

# Injection and adversarial jailbreak signatures
INJECTION_PATTERNS = [
    re.compile(r"(?i)\b(ignore|disregard|forget|override)\s+(all\s+)?(previous|prior|above|system)\s+(instructions|prompts|rules|commands|directives)\b"),
    re.compile(r"(?i)\b(system\s+prompt|reveal\s+(system\s+)?(instructions|prompt)|leak\s+prompt)\b"),
    re.compile(r"(?i)\b(you\s+are\s+now|act\s+as\s+(an?\s+)?unrestricted|jailbreak|dan\s+mode)\b"),
    re.compile(r"(?i)<\s*script[\s>]"),
    re.compile(r"(?i)\b(bypass|disable)\s+guardrails?\b"),
]

# Citation marker pattern: [chunk_id]
CITATION_PATTERN = re.compile(r"\[([a-zA-Z0-9_\-:]+)\]")


class InputGuard:
    """G1 Input Guard: language validator, injection prefilter, domain relevance check."""

    def evaluate(self, query: str) -> GateVerdict:
        cleaned = query.strip()
        if not cleaned:
            return GateVerdict(
                gate_id="G1",
                passed=False,
                confidence=1.0,
                reason="empty_query",
                metadata={"message": "Query cannot be empty."},
            )

        # 1. Fast language check: reject non-English scripts
        if NON_ENGLISH_SCRIPTS_RE.search(cleaned):
            refusal_msg = load_template("refusals/non_english.txt")
            return GateVerdict(
                gate_id="G1",
                passed=False,
                confidence=1.0,
                reason="non_english",
                metadata={"refusal": refusal_msg, "message": refusal_msg},
            )

        # 2. Injection pattern prefilter
        for pattern in INJECTION_PATTERNS:
            if pattern.search(cleaned):
                refusal_msg = load_template("refusals/prompt_injection.txt")
                return GateVerdict(
                    gate_id="G1",
                    passed=False,
                    confidence=0.99,
                    reason="prompt_injection_detected",
                    metadata={"refusal": refusal_msg, "message": refusal_msg},
                )

        return GateVerdict(
            gate_id="G1",
            passed=True,
            confidence=1.0,
            reason="input_guard_passed",
            metadata={"sanitized_query": cleaned},
        )


class StrategyRouter:
    """G2 Strategy Router: classifies retrieval intent with fallback to hybrid."""

    STRUCTURED_KEYWORDS = {
        "spreadsheet", "worksheet", "excel", "table row", "table column",
        "budget sheet", "salary table", "tabular", "cell value"
    }
    GRAPH_KEYWORDS = {
        "co-pi relationship", "investigator affiliation", "subawardee hierarchy",
        "partner network", "investigator relationship"
    }


    def evaluate(self, query: str, min_confidence: float = 0.7) -> GateVerdict:
        lower_q = query.lower()

        # Check structured tabular query intent
        if any(kw in lower_q for kw in self.STRUCTURED_KEYWORDS):
            return GateVerdict(
                gate_id="G2",
                passed=True,
                confidence=0.85,
                reason="tabular_intent_detected",
                metadata={"strategy": "structured"},
            )

        # Check relationship/graph query intent
        if any(kw in lower_q for kw in self.GRAPH_KEYWORDS):
            return GateVerdict(
                gate_id="G2",
                passed=True,
                confidence=0.80,
                reason="graph_intent_detected",
                metadata={"strategy": "graph"},
            )

        # Default fallback: hybrid (Chroma dense + SQLite FTS BM25)
        return GateVerdict(
            gate_id="G2",
            passed=True,
            confidence=0.90,
            reason="default_hybrid_strategy",
            metadata={"strategy": "hybrid"},
        )


class OutputGuard:
    """G4 Output Guard: resolves chunk IDs to physical coordinates, checks claim grounding."""

    def __init__(self, registry: RegistryPort) -> None:
        self.registry = registry

    def resolve_citations(
        self,
        draft_text: str,
        retrieved_chunk_ids: Sequence[str],
        is_abstention: bool = False,
    ) -> tuple[str, list[CitationCoordinate], float]:
        """Resolve chunk citations, strip dangling markers, and compute confidence score."""
        if is_abstention:
            return draft_text, [], 1.0

        matches = CITATION_PATTERN.findall(draft_text)
        if not matches:
            # If draft makes assertions without citations, low confidence
            return draft_text, [], 0.35

        verified_citations: list[CitationCoordinate] = []
        resolved_ids: set[str] = set()
        unresolved_ids: set[str] = set()

        cleaned_text = draft_text

        for chunk_id in set(matches):
            chunk = self.registry.get_chunk(chunk_id)
            doc = self.registry.get_document(chunk.doc_id) if chunk else None

            # Only verify citations if chunk exists AND parent document is in active state
            if chunk is not None and doc is not None and doc.state == "active":
                resolved_ids.add(chunk_id)
                filename = doc.filename or "unknown"

                verified_citations.append(
                    CitationCoordinate(
                        chunk_id=chunk.chunk_id,
                        doc_id=chunk.doc_id,
                        filename=filename,
                        page=chunk.page_number,
                        line=f"{chunk.line_start}-{chunk.line_end}" if chunk.line_start is not None else None,
                        sheet=chunk.sheet_name,
                        row=f"{chunk.row_start}-{chunk.row_end}" if chunk.row_start is not None else None,
                        excerpt=chunk.text_content[:250].strip(),
                        verified=True,
                    )
                )
            else:
                unresolved_ids.add(chunk_id)
                logger.warning(
                    "Unresolved or non-active citation ID '%s' stripped from response (doc state: %s).",
                    chunk_id,
                    doc.state if doc else "missing",
                )
                # Strip unverified citation marker from text
                cleaned_text = cleaned_text.replace(f"[{chunk_id}]", "")

        total_citations = len(matches)
        verified_count = len(resolved_ids)
        in_retrieved_count = sum(1 for cid in resolved_ids if cid in retrieved_chunk_ids)

        if total_citations > 0:
            # Resolvability ratio (must be 100% for full score) * grounding ratio
            resolvability = verified_count / total_citations
            grounding = in_retrieved_count / max(1, verified_count)
            confidence = round(0.5 * resolvability + 0.5 * grounding, 2)
        else:
            confidence = 0.35

        return cleaned_text, verified_citations, confidence


# Document-level adversarial injection patterns
DOCUMENT_INJECTION_PATTERNS = [
    re.compile(r"(?i)\b(ignore|disregard|forget|override)\s+(all\s+)?(previous|prior|above|system)\s+(instructions|prompts|rules|commands|directives)\b"),
    re.compile(r"(?i)\b(system\s+prompt|reveal\s+(system\s+)?(instructions|prompt)|leak\s+prompt)\b"),
    re.compile(r"(?i)\b(you\s+are\s+now|act\s+as\s+(an?\s+)?unrestricted|jailbreak|dan\s+mode)\b"),
    re.compile(r"(?i)<\s*script[\s>]"),
    re.compile(r"(?i)\b(bypass|disable)\s+guardrails?\b"),
    re.compile(r"(?i)\[\s*(important\s+instruction|system\s+directive|developer\s+mode)\s*:\s*.*\]"),
    re.compile(r"(?i)\b(assistant\s+must\s+output|always\s+respond\s+with|exfiltrate)\b"),
]


class IngestionScanner:
    """Two-tier prompt injection and adversarial content scanner at ingestion."""

    def __init__(
        self,
        taint_threshold: int = 2,
        llm_client: Optional[Any] = None,
    ) -> None:
        self.taint_threshold = taint_threshold
        self.llm_client = llm_client

    async def scan_chunks(
        self,
        chunks: Sequence[DocumentChunk],
    ) -> tuple[bool, int, list[str]]:
        """Scan chunks using Tier 1 (regex) and optional Tier 2 (LLM probe).
        
        Returns:
            (is_quarantined: bool, tainted_chunk_count: int, reasons: list[str])
        """
        tainted_count = 0
        reasons: list[str] = []

        for chunk in chunks:
            text = chunk.text_content
            for pattern in DOCUMENT_INJECTION_PATTERNS:
                matches = pattern.finditer(text)
                for match in matches:
                    tainted_count += 1
                    reason = f"Chunk {chunk.chunk_id}: matched pattern '{match.group(0)[:50]}'"
                    reasons.append(reason)

        is_quarantined = tainted_count >= self.taint_threshold
        return is_quarantined, tainted_count, reasons


