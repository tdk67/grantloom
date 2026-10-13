"""Abstract Decision Backend Port: typed verdicts for gates G1-G4, Tier-2 scans, and compliance.

Implementations (app/adapters/decision_backends.py):
- LLMJudgeDecisionBackend: prompt-based judge (PRD §8.3 `PromptJudgeAdapter`, v1 default).
- HeuristicDecisionBackend: regex/lexical fallback (data-strategy §3.6 `fallback` mode).
- FallbackDecisionBackend: primary -> fallback chain that labels every verdict with the backend used.
Jev and Julia-1 are deferred (decisions-log D22); they would be further implementations of this port.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Sequence

from app.ports.models import (
    ChunkJudgement,
    ClaimJudgement,
    DocumentChunk,
    GateVerdict,
    RequirementAssessment,
)


class DecisionBackendPort(ABC):
    """Typed-verdict decision backend used by the guardrail gates."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend identifier reported in SSE events, metrics, and document metadata."""
        ...

    @property
    @abstractmethod
    def supports_semantic_scan(self) -> bool:
        """Whether the backend can perform the FR-I5 Tier-2 (model-based) injection check."""
        ...

    @abstractmethod
    async def screen_query(self, query: str, api_key: Optional[str] = None) -> GateVerdict:
        """G1 semantic screen. metadata: injection (bool), in_domain (bool), backend (str)."""
        ...

    @abstractmethod
    async def route_query(
        self,
        query: str,
        available_strategies: Sequence[str],
        api_key: Optional[str] = None,
    ) -> GateVerdict:
        """G2 routing. metadata: strategy (one of available_strategies), backend (str)."""
        ...

    @abstractmethod
    async def judge_chunks(
        self,
        query: str,
        chunks: Sequence[DocumentChunk],
        api_key: Optional[str] = None,
    ) -> list[ChunkJudgement]:
        """G3 per-chunk relevance / injection / usefulness verdicts (same order as chunks)."""
        ...

    @abstractmethod
    async def verify_claims(
        self,
        claims: Sequence[tuple[str, Sequence[str], str]],
        api_key: Optional[str] = None,
    ) -> list[ClaimJudgement]:
        """G4 claim verification. Each claim is (claim_text, cited_chunk_ids, evidence_text)."""
        ...

    @abstractmethod
    async def scan_for_injection(
        self,
        chunks: Sequence[DocumentChunk],
        api_key: Optional[str] = None,
    ) -> dict[str, bool]:
        """FR-I5 Tier-2 check: chunk_id -> contains instructions aimed at the model."""
        ...

    @abstractmethod
    async def assess_requirement(
        self,
        requirement_text: str,
        evidence_chunks: Sequence[DocumentChunk],
        api_key: Optional[str] = None,
    ) -> RequirementAssessment:
        """FR-C2 classification of lab evidence against one solicitation requirement."""
        ...
