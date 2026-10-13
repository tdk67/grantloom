"""Abstract Decision Backend Port interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.ports.models import GateVerdict


class DecisionBackendPort(ABC):
    """Abstract port for G1-G4 guardrail evaluations and prompt judge decisions."""

    @abstractmethod
    async def evaluate_gate(
        self,
        gate_id: str,
        input_payload: dict[str, Any],
    ) -> GateVerdict:
        """Evaluate input against gate policies and return a structured verdict."""
        ...
