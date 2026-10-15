"""Per-step latency profiling and telemetry for RAG pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineLatencyProfile:
    """Detailed per-step timing breakdown for an end-to-end RAG query."""
    guard_ms: float = 0.0
    route_ms: float = 0.0
    retrieve_ms: float = 0.0
    filter_ms: float = 0.0
    first_token_ms: float = 0.0
    generate_ms: float = 0.0
    verify_ms: float = 0.0
    total_ms: float = 0.0
    tool_timings: dict[str, float] = field(default_factory=dict)
    tokens_per_second: float = 0.0
    token_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "guard_ms": round(self.guard_ms, 1),
            "route_ms": round(self.route_ms, 1),
            "retrieve_ms": round(self.retrieve_ms, 1),
            "filter_ms": round(self.filter_ms, 1),
            "first_token_ms": round(self.first_token_ms, 1),
            "generate_ms": round(self.generate_ms, 1),
            "verify_ms": round(self.verify_ms, 1),
            "total_ms": round(self.total_ms, 1),
            "tool_timings": {k: round(v, 1) for k, v in self.tool_timings.items()},
            "tokens_per_second": round(self.tokens_per_second, 1),
            "token_count": self.token_count,
        }
