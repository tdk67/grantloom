"""Per-step latency profiling and telemetry for RAG pipelines."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class StepTiming:
    step_name: str
    duration_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)


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


class StageTimer:
    """Context manager to measure milliseconds of a pipeline stage."""

    def __init__(self) -> None:
        self.start_time: float = 0.0
        self.elapsed_ms: float = 0.0

    def __enter__(self) -> "StageTimer":
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.elapsed_ms = (time.perf_counter() - self.start_time) * 1000.0
