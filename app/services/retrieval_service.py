"""Hybrid retrieval service combining ChromaDB dense vectors and SQLite FTS5 BM25 with RRF."""

from __future__ import annotations

import logging
from typing import Sequence

from app.core.config import Settings
from app.ports.embedder import EmbedderPort
from app.ports.models import DocumentChunk, KeywordSearchResult, VectorSearchResult
from app.ports.registry import RegistryPort
from app.ports.vector_store import VectorStorePort

logger = logging.getLogger("grantloom.services.retrieval")

ABSTENTION_MESSAGE = "This information was not found in the indexed documents"


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

    async def retrieve(
        self,
        query: str,
        strategy: str = "hybrid",
    ) -> tuple[list[DocumentChunk], float, bool]:
        """Execute retrieval and return (candidate_chunks, max_relevance, is_abstention).
        
        If no chunks pass the min_relevance_threshold, returns empty chunk list with is_abstention=True.
        """
        dense_results: list[VectorSearchResult] = []
        bm25_results: list[KeywordSearchResult] = []

        top_k_dense = self.settings.retrieval.dense_top_k
        top_k_bm25 = self.settings.retrieval.bm25_top_k
        rrf_k = self.settings.retrieval.rrf_k
        min_threshold = self.settings.retrieval.min_relevance_threshold
        rerank_top_k = self.settings.retrieval.rerank_top_k

        # 1. Dense Semantic Search (or fallback)
        if strategy in ("hybrid", "semantic", "structured", "graph", "composite"):
            try:
                query_vector = await self.embedder.embed_text(query)
                dense_results = self.vector_store.query(query_vector, top_k=top_k_dense)
            except Exception as exc:
                logger.warning("Dense retrieval failed: %s", exc)
                dense_results = []

        # 2. SQLite FTS5 BM25 Keyword Search (or fallback)
        if strategy in ("hybrid", "keyword", "structured", "graph", "composite"):
            try:
                bm25_results = self.registry.search_bm25(query, top_k=top_k_bm25)
            except Exception as exc:
                logger.warning("BM25 retrieval failed: %s", exc)
                bm25_results = []


        # 3. Check for Zero Matching or Sub-threshold Chunks (FR-Q8 Abstention)
        max_dense_score = max((r.score for r in dense_results), default=0.0)
        has_bm25 = len(bm25_results) > 0

        has_substantive_bm25 = False
        if has_bm25:
            import re
            from app.core.constants import ENGLISH_STOP_WORDS

            query_tokens = [w for w in re.findall(r"\b[a-zA-Z0-9_\-]{2,}\b", query.lower()) if w not in ENGLISH_STOP_WORDS]
            if not query_tokens:
                has_substantive_bm25 = True
            else:
                best_overlap = 0
                for b_res in bm25_results[:5]:
                    chunk_tokens = set(re.findall(r"\b[a-zA-Z0-9_\-]{2,}\b", b_res.text.lower()))
                    overlap = len(set(query_tokens).intersection(chunk_tokens))
                    if overlap > best_overlap:
                        best_overlap = overlap

                if len(query_tokens) == 1:
                    has_substantive_bm25 = best_overlap >= 1
                elif len(query_tokens) <= 3:
                    has_substantive_bm25 = best_overlap >= 2
                else:
                    has_substantive_bm25 = best_overlap >= 3 or (best_overlap >= 2 and (best_overlap / len(query_tokens)) >= 0.50)

        # If zero hits across both, or dense score is below min_relevance_threshold and no substantive BM25 matches
        if not dense_results and not has_substantive_bm25:
            logger.info("Abstention triggered: zero retrieval matches.")
            return [], 0.0, True

        if max_dense_score < min_threshold and not has_substantive_bm25:
            logger.info(
                "Abstention triggered: max dense score %.3f below threshold %.3f without substantive BM25 hits.",
                max_dense_score,
                min_threshold,
            )
            return [], max_dense_score, True

        # 4. Reciprocal Rank Fusion (RRF)
        rrf_scores: dict[str, float] = {}

        # Add dense ranks
        for rank, res in enumerate(dense_results, start=1):
            rrf_scores[res.chunk_id] = rrf_scores.get(res.chunk_id, 0.0) + (1.0 / (rrf_k + rank))

        # Add BM25 ranks
        for rank, res in enumerate(bm25_results, start=1):
            rrf_scores[res.chunk_id] = rrf_scores.get(res.chunk_id, 0.0) + (1.0 / (rrf_k + rank))

        # Sort candidate chunk IDs by fused RRF score descending
        sorted_chunk_ids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)
        top_chunk_ids = sorted_chunk_ids[:rerank_top_k]

        # 5. Fetch and verify active chunks from registry (G3 Context Filter)
        retained_chunks: list[DocumentChunk] = []
        for cid in top_chunk_ids:
            chunk = self.registry.get_chunk(cid)
            if chunk is None:
                continue
            doc = self.registry.get_document(chunk.doc_id)
            if doc and doc.state == "active":
                retained_chunks.append(chunk)

        if not retained_chunks:
            logger.info("Abstention triggered: no active chunks retained after filtering.")
            return [], 0.0, True

        effective_score = max(max_dense_score, 0.5 if has_bm25 else 0.0)
        return retained_chunks, effective_score, False
