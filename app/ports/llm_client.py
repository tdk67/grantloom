"""Abstract LLM Client Port interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional


class LLMClientPort(ABC):
    """Abstract port for BYOK OpenRouter and local Ollama inference."""

    @abstractmethod
    async def validate_api_key(self, api_key: str) -> bool:
        """Validate API key against provider upstream auth probe."""
        ...

    @abstractmethod
    async def generate_response(
        self,
        prompt: str,
        model: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        api_key: Optional[str] = None,
    ) -> str:
        """Execute a single-turn completion."""
        ...

    @abstractmethod
    def stream_response(
        self,
        prompt: str,
        model: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        api_key: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Stream generated tokens over an async iterator."""
        ...
