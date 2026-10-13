"""Abstract LLM Client Port interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional


class LLMClientPort(ABC):
    """Abstract port for BYOK OpenRouter and local Ollama inference."""

    @property
    @abstractmethod
    def provider(self) -> str:
        """Provider identifier ('openrouter', 'ollama', or a test double name)."""
        ...

    @property
    @abstractmethod
    def destination(self) -> str:
        """Host that prompts are sent to (shown to users as the data-egress destination, FR-Q10)."""
        ...

    @abstractmethod
    async def validate_api_key(self, api_key: str) -> bool:
        """Validate a BYOK key against the provider's auth probe (raises on invalid / exhausted keys)."""
        ...

    @abstractmethod
    async def generate_response(
        self,
        prompt: str,
        model: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        api_key: Optional[str] = None,
        json_output: bool = False,
    ) -> str:
        """Execute a single-turn completion (json_output=True asks the provider for a JSON object)."""
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

    @abstractmethod
    async def describe_model(self, model: str, api_key: Optional[str] = None) -> dict[str, Any]:
        """Provider-reported model facts: {'found', 'tool_calling', 'context_length'}."""
        ...
