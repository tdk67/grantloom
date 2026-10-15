"""Explicit inference wiring: which provider serves the writer and judge roles, and with which key.

The provider is chosen by `llm.private_mode` only — never by whether a server-side key happens to
exist — so BYOK validation always reaches OpenRouter when private mode is off (review-02 §4.1):

| private_mode | writer / judge / Tier-2 / SQL | BYOK header       | data egress              |
|--------------|-------------------------------|-------------------|--------------------------|
| false        | OpenRouter                    | required, probed  | openrouter.ai            |
| true         | local Ollama                  | not used          | none (local daemon)      |

Embeddings are configured separately (`models.embedding_backend`) and reported in the egress event.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.adapters.decision_backends import (
    CorpusEvidenceDomainGuard,
    JevDecisionBackend,
    LLMJudgeDecisionBackend,
)
from app.adapters.embedder_adapter import DeterministicEmbedder, OpenRouterEmbedder
from app.adapters.ollama_client import OllamaClientAdapter
from app.adapters.openrouter_client import OpenRouterClientAdapter
from app.core.config import Settings, model_family
from app.core.errors import AuthenticationError
from app.ports.decision_backend import DecisionBackendPort
from app.ports.embedder import EmbedderPort
from app.ports.llm_client import LLMClientPort

logger = logging.getLogger("grantloom.services.inference")


class ValidatedKeyCache:
    """Remembers validated BYOK keys by SHA-256 digest only (the key itself is never stored).

    D28: validation happens once — when the user saves their key in the UI (POST
    /api/v1/auth/validate-key) or on the first request that carries it. `ttl_seconds <= 0`
    means the entry is valid for the process lifetime (an upstream 401/402 during a real
    call still surfaces immediately). The cache is FIFO-capped so a public deployment
    cannot grow it without bound.
    """

    def __init__(self, ttl_seconds: int, max_entries: int) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max(1, max_entries)
        self._expiry: dict[str, float] = {}

    @staticmethod
    def _digest(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def is_fresh(self, key: str) -> bool:
        digest = self._digest(key)
        if digest not in self._expiry:
            return False
        if self.ttl_seconds <= 0:
            return True
        return self._expiry[digest] > time.monotonic()

    def remember(self, key: str) -> None:
        digest = self._digest(key)
        self._expiry.pop(digest, None)
        self._expiry[digest] = time.monotonic() + self.ttl_seconds if self.ttl_seconds > 0 else 0.0
        while len(self._expiry) > self.max_entries:
            self._expiry.pop(next(iter(self._expiry)))


@dataclass(frozen=True)
class InferenceProfile:
    """Resolved provider + role models for one deployment mode."""

    mode: str  # "byok" or "private"
    client: LLMClientPort
    writer_model: str
    judge_model: str
    embedding_description: str
    key_cache: Optional[ValidatedKeyCache] = None

    @property
    def requires_byok_key(self) -> bool:
        return self.mode == "byok"

    async def authorize(self, byok_key: Optional[str]) -> Optional[str]:
        """FR-Q1: in BYOK mode the header is mandatory and probed live against the provider.

        Returns the key to use for inference (None in private mode, where keys are never sent anywhere).
        """
        if not self.requires_byok_key:
            return None
        if not byok_key or not byok_key.strip():
            raise AuthenticationError("Header 'X-OpenRouter-Key' is required for query inference.")
        key = byok_key.strip()
        if self.key_cache is None or not self.key_cache.is_fresh(key):
            await self.client.validate_api_key(key)
            if self.key_cache is not None:
                self.key_cache.remember(key)
        return key

    def egress(self, decision_backend: DecisionBackendPort) -> dict[str, Any]:
        """FR-Q10 egress/provenance payload streamed to the UI before any model call."""
        return {
            "mode": self.mode,
            "provider": self.client.provider,
            "destination": self.client.destination,
            "data_leaves_machine": self.mode == "byok",
            "writer_model": self.writer_model,
            "judge_model": self.judge_model,
            "decision_backend": decision_backend.name,
            "embeddings": self.embedding_description,
        }


def build_inference_profile(settings: Settings) -> InferenceProfile:
    if settings.llm.private_mode:
        client: LLMClientPort = OllamaClientAdapter(
            base_url=settings.llm.ollama_base_url,
            timeout_seconds=settings.timeouts.ollama_seconds,
            probe_timeout_seconds=settings.timeouts.probe_seconds,
            log_full_prompts=settings.logging.log_full_prompts,
        )
        return InferenceProfile(
            mode="private",
            client=client,
            writer_model=settings.llm.ollama_writer_model,
            judge_model=settings.llm.ollama_judge_model,
            embedding_description=describe_embeddings(settings),
        )
    client = OpenRouterClientAdapter(
        base_url=settings.llm.openrouter_base_url,
        timeout_seconds=settings.timeouts.openrouter_seconds,
        log_full_prompts=settings.logging.log_full_prompts,
        default_max_tokens=settings.models.max_tokens,
    )
    return InferenceProfile(
        mode="byok",
        client=client,
        writer_model=settings.models.writer_model,
        judge_model=settings.models.judge_model,
        embedding_description=describe_embeddings(settings),
        key_cache=ValidatedKeyCache(settings.llm.byok_validation_ttl_seconds, settings.llm.byok_validated_keys_max),
    )


def describe_embeddings(settings: Settings) -> str:
    if settings.models.embedding_backend == "openrouter":
        return f"openrouter:{settings.models.embedding_model} (server key)"
    return "deterministic lexical hashing (local, not semantic)"


def build_embedder(settings: Settings) -> EmbedderPort:
    """Fail fast instead of silently switching embedding spaces when the configured backend is unusable."""
    if settings.models.embedding_backend == "openrouter":
        return OpenRouterEmbedder(
            api_key=settings.secrets.openrouter_api_key or "",
            model=settings.models.embedding_model,
            base_url=settings.llm.openrouter_base_url,
            timeout_seconds=settings.timeouts.embedding_seconds,
            dimension=settings.models.embedding_dimension,
        )
    return DeterministicEmbedder(dimension=settings.models.embedding_dimension)


def build_decision_backend(
    settings: Settings,
    profile: InferenceProfile,
    corpus_overlap: Optional[Callable[[str], float]] = None,
    api_key: Optional[str] = None,
) -> DecisionBackendPort:
    """Build the configured decision backend. Only `llm_judge` and `jev` exist (D31); both are the
    same prompt-based judge pointed at a different model. A failed judge raises — no heuristic
    fallback. The corpus-evidence guard is applied per explicit config (identical in dev/Docker)."""
    cfg = settings.decision
    base_judge = LLMJudgeDecisionBackend(profile.client, profile.judge_model, cfg)
    if cfg.backend == "jev":
        key = api_key or settings.secrets.openrouter_api_key or ""
        judge: DecisionBackendPort = JevDecisionBackend(
            api_key=key,
            model=cfg.jev_model,
            base_url=cfg.jev_base_url,
            timeout_seconds=settings.timeouts.openrouter_seconds,
            config=cfg,
            judge_delegate=base_judge,
        )
    else:
        judge = base_judge
    if cfg.corpus_evidence_guard and corpus_overlap is not None:
        judge = CorpusEvidenceDomainGuard(judge, corpus_overlap, cfg.corpus_in_domain_overlap)
    return judge


def evaluator_independence_problems(writer_model: str, judge_model: str) -> list[str]:
    """NFR-6: the judge must not share a model family with the writer."""
    if writer_model == judge_model:
        return [f"judge model '{judge_model}' is identical to the writer model"]
    if model_family(writer_model) == model_family(judge_model):
        return [f"judge '{judge_model}' and writer '{writer_model}' share model family '{model_family(judge_model)}'"]
    return []
