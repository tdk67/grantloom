"""Embedder implementations for dense vector generation."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Optional, Sequence

import httpx

from app.core.constants import ENGLISH_STOP_WORDS
from app.core.errors import LLMInferenceError
from app.ports.embedder import EmbedderPort


class DeterministicEmbedder(EmbedderPort):
    """Deterministic, zero-dependency embedder generating fixed-dimension dense vectors.
    
    Used for local testing, integration benchmarks, and environments without active LLM keys.
    Uses token-level feature hashing to preserve semantic token overlap and produce
    near-orthogonal vectors for out-of-domain queries.
    """

    def __init__(self, dimension: int = 1536) -> None:
        self.dimension = dimension

    async def embed_text(self, text: str) -> list[float]:
        return self._compute_deterministic_vector(text)

    async def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._compute_deterministic_vector(t) for t in texts]

    def _compute_deterministic_vector(self, text: str) -> list[float]:
        raw_tokens = re.findall(r"\b[a-zA-Z0-9_\-]{2,}\b", text.lower())
        tokens = [t for t in raw_tokens if t not in ENGLISH_STOP_WORDS]
        if not tokens:
            return [0.0] * self.dimension

        features = list(tokens)
        if len(tokens) > 1:
            features.extend(f"{tokens[i]}_{tokens[i+1]}" for i in range(len(tokens) - 1))

        vec = [0.0] * self.dimension
        for feat in features:
            h = int(hashlib.md5(feat.encode("utf-8")).hexdigest(), 16)
            for k in range(4):
                idx = (h >> (k * 12)) % self.dimension
                sign = 1.0 if ((h >> (k * 12 + 11)) & 1) else -1.0
                vec[idx] += sign

        norm_sq = sum(v * v for v in vec)
        if norm_sq <= 0.0:
            return [0.0] * self.dimension
        norm = math.sqrt(norm_sq)
        return [round(v / norm, 6) for v in vec]


class OpenRouterEmbedder(EmbedderPort):
    """Cloud embedding client calling OpenRouter / OpenAI embeddings endpoint."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "text-embedding-3-small",
        base_url: str = "https://openrouter.ai/api/v1",
        fallback_embedder: Optional[EmbedderPort] = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.fallback = fallback_embedder or DeterministicEmbedder()

    async def embed_text(self, text: str) -> list[float]:
        batch = await self.embed_batch([text])
        return batch[0]

    async def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.api_key:
            return await self.fallback.embed_batch(texts)

        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "input": list(texts),
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code != 200:
                    raise LLMInferenceError(
                        f"OpenRouter embedding failed with HTTP {resp.status_code}: {resp.text}"
                    )
                data = resp.json()
                items = data.get("data", [])
                # Sort by index in case API returns out of order
                items.sort(key=lambda x: x.get("index", 0))
                return [item["embedding"] for item in items]
        except Exception as exc:
            if isinstance(exc, LLMInferenceError):
                raise
            raise LLMInferenceError(f"Embedding request failed: {exc}") from exc
