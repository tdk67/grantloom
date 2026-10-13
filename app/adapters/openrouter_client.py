"""OpenRouter LLM Client Adapter with live BYOK key probe validation."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Optional

import httpx

from app.core.errors import (
    AuthenticationError,
    LLMInferenceError,
    PaymentRequiredError,
    UpstreamServiceError,
)
from app.ports.llm_client import LLMClientPort

logger = logging.getLogger("grantloom.adapters.openrouter_client")

OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterClientAdapter(LLMClientPort):
    """Client for OpenRouter inference and live BYOK credential validation."""

    def __init__(
        self,
        base_url: str = OPENROUTER_DEFAULT_BASE_URL,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def validate_api_key(self, api_key: str) -> bool:
        """Validate API key live against OpenRouter /api/v1/auth/key probe.
        
        Raises AuthenticationError on HTTP 401.
        Raises PaymentRequiredError on HTTP 402 or zero balance.
        Raises UpstreamServiceError on HTTP 5xx or probe communication failure.
        """
        if not api_key or not api_key.strip():
            raise AuthenticationError("API key must not be empty.")

        url = f"{self.base_url}/auth/key"
        headers = {
            "Authorization": f"Bearer {api_key.strip()}",
            "HTTP-Referer": "https://grantloom.local",
            "X-Title": "GrantLoom",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            logger.warning("Failed to reach OpenRouter auth probe: %s", exc)
            raise UpstreamServiceError(f"Failed to communicate with OpenRouter auth probe: {exc}") from exc

        if response.status_code == 401:
            raise AuthenticationError("Invalid OpenRouter API key.")

        if response.status_code == 402:
            raise PaymentRequiredError("OpenRouter account balance is exhausted.")

        if response.status_code >= 500:
            raise UpstreamServiceError(
                f"OpenRouter upstream server returned status {response.status_code} during auth probe."
            )

        if response.status_code != 200:
            raise AuthenticationError(
                f"OpenRouter auth probe rejected key with status {response.status_code}."
            )

        # Inspect data payload for usage vs credit limits if present
        try:
            payload = response.json()
            data = payload.get("data", {})
            limit = data.get("limit")
            usage = data.get("usage", 0)
            if limit is not None and usage is not None and usage >= limit:
                raise PaymentRequiredError("OpenRouter account credit limit reached.")
        except (json.JSONDecodeError, KeyError):
            pass

        return True

    async def generate_response(
        self,
        prompt: str,
        model: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        api_key: Optional[str] = None,
    ) -> str:
        """Execute single-turn chat completion."""
        if not api_key:
            raise AuthenticationError("API key is required for inference.")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://grantloom.local",
            "X-Title": "GrantLoom",
        }

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, headers=headers, json=body)
        except httpx.RequestError as exc:
            raise UpstreamServiceError(f"OpenRouter chat completion request failed: {exc}") from exc

        if resp.status_code == 401:
            raise AuthenticationError("Invalid OpenRouter API key.")
        if resp.status_code == 402:
            raise PaymentRequiredError("OpenRouter account balance exhausted.")
        if resp.status_code >= 500:
            raise UpstreamServiceError(f"OpenRouter returned status {resp.status_code}: {resp.text}")
        if resp.status_code != 200:
            raise LLMInferenceError(f"OpenRouter completion failed with status {resp.status_code}: {resp.text}")

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise LLMInferenceError("OpenRouter returned empty choices list.")

        content = choices[0].get("message", {}).get("content", "")
        return str(content)

    async def stream_response(
        self,
        prompt: str,
        model: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        api_key: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Stream generated response tokens from OpenRouter SSE endpoint."""
        if not api_key:
            raise AuthenticationError("API key is required for inference.")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://grantloom.local",
            "X-Title": "GrantLoom",
        }

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, headers=headers, json=body) as stream_resp:
                    if stream_resp.status_code == 401:
                        raise AuthenticationError("Invalid OpenRouter API key.")
                    if stream_resp.status_code == 402:
                        raise PaymentRequiredError("OpenRouter credit balance exhausted.")
                    if stream_resp.status_code != 200:
                        err_text = await stream_resp.aread()
                        raise UpstreamServiceError(
                            f"OpenRouter stream failed with status {stream_resp.status_code}: {err_text.decode('utf-8', errors='replace')}"
                        )

                    async for line in stream_resp.aiter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        raw_data = line[len("data:"):].strip()
                        if raw_data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(raw_data)
                            choices = chunk.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                token = delta.get("content")
                                if token:
                                    yield token
                        except json.JSONDecodeError:
                            continue
        except (AuthenticationError, PaymentRequiredError, UpstreamServiceError):
            raise
        except Exception as exc:
            raise LLMInferenceError(f"Error while streaming from OpenRouter: {exc}") from exc
