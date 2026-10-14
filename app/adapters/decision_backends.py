"""Decision backend implementations (DecisionBackendPort).

- LLMJudgeDecisionBackend: prompt-based judge on the configured judge model (PRD §8.3
  `PromptJudgeAdapter`, the v1 default). Verdicts are JSON validated against pydantic schemas.
- HeuristicDecisionBackend: regex + lexical-overlap fallback (data-strategy §3.6 `fallback` mode).
  It is deterministic and cheap but cannot catch paraphrased injections or judge meaning.
- FallbackDecisionBackend: calls the primary and, if it fails (unreachable model, unparseable
  verdict), answers with the fallback. Every verdict carries the backend that produced it and
  each fallback increments `grantloom_decision_fallbacks_total`, so degradation is never silent.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Literal, Optional, Sequence, TypeVar

from pydantic import BaseModel, Field, ValidationError

from app.core.config import DecisionConfig, HeuristicConfig
from app.core.errors import DecisionBackendError, LLMInferenceError, UpstreamServiceError
from app.core.metrics import DECISION_FALLBACKS
from app.core.templates import load_json_template, load_template, render_template
from app.core.text import content_tokens, lexicon_hits, numbers_in, overlap_ratio
from app.ports.decision_backend import DecisionBackendPort
from app.ports.llm_client import LLMClientPort
from app.verification.injection_patterns import DOCUMENT_INJECTION_PATTERNS, INJECTION_PATTERNS
from app.ports.models import (
    ChunkJudgement,
    ClaimJudgement,
    DocumentChunk,
    GateVerdict,
    RequirementAssessment,
)

logger = logging.getLogger("grantloom.adapters.decision_backends")

LEXICON_PATH = "decision/heuristic_lexicon.json"

T = TypeVar("T")


# ---------------------------------------------------------------------------------------------
# Verdict schemas (LLM judge output)
# ---------------------------------------------------------------------------------------------

class _G1(BaseModel):
    manipulation: bool
    on_topic: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class _G2(BaseModel):
    strategy: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class _G3Item(BaseModel):
    id: Optional[str] = None
    relevance: Literal["answers", "partial", "irrelevant"]
    injection_probability: float = Field(ge=0.0, le=1.0)
    usefulness: float = Field(ge=0.0, le=100.0)


class _G3(BaseModel):
    chunks: list[_G3Item]


class _G4Item(BaseModel):
    id: Optional[str] = None
    verdict: Literal["grounded", "unsupported", "contradicted"]
    reason: str = ""


class _G4(BaseModel):
    claims: list[_G4Item]


class _T2Item(BaseModel):
    id: Optional[str] = None
    injection: bool


class _T2(BaseModel):
    chunks: list[_T2Item]


class _Compliance(BaseModel):
    status: Literal["Satisfied", "Partial", "Gap"]
    evidence_id: Optional[str] = None
    rationale: str = ""


def _parse_json(raw: str, schema: type[BaseModel]) -> Any:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise DecisionBackendError(f"Judge returned no JSON object: {raw[:200]!r}")
    try:
        return schema.model_validate(json.loads(raw[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise DecisionBackendError(f"Judge returned an invalid verdict: {exc}") from exc


def _by_id(items: Sequence[Any], expected_ids: Sequence[str]) -> dict[str, Any]:
    """Map verdict items to ids. Small local models occasionally garble an id key; when the item
    count matches exactly, positional order is used for items without a usable id."""
    found = {item.id: item for item in items if item.id in expected_ids}
    missing = [i for i in expected_ids if i not in found]
    if missing and len(items) == len(expected_ids):
        for expected, item in zip(expected_ids, items):
            if expected in missing and item.id not in expected_ids:
                found[expected] = item
        missing = [i for i in expected_ids if i not in found]
    if missing:
        raise DecisionBackendError(f"Judge verdict is missing ids: {missing}")
    return found


# ---------------------------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------------------------

class LLMJudgeDecisionBackend(DecisionBackendPort):
    """Prompt-based judge using the configured judge model (must differ in family from the writer, NFR-6)."""

    def __init__(self, llm_client: LLMClientPort, judge_model: str, config: DecisionConfig) -> None:
        self.llm_client = llm_client
        self.judge_model = judge_model
        self.config = config

    @property
    def name(self) -> str:
        return f"llm_judge:{self.judge_model}"

    @property
    def supports_semantic_scan(self) -> bool:
        return True

    def _clip(self, text: str) -> str:
        limit = self.config.llm_judge.max_chunk_chars
        return text if len(text) <= limit else text[:limit] + " [...]"

    async def _ask(self, template: str, schema: type[BaseModel], api_key: Optional[str], **values: str) -> Any:
        prompt = render_template(f"prompts/{template}", **values)
        max_attempts = getattr(self.config, "max_retries", 3)
        retry_delay = getattr(self.config, "retry_delay_seconds", 1.0)
        last_exc: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            try:
                raw = await self.llm_client.generate_response(
                    prompt=prompt,
                    model=self.judge_model,
                    system_prompt=load_template("prompts/judge_system.txt"),
                    temperature=0.0,
                    api_key=api_key,
                    json_output=True,
                )
                return _parse_json(raw, schema)
            except (LLMInferenceError, UpstreamServiceError, DecisionBackendError) as exc:
                last_exc = exc
                if attempt < max_attempts:
                    delay = retry_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "LLM judge '%s' call '%s' failed (attempt %d/%d): %s; retrying in %.1fs...",
                        self.judge_model, template, attempt, max_attempts, exc, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "LLM judge '%s' call '%s' failed after %d attempts: %s",
                        self.judge_model, template, max_attempts, exc,
                    )
                    raise DecisionBackendError(
                        f"Decision judge '{self.judge_model}' failed after {max_attempts} attempts: {exc}"
                    ) from exc
        raise DecisionBackendError(f"Decision judge '{self.judge_model}' failed after {max_attempts} attempts: {last_exc}")

    def _chunk_blocks(self, chunks: Sequence[DocumentChunk]) -> str:
        return "\n".join(
            f"[C{i}]\n<untrusted>\n{self._clip(c.text_content)}\n</untrusted>" for i, c in enumerate(chunks, start=1)
        )

    async def screen_query(self, query: str, api_key: Optional[str] = None) -> GateVerdict:
        verdict: _G1 = await self._ask("judge_g1_input.txt", _G1, api_key, query=query)
        return GateVerdict(
            gate_id="G1",
            passed=not verdict.manipulation and verdict.on_topic,
            confidence=verdict.confidence,
            reason=verdict.reason,
            metadata={"injection": verdict.manipulation, "in_domain": verdict.on_topic, "backend": self.name},
        )

    async def route_query(self, query: str, available_strategies: Sequence[str], api_key: Optional[str] = None) -> GateVerdict:
        verdict: _G2 = await self._ask(
            "judge_g2_route.txt", _G2, api_key,
            query=query, strategies="Allowed: " + ", ".join(available_strategies),
        )
        if verdict.strategy not in available_strategies:
            raise DecisionBackendError(f"Judge chose unavailable strategy '{verdict.strategy}'.")
        return GateVerdict(
            gate_id="G2",
            passed=True,
            confidence=verdict.confidence,
            reason=verdict.reason,
            metadata={"strategy": verdict.strategy, "backend": self.name},
        )

    async def judge_chunks(self, query: str, chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> list[ChunkJudgement]:
        if not chunks:
            return []
        verdict: _G3 = await self._ask("judge_g3_context.txt", _G3, api_key, query=query, chunks=self._chunk_blocks(chunks))
        found = _by_id(verdict.chunks, [f"C{i}" for i in range(1, len(chunks) + 1)])
        return [
            ChunkJudgement(
                chunk_id=c.chunk_id,
                relevance=found[f"C{i}"].relevance,
                injection_probability=found[f"C{i}"].injection_probability,
                usefulness=found[f"C{i}"].usefulness,
                backend=self.name,
            )
            for i, c in enumerate(chunks, start=1)
        ]

    async def verify_claims(self, claims: Sequence[tuple[str, Sequence[str], str]], api_key: Optional[str] = None) -> list[ClaimJudgement]:
        if not claims:
            return []
        blocks = "\n\n".join(
            f"[K{i}] Claim: {claim}\nEvidence:\n<untrusted>\n{self._clip(evidence) if evidence else '(no evidence cited)'}\n</untrusted>"
            for i, (claim, _, evidence) in enumerate(claims, start=1)
        )
        verdict: _G4 = await self._ask("judge_g4_claims.txt", _G4, api_key, claims=blocks)
        found = _by_id(verdict.claims, [f"K{i}" for i in range(1, len(claims) + 1)])
        return [
            ClaimJudgement(
                claim=claim,
                cited_chunk_ids=list(cited),
                verdict=found[f"K{i}"].verdict,
                reason=found[f"K{i}"].reason,
                backend=self.name,
            )
            for i, (claim, cited, _) in enumerate(claims, start=1)
        ]

    async def scan_for_injection(self, chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> dict[str, bool]:
        results: dict[str, bool] = {}
        batch_size = self.config.llm_judge.max_tier2_chunks_per_call
        for offset in range(0, len(chunks), batch_size):
            batch = list(chunks[offset : offset + batch_size])
            verdict: _T2 = await self._ask("judge_tier2_scan.txt", _T2, api_key, chunks=self._chunk_blocks(batch))
            found = _by_id(verdict.chunks, [f"C{i}" for i in range(1, len(batch) + 1)])
            for i, c in enumerate(batch, start=1):
                results[c.chunk_id] = found[f"C{i}"].injection
        return results

    async def assess_requirement(self, requirement_text: str, evidence_chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> RequirementAssessment:
        if not evidence_chunks:
            return RequirementAssessment("Gap", None, "No lab evidence retrieved for this requirement.", self.name)
        evidence = "\n".join(
            f"[E{i}]\n<untrusted>\n{self._clip(c.text_content)}\n</untrusted>" for i, c in enumerate(evidence_chunks, start=1)
        )
        verdict: _Compliance = await self._ask("judge_compliance.txt", _Compliance, api_key, requirement=requirement_text, evidence=evidence)
        index = {f"E{i}": c.chunk_id for i, c in enumerate(evidence_chunks, start=1)}
        evidence_chunk_id = index.get(verdict.evidence_id or "")
        if verdict.status != "Gap" and evidence_chunk_id is None:
            raise DecisionBackendError(f"Judge returned status '{verdict.status}' without a valid evidence id.")
        return RequirementAssessment(verdict.status, evidence_chunk_id, verdict.rationale, self.name)


class CorpusEvidenceDomainGuard(DecisionBackendPort):
    """Wraps a semantic backend and corrects out-of-domain REFUSALS with objective corpus evidence.

    A judge's opinion that a question is off-topic can be wrong for lab-operations detail questions
    ("what uptime did the lab achieve..."). If the indexed corpus demonstrably contains the query's
    vocabulary (best single-chunk overlap >= threshold), the refusal is overridden to in-domain —
    labelled and logged, never silent. Manipulation verdicts are never overridden. Retrieval and G3
    still decide whether the question is actually answerable (FR-Q8 abstention).
    """

    def __init__(self, inner: DecisionBackendPort, corpus_overlap: Callable[[str], float], threshold: float) -> None:
        self.inner = inner
        self.corpus_overlap = corpus_overlap
        self.threshold = threshold

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def supports_semantic_scan(self) -> bool:
        return self.inner.supports_semantic_scan

    async def route_query(self, query: str, available_strategies: Sequence[str], api_key: Optional[str] = None) -> GateVerdict:
        return await self.inner.route_query(query, available_strategies, api_key)

    async def judge_chunks(self, query: str, chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> list[ChunkJudgement]:
        return await self.inner.judge_chunks(query, chunks, api_key)

    async def verify_claims(self, claims: Sequence[tuple[str, Sequence[str], str]], api_key: Optional[str] = None) -> list[ClaimJudgement]:
        return await self.inner.verify_claims(claims, api_key)

    async def scan_for_injection(self, chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> dict[str, bool]:
        return await self.inner.scan_for_injection(chunks, api_key)

    async def assess_requirement(self, requirement_text: str, evidence_chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> RequirementAssessment:
        return await self.inner.assess_requirement(requirement_text, evidence_chunks, api_key)

    async def screen_query(self, query: str, api_key: Optional[str] = None) -> GateVerdict:
        verdict = await self.inner.screen_query(query, api_key)
        if verdict.metadata.get("in_domain", True) or verdict.metadata.get("injection"):
            return verdict
        overlap = self.corpus_overlap(query)
        if overlap >= self.threshold:
            logger.info(
                "G1 out-of-domain verdict overridden by corpus evidence (overlap %.2f >= %.2f) for query %.60r",
                overlap, self.threshold, query,
            )
            return GateVerdict(
                gate_id="G1",
                passed=not verdict.metadata.get("manipulation", False),
                confidence=round(overlap, 2),
                reason=f"corpus_evidence_override (judge said out_of_domain; corpus_overlap={overlap:.2f})",
                metadata={**verdict.metadata, "in_domain": True, "backend": f"{verdict.metadata.get('backend', self.inner.name)}+corpus_evidence"},
            )
        return verdict


# ---------------------------------------------------------------------------------------------
# Heuristic fallback
# ---------------------------------------------------------------------------------------------

class HeuristicDecisionBackend(DecisionBackendPort):
    """Deterministic regex + lexical-overlap verdicts. Scores are overlap ratios, not calibrated probabilities."""

    def __init__(self, config: Optional[HeuristicConfig] = None, corpus_overlap: Optional[Callable[[str], float]] = None) -> None:
        """corpus_overlap(query) -> best share of query terms found in one indexed chunk; lets questions
        about document content pass G1 even when they use none of the lexicon's domain vocabulary."""
        self.lexicon = load_json_template(LEXICON_PATH)
        self.config = config or HeuristicConfig()
        self.corpus_overlap = corpus_overlap

    @property
    def name(self) -> str:
        return "heuristic"

    @property
    def supports_semantic_scan(self) -> bool:
        return False

    async def screen_query(self, query: str, api_key: Optional[str] = None) -> GateVerdict:
        injection = any(p.search(query) for p in INJECTION_PATTERNS)
        hits = lexicon_hits(query, self.lexicon["domain_terms"])
        corpus = self.corpus_overlap(query) if (self.corpus_overlap and not hits) else 0.0
        if hits:
            in_domain, confidence, reason = True, min(1.0, 0.5 + 0.1 * len(hits)), f"domain_terms={hits[:5]}"
        elif corpus >= self.config.corpus_in_domain_overlap:
            in_domain, confidence, reason = True, round(corpus, 2), f"corpus_overlap={corpus:.2f}"
        else:
            # Neither domain vocabulary nor support in the indexed documents: likely off-topic.
            in_domain, confidence, reason = False, 0.7, f"no_domain_vocabulary (corpus_overlap={corpus:.2f})"
        return GateVerdict(
            gate_id="G1",
            passed=in_domain and not injection,
            confidence=confidence,
            reason=reason,
            metadata={"injection": injection, "in_domain": in_domain, "backend": self.name},
        )

    async def route_query(self, query: str, available_strategies: Sequence[str], api_key: Optional[str] = None) -> GateVerdict:
        scores = {
            strategy: len(lexicon_hits(query, terms))
            for strategy, terms in self.lexicon["route_terms"].items()
            if strategy in available_strategies
        }
        best = max(scores, key=lambda s: scores[s], default=None)
        if best is None or scores[best] == 0:
            return GateVerdict("G2", True, 0.0, "no_routing_cues", {"strategy": "hybrid", "backend": self.name})
        hits = scores[best]
        return GateVerdict(
            "G2", True, round(hits / (hits + 1), 2), f"{hits} {best} cue(s)",
            {"strategy": best, "backend": self.name},
        )

    async def judge_chunks(self, query: str, chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> list[ChunkJudgement]:
        judgements = []
        for c in chunks:
            ratio = overlap_ratio(query, c.text_content)
            relevance = "answers" if ratio >= 0.5 else "partial" if ratio > 0 else "irrelevant"
            injected = any(p.search(c.text_content) for p in DOCUMENT_INJECTION_PATTERNS)
            judgements.append(ChunkJudgement(c.chunk_id, relevance, 1.0 if injected else 0.0, round(ratio * 100, 1), self.name))
        return judgements

    async def verify_claims(self, claims: Sequence[tuple[str, Sequence[str], str]], api_key: Optional[str] = None) -> list[ClaimJudgement]:
        judgements = []
        for claim, cited, evidence in claims:
            if not evidence:
                judgements.append(ClaimJudgement(claim, list(cited), "unsupported", "no evidence cited", self.name))
                continue
            ratio = overlap_ratio(claim, evidence)
            missing_numbers = numbers_in(claim) - numbers_in(evidence)
            if missing_numbers:
                verdict, reason = "unsupported", f"numbers not in evidence: {sorted(missing_numbers)}"
            elif ratio >= self.config.grounded_overlap:
                verdict, reason = "grounded", f"lexical overlap {ratio:.2f}"
            else:
                verdict, reason = "unsupported", f"lexical overlap {ratio:.2f}"
            judgements.append(ClaimJudgement(claim, list(cited), verdict, reason, self.name))
        return judgements

    async def scan_for_injection(self, chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> dict[str, bool]:
        raise DecisionBackendError("The heuristic backend cannot perform a Tier-2 semantic scan.")

    async def assess_requirement(self, requirement_text: str, evidence_chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> RequirementAssessment:
        if not content_tokens(requirement_text) or not evidence_chunks:
            return RequirementAssessment("Gap", None, "No lab evidence retrieved for this requirement.", self.name)
        best = max(evidence_chunks, key=lambda c: overlap_ratio(requirement_text, c.text_content))
        ratio = overlap_ratio(requirement_text, best.text_content)
        if ratio >= self.config.satisfied_overlap:
            status = "Satisfied"
        elif ratio >= self.config.partial_overlap:
            status = "Partial"
        else:
            return RequirementAssessment("Gap", None, f"Best lexical overlap {ratio:.2f} is below the partial threshold.", self.name)
        return RequirementAssessment(status, best.chunk_id, f"Lexical overlap {ratio:.2f} with lab evidence.", self.name)


# ---------------------------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------------------------

class FallbackDecisionBackend(DecisionBackendPort):
    """Primary backend with a labelled, metered fallback. Auth/payment errors are never masked."""

    RECOVERABLE = (DecisionBackendError, LLMInferenceError, UpstreamServiceError)

    def __init__(self, primary: DecisionBackendPort, fallback: DecisionBackendPort) -> None:
        self.primary = primary
        self.fallback = fallback

    @property
    def name(self) -> str:
        return self.primary.name

    @property
    def supports_semantic_scan(self) -> bool:
        return self.primary.supports_semantic_scan

    @property
    def fallback_label(self) -> str:
        return f"{self.fallback.name}(fallback from {self.primary.name})"

    async def _call(self, operation: str, primary: Callable[[], Awaitable[T]], fallback: Callable[[], Awaitable[T]]) -> T:
        try:
            return await primary()
        except self.RECOVERABLE as exc:
            DECISION_FALLBACKS.labels(operation=operation).inc()
            logger.warning("Decision backend '%s' failed on %s (%s); using %s.", self.primary.name, operation, exc, self.fallback.name)
            result = await fallback()
            self._relabel(result)
            return result

    def _relabel(self, result: Any) -> None:
        items = result if isinstance(result, list) else [result]
        for item in items:
            if isinstance(item, GateVerdict):
                item.metadata["backend"] = self.fallback_label
            elif hasattr(item, "backend"):
                item.backend = self.fallback_label

    async def screen_query(self, query: str, api_key: Optional[str] = None) -> GateVerdict:
        return await self._call("screen_query", lambda: self.primary.screen_query(query, api_key), lambda: self.fallback.screen_query(query, api_key))

    async def route_query(self, query: str, available_strategies: Sequence[str], api_key: Optional[str] = None) -> GateVerdict:
        return await self._call(
            "route_query",
            lambda: self.primary.route_query(query, available_strategies, api_key),
            lambda: self.fallback.route_query(query, available_strategies, api_key),
        )

    async def judge_chunks(self, query: str, chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> list[ChunkJudgement]:
        return await self._call("judge_chunks", lambda: self.primary.judge_chunks(query, chunks, api_key), lambda: self.fallback.judge_chunks(query, chunks, api_key))

    async def verify_claims(self, claims: Sequence[tuple[str, Sequence[str], str]], api_key: Optional[str] = None) -> list[ClaimJudgement]:
        return await self._call("verify_claims", lambda: self.primary.verify_claims(claims, api_key), lambda: self.fallback.verify_claims(claims, api_key))

    async def scan_for_injection(self, chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> dict[str, bool]:
        # No fallback: a heuristic cannot stand in for the Tier-2 semantic check (the scanner records the failure).
        return await self.primary.scan_for_injection(chunks, api_key)

    async def assess_requirement(self, requirement_text: str, evidence_chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> RequirementAssessment:
        return await self._call(
            "assess_requirement",
            lambda: self.primary.assess_requirement(requirement_text, evidence_chunks, api_key),
            lambda: self.fallback.assess_requirement(requirement_text, evidence_chunks, api_key),
        )


# ---------------------------------------------------------------------------------------------
# Jev Decision Backend (Non-autoregressive discriminative model by TypeSafe AI)
# ---------------------------------------------------------------------------------------------

class JevDecisionBackend(DecisionBackendPort):
    """Jev decision backend executed over OpenRouter (TypeSafe AI classification model).

    Uses the OpenRouter client with the caller's BYOK key. Zero separate API keys or external endpoints.
    Retries up to 3 times on transient failures before raising an explicit DecisionBackendError.
    """

    def __init__(self, llm_client: LLMClientPort, model: str, config: DecisionConfig) -> None:
        self.llm_client = llm_client
        self.model = model
        self.config = config

    @property
    def name(self) -> str:
        return f"jev:{self.model}"

    @property
    def supports_semantic_scan(self) -> bool:
        return True

    def _clip(self, text: str) -> str:
        limit = self.config.llm_judge.max_chunk_chars
        return text if len(text) <= limit else text[:limit] + " [...]"

    async def _ask(self, template: str, schema: type[BaseModel], api_key: Optional[str], **values: str) -> Any:
        prompt = render_template(f"prompts/{template}", **values)
        max_attempts = getattr(self.config, "max_retries", 3)
        retry_delay = getattr(self.config, "retry_delay_seconds", 1.0)
        last_exc: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            try:
                raw = await self.llm_client.generate_response(
                    prompt=prompt,
                    model=self.model,
                    system_prompt=load_template("prompts/judge_system.txt"),
                    temperature=0.0,
                    api_key=api_key,
                    json_output=True,
                )
                return _parse_json(raw, schema)
            except (LLMInferenceError, UpstreamServiceError, DecisionBackendError) as exc:
                last_exc = exc
                if attempt < max_attempts:
                    delay = retry_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "Jev judge '%s' call '%s' failed (attempt %d/%d): %s; retrying in %.1fs...",
                        self.model, template, attempt, max_attempts, exc, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "Jev judge '%s' call '%s' failed after %d attempts: %s",
                        self.model, template, max_attempts, exc,
                    )
                    raise DecisionBackendError(
                        f"Jev decision judge '{self.model}' failed after {max_attempts} attempts: {exc}"
                    ) from exc
        raise DecisionBackendError(f"Jev decision judge '{self.model}' failed after {max_attempts} attempts: {last_exc}")

    def _chunk_blocks(self, chunks: Sequence[DocumentChunk]) -> str:
        return "\n".join(
            f"[C{i}]\n<untrusted>\n{self._clip(c.text_content)}\n</untrusted>" for i, c in enumerate(chunks, start=1)
        )

    async def screen_query(self, query: str, api_key: Optional[str] = None) -> GateVerdict:
        verdict: _G1 = await self._ask("judge_g1_input.txt", _G1, api_key, query=query)
        return GateVerdict(
            gate_id="G1",
            passed=verdict.on_topic and not verdict.manipulation,
            confidence=verdict.confidence,
            reason=verdict.reason,
            metadata={"manipulation": verdict.manipulation, "in_domain": verdict.on_topic, "backend": self.name},
        )

    async def route_query(self, query: str, available_strategies: Sequence[str], api_key: Optional[str] = None) -> GateVerdict:
        strategies_block = "\n".join(f"- {s}" for s in available_strategies)
        verdict: _G2 = await self._ask("judge_g2_route.txt", _G2, api_key, query=query, strategies=strategies_block)
        return GateVerdict(
            gate_id="G2",
            passed=True,
            confidence=verdict.confidence,
            reason=verdict.reason,
            metadata={"strategy": verdict.strategy, "backend": self.name},
        )

    async def judge_chunks(self, query: str, chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> list[ChunkJudgement]:
        if not chunks:
            return []
        verdict: _G3 = await self._ask("judge_g3_context.txt", _G3, api_key, query=query, chunks=self._chunk_blocks(chunks))
        found = _by_id(verdict.chunks, [f"C{i}" for i in range(1, len(chunks) + 1)])
        return [
            ChunkJudgement(
                chunk_id=c.chunk_id,
                relevance=found[f"C{i}"].relevance,
                injection_probability=found[f"C{i}"].injection_probability,
                usefulness=found[f"C{i}"].usefulness,
                backend=self.name,
            )
            for i, c in enumerate(chunks, start=1)
        ]

    async def verify_claims(self, claims: Sequence[tuple[str, Sequence[str], str]], api_key: Optional[str] = None) -> list[ClaimJudgement]:
        if not claims:
            return []
        blocks = "\n\n".join(
            f"[K{i}] Claim: {claim}\nEvidence:\n<untrusted>\n{self._clip(evidence) if evidence else '(no evidence cited)'}\n</untrusted>"
            for i, (claim, _, evidence) in enumerate(claims, start=1)
        )
        verdict: _G4 = await self._ask("judge_g4_claims.txt", _G4, api_key, claims=blocks)
        found = _by_id(verdict.claims, [f"K{i}" for i in range(1, len(claims) + 1)])
        return [
            ClaimJudgement(
                claim=claim,
                cited_chunk_ids=list(cited),
                verdict=found[f"K{i}"].verdict,
                reason=found[f"K{i}"].reason,
                backend=self.name,
            )
            for i, (claim, cited, _) in enumerate(claims, start=1)
        ]

    async def scan_for_injection(self, chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> dict[str, bool]:
        results: dict[str, bool] = {}
        batch_size = self.config.llm_judge.max_tier2_chunks_per_call
        for offset in range(0, len(chunks), batch_size):
            batch = list(chunks[offset : offset + batch_size])
            verdict: _T2 = await self._ask("judge_tier2_scan.txt", _T2, api_key, chunks=self._chunk_blocks(batch))
            found = _by_id(verdict.chunks, [f"C{i}" for i in range(1, len(batch) + 1)])
            for i, c in enumerate(batch, start=1):
                results[c.chunk_id] = found[f"C{i}"].injection
        return results

    async def assess_requirement(self, requirement_text: str, evidence_chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> RequirementAssessment:
        if not evidence_chunks:
            return RequirementAssessment("Gap", None, "No lab evidence retrieved for this requirement.", self.name)
        evidence = "\n".join(
            f"[E{i}]\n<untrusted>\n{self._clip(c.text_content)}\n</untrusted>" for i, c in enumerate(evidence_chunks, start=1)
        )
        verdict: _Compliance = await self._ask("judge_compliance.txt", _Compliance, api_key, requirement=requirement_text, evidence=evidence)
        return RequirementAssessment(verdict.status, verdict.evidence_id, verdict.rationale, self.name)


