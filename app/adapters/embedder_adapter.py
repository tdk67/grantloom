"""Embedder implementations for dense vector generation."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Sequence

import httpx

from app.core.constants import ENGLISH_STOP_WORDS
from app.core.errors import LLMInferenceError
from app.ports.embedder import EmbedderPort


class DeterministicEmbedder(EmbedderPort):
    """Deterministic, zero-dependency lexical embedder (token + bigram feature hashing).

    NOT a semantic model: similarity reflects shared tokens, not meaning. Used for offline
    development, tests, and the offline eval smoke run. Select it explicitly with
    `models.embedding_backend: "deterministic"`.
    """

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension

    @property
    def fingerprint(self) -> str:
        return f"deterministic-hash-v1:{self.dimension}"

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
    """Cloud embedding client calling the OpenRouter / OpenAI-compatible embeddings endpoint.

    Requires a server-side key; there is no silent fallback to another embedding space.
    """

    def __init__(self, api_key: str, model: str, base_url: str, timeout_seconds: float, dimension: int) -> None:
        if not api_key:
            raise LLMInferenceError("OpenRouterEmbedder requires OPENROUTER_API_KEY in .env.")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.dimension = dimension

    @property
    def fingerprint(self) -> str:
        return f"openrouter:{self.model}:{self.dimension}"

    async def embed_text(self, text: str) -> list[float]:
        batch = await self.embed_batch([text])
        return batch[0]

    async def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": self.model, "input": list(texts)}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(url, headers=headers, json=payload)
        except httpx.RequestError as exc:
            raise LLMInferenceError(f"Embedding request failed: {exc}") from exc
        if resp.status_code != 200:
            raise LLMInferenceError(f"OpenRouter embedding failed with HTTP {resp.status_code}: {resp.text}")
        items = sorted(resp.json().get("data", []), key=lambda x: x.get("index", 0))
        vectors = [item["embedding"] for item in items]
        if len(vectors) != len(texts) or any(len(v) != self.dimension for v in vectors):
            raise LLMInferenceError(
                f"Embedding response shape mismatch: expected {len(texts)} vectors of dimension {self.dimension}."
            )
        return vectors
