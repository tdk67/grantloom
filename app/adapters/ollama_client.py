"""Ollama local LLM client adapter for private-mode (offline) inference."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlparse

import httpx

from app.core.errors import AuthenticationError, LLMInferenceError, UpstreamServiceError
from app.ports.llm_client import LLMClientPort

logger = logging.getLogger("grantloom.adapters.ollama_client")


class OllamaClientAdapter(LLMClientPort):
    """Client for the local Ollama daemon. Ollama has no API keys; BYOK keys are never sent here."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        probe_timeout_seconds: float,
        log_full_prompts: bool,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.probe_timeout_seconds = probe_timeout_seconds
        self.log_full_prompts = log_full_prompts

    @property
    def provider(self) -> str:
        return "ollama"

    @property
    def destination(self) -> str:
        return urlparse(self.base_url).netloc or self.base_url

    async def validate_api_key(self, api_key: str) -> bool:
        """Private mode takes no BYOK key; dispatching key validation here is a wiring error."""
        raise AuthenticationError(
            "BYOK key validation is not applicable to the local Ollama backend (private mode)."
        )

    async def check_reachable(self) -> None:
        """Raise UpstreamServiceError if the Ollama daemon does not answer /api/tags."""
        try:
            async with httpx.AsyncClient(timeout=self.probe_timeout_seconds) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
        except httpx.RequestError as exc:
            raise UpstreamServiceError(f"Cannot connect to local Ollama daemon at {self.base_url}: {exc}") from exc
        if resp.status_code != 200:
            raise UpstreamServiceError(f"Ollama daemon returned HTTP {resp.status_code}")

    def _log_call(self, model: str, prompt: str, system_prompt: Optional[str]) -> None:
        logger.info("Ollama call model=%s prompt_chars=%d", model, len(prompt) + len(system_prompt or ""))
        if self.log_full_prompts:
            logger.info("Ollama prompt (model=%s):\nSYSTEM: %s\nUSER: %s", model, system_prompt or "", prompt)

    @staticmethod
    def _messages(prompt: str, system_prompt: Optional[str]) -> list[dict[str, str]]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages

    async def generate_response(
        self,
        prompt: str,
        model: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        api_key: Optional[str] = None,
        json_output: bool = False,
    ) -> str:
        """Generate complete response via Ollama chat API."""
        self._log_call(model, prompt, system_prompt)
        payload = {
            "model": model,
            "messages": self._messages(prompt, system_prompt),
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_output:
            payload["format"] = "json"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.RequestError as exc:
            raise LLMInferenceError(f"Ollama request error: {exc}") from exc
        if resp.status_code != 200:
            raise LLMInferenceError(f"Ollama chat failed with HTTP {resp.status_code}: {resp.text}")
        return resp.json().get("message", {}).get("content", "")

    async def stream_response(
        self,
        prompt: str,
        model: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        api_key: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Stream generated tokens via Ollama chat API (NDJSON)."""
        self._log_call(model, prompt, system_prompt)
        payload = {
            "model": model,
            "messages": self._messages(prompt, system_prompt),
            "stream": True,
            "options": {"temperature": temperature},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as stream_resp:
                    if stream_resp.status_code != 200:
                        body = await stream_resp.aread()
                        raise LLMInferenceError(
                            f"Ollama stream failed with HTTP {stream_resp.status_code}: {body.decode(errors='replace')}"
                        )
                    async for line in stream_resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            token = json.loads(line).get("message", {}).get("content", "")
                        except json.JSONDecodeError:
                            continue
                        if token:
                            yield token
        except httpx.RequestError as exc:
            raise LLMInferenceError(f"Ollama stream error: {exc}") from exc

    async def describe_model(self, model: str, api_key: Optional[str] = None) -> dict[str, Any]:
        """Model facts reported by Ollama's /api/show (capabilities list and context length)."""
        try:
            async with httpx.AsyncClient(timeout=self.probe_timeout_seconds) as client:
                resp = await client.post(f"{self.base_url}/api/show", json={"model": model})
        except httpx.RequestError as exc:
            raise UpstreamServiceError(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc
        if resp.status_code == 404:
            return {"found": False, "tool_calling": False, "context_length": None}
        if resp.status_code != 200:
            raise UpstreamServiceError(f"Ollama /api/show returned HTTP {resp.status_code}")
        data = resp.json()
        context_length = next(
            (v for k, v in data.get("model_info", {}).items() if k.endswith(".context_length")),
            None,
        )
        return {
            "found": True,
            "tool_calling": "tools" in (data.get("capabilities") or []),
            "context_length": context_length,
        }
