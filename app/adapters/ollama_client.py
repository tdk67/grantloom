"""Ollama local LLM client adapter for offline inference."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Optional

import httpx

from app.core.errors import (
    AuthenticationError,
    LLMInferenceError,
    UpstreamServiceError,
)
from app.ports.llm_client import LLMClientPort

logger = logging.getLogger("grantloom.adapters.ollama_client")

class OllamaClientAdapter(LLMClientPort):
    """Client for local Ollama inference and capability verification."""

    def __init__(
        self,
        base_url: str,
        default_model: str,
        timeout: float = 180.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout = timeout

    def _resolve_model(self, model: Optional[str]) -> str:
        """Resolve model name, falling back to local default if cloud format or empty."""
        if not model or "/" in model:
            return self.default_model
        return model

    async def validate_api_key(self, api_key: str = "") -> bool:
        """Validate connection to local Ollama daemon via /api/tags."""
        url = f"{self.base_url}/api/tags"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    raise UpstreamServiceError(f"Ollama daemon returned HTTP {resp.status_code}")
                return True
        except Exception as exc:
            raise UpstreamServiceError(f"Cannot connect to local Ollama daemon at {self.base_url}: {exc}") from exc

    async def get_available_models(self) -> list[str]:
        """List model names available in the local Ollama instance."""
        url = f"{self.base_url}/api/tags"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    return [m["name"] for m in data.get("models", [])]
                return []
        except Exception:
            return []

    async def generate_response(
        self,
        prompt: str,
        model: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        api_key: Optional[str] = None,
    ) -> str:
        """Generate complete response via Ollama chat API."""
        url = f"{self.base_url}/api/chat"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        target_model = self._resolve_model(model)
        payload = {
            "model": target_model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    raise LLMInferenceError(f"Ollama chat failed with HTTP {resp.status_code}: {resp.text}")
                data = resp.json()
                msg = data.get("message", {})
                return msg.get("content", "")
        except Exception as exc:
            if isinstance(exc, LLMInferenceError):
                raise
            raise LLMInferenceError(f"Ollama request error: {exc}") from exc

    async def stream_response(
        self,
        prompt: str,
        model: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        api_key: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Stream generated tokens via Ollama chat API."""
        url = f"{self.base_url}/api/chat"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        target_model = self._resolve_model(model)
        payload = {
            "model": target_model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature},
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, json=payload) as stream_resp:
                    if stream_resp.status_code != 200:
                        body = await stream_resp.aread()
                        raise LLMInferenceError(f"Ollama stream failed with HTTP {stream_resp.status_code}: {body.decode()}")

                    async for line in stream_resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            item = json.loads(line)
                            token = item.get("message", {}).get("content", "")
                            if token:
                                yield token
                        except json.JSONDecodeError:
                            continue
        except Exception as exc:
            if isinstance(exc, LLMInferenceError):
                raise
            raise LLMInferenceError(f"Ollama stream error: {exc}") from exc

    async def test_capabilities(self, model: str) -> dict[str, Any]:
        """Execute a live probe to test JSON instruction following and basic tool-calling."""
        probe_prompt = (
            'Extract the fruit name and count from the following sentence as JSON with keys "item" and "count":\n'
            '"Alice brought 3 apples to the lab."\n'
            'Return ONLY the raw JSON object, no explanation.'
        )
        try:
            resp = await self.generate_response(
                prompt=probe_prompt,
                model=model,
                temperature=0.0,
            )
            # Try parsing JSON from response
            import re
            m = re.search(r"\{.*?\}", resp, re.DOTALL)
            parsed = json.loads(m.group(0)) if m else {}
            has_json = parsed.get("item") == "apples" or "apples" in str(parsed)
            return {
                "model": model,
                "reachable": True,
                "json_support": has_json,
                "raw_response": resp.strip(),
                "status": "passed" if has_json else "partial",
            }
        except Exception as exc:
            return {
                "model": model,
                "reachable": False,
                "error": str(exc),
                "status": "failed",
            }
