"""Hybrid retrieval service combining ChromaDB dense vectors and SQLite FTS5 BM25 with RRF."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from app.core.config import Settings
from app.core.errors import GrantLoomError, StorageError
from app.core.text import TOKEN_RE, content_tokens, overlap_ratio
from app.ports.embedder import EmbedderPort
from app.ports.models import DocumentChunk, KeywordSearchResult, VectorSearchResult
from app.ports.registry import RegistryPort
from app.ports.vector_store import VectorStorePort

logger = logging.getLogger("grantloom.services.retrieval")

ABSTENTION_MESSAGE = "This information was not found in the indexed documents"

NEAR_DUPLICATE_RE = re.compile(r"\d+|\s+")

DENSE_STRATEGIES = ("hybrid", "semantic", "structured", "graph")
KEYWORD_STRATEGIES = ("hybrid", "keyword", "structured", "graph")


@dataclass
class RetrievalResult:
    """Fused retrieval outcome with per-channel evidence for tool logs and the G3 filter."""

    chunks: list[DocumentChunk]
    max_relevance: float
    abstention: bool
    dense_scores: dict[str, float] = field(default_factory=dict)
    dense_count: int = 0
    bm25_count: int = 0
    dense_ms: float = 0.0
    bm25_ms: float = 0.0
    errors: list[str] = field(default_factory=list)


class HybridRetrievalService:
    """Retrieval orchestrator fusing dense semantic vectors and BM25 keyword matching."""

    def __init__(
        self,
        vector_store: VectorStorePort,
        registry: RegistryPort,
        embedder: EmbedderPort,
        settings: Settings,
    ) -> None:
        self.vector_store = vector_store
        self.registry = registry
        self.embedder = embedder
        self.settings = settings

    async def retrieve(self, query: str, strategy: str = "hybrid") -> RetrievalResult:
        """Execute retrieval. With no chunk above min_relevance_threshold and no substantive keyword
        match, returns an abstention result (FR-Q8)."""
        cfg = self.settings.retrieval
        dense_results: list[VectorSearchResult] = []
        bm25_results: list[KeywordSearchResult] = []
        errors: list[str] = []
        dense_ms = bm25_ms = 0.0

        # 1. Dense semantic search
        if strategy in DENSE_STRATEGIES:
            started = time.perf_counter()
            try:
                query_vector = await self.embedder.embed_text(query)
                dense_results = self.vector_store.query(query_vector, top_k=cfg.dense_top_k)
            except GrantLoomError as exc:
                errors.append(f"dense_vector_search: {exc.detail}")
                logger.error("Dense retrieval failed: %s", exc)
            dense_ms = (time.perf_counter() - started) * 1000

        # 2. SQLite FTS5 BM25 keyword search
        if strategy in KEYWORD_STRATEGIES:
            started = time.perf_counter()
            try:
                bm25_results = self.registry.search_bm25(query, top_k=cfg.bm25_top_k)
            except GrantLoomError as exc:
                errors.append(f"bm25_keyword_search: {exc.detail}")
                logger.error("BM25 retrieval failed: %s", exc)
            bm25_ms = (time.perf_counter() - started) * 1000

        attempted = (strategy in DENSE_STRATEGIES) + (strategy in KEYWORD_STRATEGIES)
        if attempted and len(errors) == attempted:
            raise StorageError("All retrieval channels failed: " + "; ".join(errors))

        def result(chunks: list[DocumentChunk], relevance: float, abstention: bool) -> RetrievalResult:
            return RetrievalResult(
                chunks=chunks,
                max_relevance=relevance,
                abstention=abstention,
                dense_scores={r.chunk_id: r.score for r in dense_results},
                dense_count=len(dense_results),
                bm25_count=len(bm25_results),
                dense_ms=round(dense_ms, 1),
                bm25_ms=round(bm25_ms, 1),
                errors=errors,
            )

        # 3. FR-Q8 abstention check. The two signals live on different scales and are calibrated
        # separately (review-04 C2): dense cosine >= retrieval.min_relevance_threshold OR lexical
        # overlap >= retrieval.min_bm25_overlap. An empty query has no tokens -> overlap 0.0.
        max_dense_score = max((r.score for r in dense_results), default=0.0)
        query_tokens = set(content_tokens(query))
        best_overlap = 0
        for b_res in bm25_results[:5]:
            best_overlap = max(best_overlap, len(query_tokens & set(TOKEN_RE.findall(b_res.text.lower()))))

        bm25_overlap_ratio = (best_overlap / len(query_tokens)) if query_tokens else 0.0
        dense_relevant = max_dense_score >= cfg.min_relevance_threshold
        # Calibrated lexical relevance: matching at least min(2, len(query_tokens)) key terms
        # in top BM25 results or meeting overlap ratio threshold confirms relevance without
        # falsely aborting multi-clause or comparative queries.
        min_overlap_required = min(2, len(query_tokens)) if query_tokens else 1
        keyword_relevant = (bool(bm25_results) and best_overlap >= min_overlap_required) or (bm25_overlap_ratio >= cfg.min_bm25_overlap)
        max_relevance_signal = max(max_dense_score, bm25_overlap_ratio)

        if not (dense_relevant or keyword_relevant):
            logger.info(
                "Abstention: no relevant evidence (dense=%.3f < %.3f and bm25_overlap=%.3f < %.3f).",
                max_dense_score, cfg.min_relevance_threshold, bm25_overlap_ratio, cfg.min_bm25_overlap,
            )
            return result([], max_relevance_signal, True)

        # 4. Reciprocal Rank Fusion (RRF)
        rrf_scores: dict[str, float] = {}
        for rank, res in enumerate(dense_results, start=1):
            rrf_scores[res.chunk_id] = rrf_scores.get(res.chunk_id, 0.0) + 1.0 / (cfg.rrf_k + rank)
        for rank, res in enumerate(bm25_results, start=1):
            rrf_scores[res.chunk_id] = rrf_scores.get(res.chunk_id, 0.0) + 1.0 / (cfg.rrf_k + rank)
        ranked_ids = sorted(rrf_scores, key=lambda cid: rrf_scores[cid], reverse=True)

        # 5. Keep chunks of active documents only (quarantined / deleting / failed docs never surface),
        #    skipping near-duplicates (boilerplate repeated across pages with only numbers changed)
        retained: list[DocumentChunk] = []
        seen_texts: set[str] = set()
        for cid in ranked_ids:
            if len(retained) >= cfg.rerank_top_k:
                break
            chunk = self.registry.get_chunk(cid)
            if chunk is None:
                continue
            signature = NEAR_DUPLICATE_RE.sub("#", chunk.text_content.lower())
            if signature in seen_texts:
                continue
            doc = self.registry.get_document(chunk.doc_id)
            if doc and doc.state == "active":
                seen_texts.add(signature)
                retained.append(chunk)

        if not retained:
            logger.info("Abstention: no active chunks retained after fusion.")
            return result([], 0.0, True)

        keyword_score = round(bm25_overlap_ratio, 2) if keyword_relevant else 0.0
        return result(retained, max(max_dense_score, min(1.0, keyword_score)), False)


def corpus_overlap(registry: RegistryPort, query: str, top_k: int = 5) -> float:
    """Best share of the query's content terms found in a single active chunk (BM25 candidates)."""
    try:
        hits = registry.search_bm25(query, top_k)
    except StorageError:
        return 0.0
    return max((overlap_ratio(query, h.text) for h in hits), default=0.0)
