"""Guardrail gates (PRD §8.3) built on the DecisionBackendPort.

G1 InputGuard      regex prefilter (language, canonical injections) + backend semantic screen
                   (paraphrased injections, domain relevance — FR-Q2).
G2 StrategyRouter  backend routing with a configured confidence floor; below it -> hybrid.
G3 ContextFilter   drops ingestion-tainted / injection-flagged / irrelevant chunks and reranks.
G4 OutputGuard     resolves [chunk_id] markers against the registry (in-context, active docs only)
                   and verifies each claim against its cited evidence via the backend.
IngestionScanner   FR-I5 two-tier scan, counted per chunk; Tier 2 uses the backend's semantic check.
"""

from __future__ import annotations

import logging
import math
import random
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from app.core.config import DecisionConfig, GuardrailsConfig, RetrievalConfig
from app.core.errors import GrantLoomError
from app.core.metrics import GUARD_VERDICTS, ROUTER_DECISIONS
from app.core.templates import load_template
from app.core.text import content_tokens, first_match, split_sentences
from app.ports.decision_backend import DecisionBackendPort
from app.ports.models import ChunkJudgement, CitationCoordinate, ClaimJudgement, DocumentChunk, GateVerdict, ScanReport
from app.ports.registry import RegistryPort
from app.verification.injection_patterns import DOCUMENT_INJECTION_PATTERNS, INJECTION_PATTERNS

logger = logging.getLogger("grantloom.services.guardrails")

# Non-English Unicode ranges (Cyrillic, CJK, Kana, Hangul, Arabic, Devanagari, Hebrew, Thai)
NON_ENGLISH_SCRIPTS_RE = re.compile(
    r"[Ѐ-ӿ一-鿿぀-ヿ가-힯؀-ۿऀ-ॿ֐-׿฀-๿]"
)

# Citation marker pattern: [chunk_id]
CITATION_PATTERN = re.compile(r"\[([a-zA-Z0-9_\-:]+)\]")

STRATEGIES = ("hybrid", "semantic", "keyword", "structured", "graph", "composite")


def _record(verdict: GateVerdict) -> GateVerdict:
    GUARD_VERDICTS.labels(
        gate=verdict.gate_id,
        outcome="pass" if verdict.passed else verdict.reason,
        backend=str(verdict.metadata.get("backend", "regex")),
    ).inc()
    return verdict


def _refusal(template: str, reason: str, confidence: float, backend: str) -> GateVerdict:
    message = load_template(template)
    return _record(GateVerdict(
        gate_id="G1",
        passed=False,
        confidence=confidence,
        reason=reason,
        metadata={"refusal": message, "message": message, "backend": backend},
    ))


class InputGuard:
    """G1 Input Guard: language check, injection prefilter, semantic injection + domain screen."""

    def __init__(self, decision_backend: DecisionBackendPort, guardrails: GuardrailsConfig, decision: DecisionConfig) -> None:
        self.decision_backend = decision_backend
        self.max_query_chars = guardrails.max_query_chars
        self.domain_min_confidence = decision.domain_min_confidence

    async def evaluate(self, query: str, api_key: Optional[str] = None) -> GateVerdict:
        cleaned = query.strip()
        if not cleaned:
            return _record(GateVerdict("G1", False, 1.0, "empty_query", {"message": "Query cannot be empty.", "backend": "regex"}))
        if len(cleaned) > self.max_query_chars:
            return _record(GateVerdict(
                "G1", False, 1.0, "query_too_long",
                {"refusal": f"Queries are limited to {self.max_query_chars} characters.", "backend": "regex"},
            ))

        # 1. Fast language check (no model call)
        if NON_ENGLISH_SCRIPTS_RE.search(cleaned):
            return _refusal("refusals/non_english.txt", "non_english", 1.0, "regex")

        # 2. Canonical injection prefilter (no model call)
        if first_match(INJECTION_PATTERNS, cleaned):
            return _refusal("refusals/prompt_injection.txt", "prompt_injection_detected", 0.99, "regex")

        # 3. Semantic screen: paraphrased injections and domain relevance
        screen = await self.decision_backend.screen_query(cleaned, api_key)
        backend = str(screen.metadata.get("backend", self.decision_backend.name))
        if screen.metadata.get("injection"):
            return _refusal("refusals/prompt_injection.txt", "prompt_injection_detected", screen.confidence, backend)
        if not screen.metadata.get("in_domain", True) and screen.confidence >= self.domain_min_confidence:
            return _refusal("refusals/out_of_domain.txt", "out_of_domain", screen.confidence, backend)

        return _record(GateVerdict(
            gate_id="G1",
            passed=True,
            confidence=screen.confidence,
            reason="input_guard_passed",
            metadata={"sanitized_query": cleaned, "backend": backend, "screen_reason": screen.reason},
        ))


class StrategyRouter:
    """G2 Strategy Router: backend routing, falling back to hybrid below the confidence floor."""

    def __init__(self, decision_backend: DecisionBackendPort, decision: DecisionConfig) -> None:
        self.decision_backend = decision_backend
        self.min_confidence = decision.router_min_confidence

    async def evaluate(self, query: str, available_strategies: Sequence[str], api_key: Optional[str] = None) -> GateVerdict:
        verdict = await self.decision_backend.route_query(query, available_strategies, api_key)
        backend = str(verdict.metadata.get("backend", self.decision_backend.name))
        proposed = verdict.metadata.get("strategy", "hybrid")
        if proposed != "hybrid" and verdict.confidence < self.min_confidence:
            verdict = GateVerdict(
                "G2", True, verdict.confidence,
                f"low_confidence_fallback ({proposed} at {verdict.confidence:.2f} < {self.min_confidence})",
                {"strategy": "hybrid", "proposed": proposed, "backend": backend},
            )
        ROUTER_DECISIONS.labels(strategy=verdict.metadata["strategy"], backend=backend).inc()
        return verdict


@dataclass
class ContextFilterResult:
    chunks: list[DocumentChunk]
    dropped: list[dict[str, str]] = field(default_factory=list)
    backend: str = ""


class ContextFilter:
    """G3: drop tainted/injected/irrelevant chunks, rerank by verdict, truncate to top_k_after_filter."""

    RELEVANCE_RANK = {"answers": 2, "partial": 1, "irrelevant": 0}

    def __init__(self, decision_backend: DecisionBackendPort, decision: DecisionConfig, retrieval: RetrievalConfig) -> None:
        self.decision_backend = decision_backend
        self.injection_threshold = decision.injection_probability_threshold
        self.min_usefulness = decision.min_chunk_usefulness
        self.min_dense = retrieval.min_relevance_threshold
        self.top_k = retrieval.top_k_after_filter

    async def filter(
        self,
        query: str,
        chunks: Sequence[DocumentChunk],
        dense_scores: dict[str, float],
        api_key: Optional[str] = None,
    ) -> ContextFilterResult:
        dropped: list[dict[str, str]] = []
        candidates: list[DocumentChunk] = []
        for c in chunks:
            if c.metadata.get("tainted"):
                dropped.append({"chunk_id": c.chunk_id, "reason": "tainted_at_ingestion"})
            else:
                candidates.append(c)
        if not candidates:
            return ContextFilterResult([], dropped, self.decision_backend.name)

        judgements = await self.decision_backend.judge_chunks(query, candidates, api_key)
        backend = judgements[0].backend if judgements else self.decision_backend.name
        kept: list[tuple[DocumentChunk, ChunkJudgement]] = []
        for c, j in zip(candidates, judgements):
            dense = dense_scores.get(c.chunk_id, 0.0)
            if j.injection_probability > self.injection_threshold:
                dropped.append({"chunk_id": c.chunk_id, "reason": f"injection_probability={j.injection_probability:.2f}"})
            elif j.relevance == "irrelevant" and dense < self.min_dense:
                dropped.append({"chunk_id": c.chunk_id, "reason": "irrelevant"})
            elif j.relevance != "answers" and j.usefulness < self.min_usefulness and dense < self.min_dense:
                dropped.append({"chunk_id": c.chunk_id, "reason": f"usefulness={j.usefulness:.0f}"})
            else:
                kept.append((c, j))

        kept.sort(
            key=lambda pair: (self.RELEVANCE_RANK[pair[1].relevance], pair[1].usefulness, dense_scores.get(pair[0].chunk_id, 0.0)),
            reverse=True,
        )
        for c, _ in kept[self.top_k :]:
            dropped.append({"chunk_id": c.chunk_id, "reason": "below_top_k"})
        return ContextFilterResult([c for c, _ in kept[: self.top_k]], dropped, backend)

    def screen_tool_evidence(self, chunks: Sequence[DocumentChunk]) -> tuple[list[DocumentChunk], list[dict[str, str]]]:
        """Tool-selected provenance chunks skip relevance ranking but never injection screening."""
        kept, dropped = [], []
        for c in chunks:
            if c.metadata.get("tainted") or first_match(DOCUMENT_INJECTION_PATTERNS, c.text_content):
                dropped.append({"chunk_id": c.chunk_id, "reason": "injection_in_tool_evidence"})
            else:
                kept.append(c)
        return kept, dropped


@dataclass
class Claim:
    text: str
    cited_chunk_ids: list[str]


@dataclass
class VerificationResult:
    """G4 outcome for one draft."""

    cleaned_text: str
    citations: list[CitationCoordinate]
    unresolved_ids: list[str]
    claims: list[ClaimJudgement]
    resolvability: float
    faithfulness: float
    backend: str

    @property
    def unsupported_claims(self) -> list[ClaimJudgement]:
        return [c for c in self.claims if c.verdict != "grounded"]

    @property
    def passed(self) -> bool:
        return bool(self.citations) and bool(self.claims) and not self.unresolved_ids and not self.unsupported_claims

    @property
    def confidence_score(self) -> float:
        return round(self.resolvability * self.faithfulness, 2)

    def issues(self) -> list[str]:
        issues = []
        if not self.citations:
            issues.append("- The draft contains no resolvable [chunk_id] citations.")
        elif not self.claims:
            issues.append("- The draft states no answer: write the answer in full sentences, each followed by its [chunk_id].")
        for cid in self.unresolved_ids:
            issues.append(f"- Citation [{cid}] does not match any chunk in the provided context.")
        for claim in self.unsupported_claims:
            issues.append(f"- {claim.verdict.upper()}: \"{claim.claim}\" ({claim.reason})")
        return issues


class OutputGuard:
    """G4 Output Guard: citation resolution to physical coordinates plus per-claim verification."""

    MIN_CLAIM_TOKENS = 3

    def __init__(self, registry: RegistryPort, decision_backend: DecisionBackendPort, citation_excerpt_chars: int) -> None:
        self.registry = registry
        self.decision_backend = decision_backend
        self.citation_excerpt_chars = citation_excerpt_chars

    def resolve_citations(
        self,
        draft_text: str,
        context_chunk_ids: Sequence[str],
    ) -> tuple[str, list[CitationCoordinate], list[str]]:
        """Resolve markers that point at in-context chunks of active documents; strip all others."""
        context = set(context_chunk_ids)
        verified: list[CitationCoordinate] = []
        unresolved: list[str] = []
        cleaned_text = draft_text

        for chunk_id in dict.fromkeys(CITATION_PATTERN.findall(draft_text)):
            chunk = self.registry.get_chunk(chunk_id) if chunk_id in context else None
            doc = self.registry.get_document(chunk.doc_id) if chunk else None
            if chunk is not None and doc is not None and doc.state == "active":
                verified.append(
                    CitationCoordinate(
                        chunk_id=chunk.chunk_id,
                        doc_id=chunk.doc_id,
                        filename=doc.filename,
                        page=chunk.page_number,
                        line=f"{chunk.line_start}-{chunk.line_end}" if chunk.line_start is not None else None,
                        sheet=chunk.sheet_name,
                        row=f"{chunk.row_start}-{chunk.row_end}" if chunk.row_start is not None else None,
                        excerpt=chunk.text_content[: self.citation_excerpt_chars].strip(),
                        verified=True,
                    )
                )
            else:
                unresolved.append(chunk_id)
                logger.warning("Citation '%s' stripped (not in context or document not active).", chunk_id)
                cleaned_text = cleaned_text.replace(f"[{chunk_id}]", "")
        return cleaned_text, verified, unresolved

    def extract_claims(self, draft_text: str, resolved_ids: Sequence[str]) -> list[Claim]:
        """Sentences with factual content; a sentence without its own markers is checked against all citations."""
        claims = []
        for sentence in split_sentences(draft_text):
            cited = [cid for cid in CITATION_PATTERN.findall(sentence) if cid in resolved_ids]
            text = CITATION_PATTERN.sub("", sentence).strip()
            if len(content_tokens(text)) < self.MIN_CLAIM_TOKENS:
                continue
            claims.append(Claim(text=text, cited_chunk_ids=cited or list(resolved_ids)))
        return claims

    async def verify(
        self,
        draft_text: str,
        context_chunk_ids: Sequence[str],
        tool_evidence: Sequence[str] = (),
        api_key: Optional[str] = None,
    ) -> VerificationResult:
        cleaned_text, citations, unresolved = self.resolve_citations(draft_text, context_chunk_ids)
        total_markers = len(citations) + len(unresolved)
        resolvability = len(citations) / total_markers if total_markers else 0.0
        resolved_ids = [c.chunk_id for c in citations]

        claims = self.extract_claims(draft_text, resolved_ids)
        texts = {}
        for cid in resolved_ids:
            chunk = self.registry.get_chunk(cid)
            texts[cid] = chunk.text_content if chunk else ""
        extra = "\n".join(tool_evidence)
        payload = [
            (c.text, c.cited_chunk_ids, "\n".join(texts.get(cid, "") for cid in c.cited_chunk_ids) + ("\n" + extra if extra else ""))
            for c in claims
        ]
        judgements = await self.decision_backend.verify_claims(payload, api_key) if payload else []
        grounded = sum(1 for j in judgements if j.verdict == "grounded")
        faithfulness = grounded / len(judgements) if judgements else 0.0
        backend = judgements[0].backend if judgements else self.decision_backend.name
        GUARD_VERDICTS.labels(gate="G4", outcome="pass" if (citations and grounded == len(judgements) and not unresolved) else "fail", backend=backend).inc()
        return VerificationResult(
            cleaned_text=cleaned_text,
            citations=citations,
            unresolved_ids=unresolved,
            claims=judgements,
            resolvability=round(resolvability, 3),
            faithfulness=round(faithfulness, 3),
            backend=backend,
        )


class IngestionScanner:
    """FR-I5 two-tier scan, counted per chunk.

    Tier 1: regex over 100% of chunks. Tier 2: the decision backend's semantic check over every
    Tier-1 flagged chunk plus a deterministic random sample (guardrails.tier2_sample_rate).
    With Tier 2 available, a chunk is tainted when Tier 2 says so (it can clear regex false
    positives and catch paraphrased injections in the sample). A document is quarantined when
    tainted chunks exceed guardrails.taint_threshold_chunks.
    """

    def __init__(self, decision_backend: DecisionBackendPort, guardrails: GuardrailsConfig) -> None:
        self.decision_backend = decision_backend
        self.taint_threshold = guardrails.taint_threshold_chunks
        self.sample_rate = guardrails.tier2_sample_rate

    async def scan(
        self,
        doc_id: str,
        chunks: Sequence[DocumentChunk],
        api_key: Optional[str] = None,
        tier2_unavailable_reason: Optional[str] = None,
    ) -> ScanReport:
        reasons: list[str] = []
        flagged: list[DocumentChunk] = []
        for c in chunks:
            hit = first_match(DOCUMENT_INJECTION_PATTERNS, c.text_content)
            if hit:
                flagged.append(c)
                reasons.append(f"tier1 {c.chunk_id}: '{hit[:60]}'")

        flagged_ids = [c.chunk_id for c in flagged]
        unflagged = [c for c in chunks if c.chunk_id not in set(flagged_ids)]
        sample_size = math.floor(len(unflagged) * self.sample_rate)
        sample = random.Random(doc_id).sample(unflagged, sample_size) if sample_size else []

        if not self.decision_backend.supports_semantic_scan:
            tier2_status = f"skipped: decision backend '{self.decision_backend.name}' has no semantic scan"
            tainted = flagged_ids
        elif tier2_unavailable_reason:
            tier2_status = f"skipped: {tier2_unavailable_reason}"
            tainted = flagged_ids
        else:
            try:
                verdicts = await self.decision_backend.scan_for_injection(flagged + sample, api_key)
                tainted = [cid for cid, injected in verdicts.items() if injected]
                tier2_status = f"completed: {len(flagged) + len(sample)} chunk(s) via {self.decision_backend.name}"
                for cid in flagged_ids:
                    if cid not in tainted:
                        reasons.append(f"tier2 cleared {cid}")
                for c in sample:
                    if c.chunk_id in tainted:
                        reasons.append(f"tier2 sample flagged {c.chunk_id}")
            except GrantLoomError as exc:
                tier2_status = f"failed: {exc.detail}"
                tainted = flagged_ids
                logger.warning("Tier-2 scan for '%s' failed; using Tier-1 result only: %s", doc_id, exc)

        return ScanReport(
            quarantined=len(tainted) > self.taint_threshold,
            tainted_chunk_ids=tainted,
            tier1_flagged_chunk_ids=flagged_ids,
            tier2_status=tier2_status,
            reasons=reasons,
        )


def gate_event(verdict: GateVerdict, **extra: Any) -> dict[str, Any]:
    """Common SSE payload for gate verdicts (includes the backend that decided)."""
    payload = {
        "gate": verdict.gate_id,
        "passed": verdict.passed,
        "confidence": round(verdict.confidence, 3),
        "reason": verdict.reason,
        "backend": verdict.metadata.get("backend", ""),
    }
    payload.update(extra)
    return payload
