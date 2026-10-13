"""Ask reasoning pipeline orchestrating G1, G2, Hybrid Retrieval, Writer LLM, and G4."""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Optional, Sequence

from app.core.config import Settings
from app.core.templates import load_template
from app.ports.llm_client import LLMClientPort
from app.ports.models import AskResponse, CitationCoordinate, DocumentChunk
from app.ports.registry import RegistryPort
from app.services.guardrails import InputGuard, OutputGuard, StrategyRouter
from app.services.retrieval_service import ABSTENTION_MESSAGE, HybridRetrievalService

logger = logging.getLogger("grantloom.services.ask")


class AskService:
    """Orchestrator for question answering with SSE event emission."""

    def __init__(
        self,
        retrieval_service: HybridRetrievalService,
        registry: RegistryPort,
        llm_client: LLMClientPort,
        settings: Settings,
        input_guard: Optional[InputGuard] = None,
        strategy_router: Optional[StrategyRouter] = None,
        output_guard: Optional[OutputGuard] = None,
    ) -> None:
        self.retrieval_service = retrieval_service
        self.registry = registry
        self.llm_client = llm_client
        self.settings = settings
        self.input_guard = input_guard or InputGuard()
        self.strategy_router = strategy_router or StrategyRouter()
        self.output_guard = output_guard or OutputGuard(registry)

    async def stream_ask(
        self,
        query: str,
        api_key: Optional[str] = None,
    ) -> AsyncIterator[tuple[str, dict]]:
        """Stream canonical SSE events: guard -> route -> retrieve -> filter -> generate -> verify -> done."""
        # 1. Event: guard (G1)
        g1_verdict = self.input_guard.evaluate(query)
        yield ("guard", {
            "gate": "G1",
            "passed": g1_verdict.passed,
            "confidence": g1_verdict.confidence,
            "reason": g1_verdict.reason,
        })

        if not g1_verdict.passed:
            refusal_text = g1_verdict.metadata.get("refusal", "Your query was declined by the input guardrail.")
            yield ("generate", {"token": refusal_text})
            yield ("done", {
                "answer": refusal_text,
                "citations": [],
                "confidence_score": 0.0,
                "abstention": False,
                "refusal": True,
            })
            return

        sanitized_query = g1_verdict.metadata.get("sanitized_query", query)

        # 2. Event: route (G2)
        g2_verdict = self.strategy_router.evaluate(sanitized_query)
        strategy = g2_verdict.metadata.get("strategy", "hybrid")
        yield ("route", {
            "gate": "G2",
            "strategy": strategy,
            "confidence": g2_verdict.confidence,
            "reason": g2_verdict.reason,
        })

        # 3. Event: retrieve (Multi-store hybrid)
        chunks, max_relevance, is_abstention = await self.retrieval_service.retrieve(
            query=sanitized_query,
            strategy=strategy,
        )
        yield ("retrieve", {
            "chunks_retrieved": len(chunks),
            "max_relevance": round(max_relevance, 3),
            "strategy": strategy,
        })

        # 4. Event: filter (G3 Context Filter / Abstention check)
        yield ("filter", {
            "chunks_retained": len(chunks),
            "abstention": is_abstention,
        })

        # Handle FR-Q8 Abstention contract
        if is_abstention or not chunks:
            abstention_answer = ABSTENTION_MESSAGE
            yield ("generate", {"token": abstention_answer})
            yield ("verify", {
                "gate": "G4",
                "verified": True,
                "confidence_score": 1.0,
                "citations_count": 0,
            })
            yield ("done", {
                "answer": abstention_answer,
                "citations": [],
                "confidence_score": 1.0,
                "abstention": True,
                "strategy": strategy,
            })
            return

        # 5. Format prompt context and call Writer LLM
        system_prompt = load_template("prompts/writer_system.txt")
        context_blocks = []
        retrieved_chunk_ids = [c.chunk_id for c in chunks]

        for c in chunks:
            doc = self.registry.get_document(c.doc_id)
            fname = doc.filename if doc else "unknown"
            coord_str = f"Page {c.page_number}"
            if c.sheet_name:
                coord_str = f"Sheet '{c.sheet_name}', Row {c.row_start}"
            context_blocks.append(
                f"[{c.chunk_id}]\nSource: {fname} ({coord_str})\nText: {c.text_content.strip()}"
            )

        context_str = "\n\n".join(context_blocks)
        user_prompt = f"Context Documents:\n{context_str}\n\nQuestion: {sanitized_query}\nAnswer:"

        writer_model = self.settings.models.writer_model
        max_rewrites = self.settings.guardrails.max_rewrite_attempts
        attempt = 0
        current_prompt = user_prompt
        cleaned_text = ""
        verified_citations: list[CitationCoordinate] = []
        confidence_score = 0.0

        while True:
            attempt += 1
            collected_tokens: list[str] = []

            try:
                async for token in self.llm_client.stream_response(
                    prompt=current_prompt,
                    model=writer_model,
                    system_prompt=system_prompt,
                    temperature=0.0,
                    api_key=api_key,
                ):
                    collected_tokens.append(token)
                    yield ("generate", {"token": token})
            except Exception as exc:
                logger.error("Writer LLM streaming failed: %s", exc)
                yield ("error", {"detail": f"Inference error: {exc}"})
                return

            full_draft = "".join(collected_tokens)

            # 6. Event: verify (G4 Output Guard)
            cleaned_text, verified_citations, confidence_score = self.output_guard.resolve_citations(
                draft_text=full_draft,
                retrieved_chunk_ids=retrieved_chunk_ids,
                is_abstention=False,
            )

            if len(verified_citations) > 0 or attempt > max_rewrites:
                break

            logger.info("Rewrite loop triggered (attempt %d/%d): unverified citations.", attempt, max_rewrites)
            yield ("rewrite", {
                "attempt": attempt,
                "max_attempts": max_rewrites,
                "reason": "unverified_citations",
            })
            current_prompt = (
                f"{user_prompt}\n\n"
                f"[SYSTEM FEEDBACK]: Your previous draft lacked verifiable citations. "
                f"You must strictly ground every claim in the provided context documents and cite them with [chunk_id]. "
                f"Rewrite your answer now:"
            )


        citation_dicts = [
            {
                "chunk_id": c.chunk_id,
                "doc_id": c.doc_id,
                "filename": c.filename,
                "page": c.page,
                "line": c.line,
                "sheet": c.sheet,
                "row": c.row,
                "excerpt": c.excerpt,
                "verified": c.verified,
            }
            for c in verified_citations
        ]

        yield ("verify", {
            "gate": "G4",
            "verified": len(verified_citations) > 0,
            "citations_count": len(verified_citations),
            "confidence_score": confidence_score,
        })

        # 7. Event: done
        yield ("done", {
            "answer": cleaned_text,
            "citations": citation_dicts,
            "confidence_score": confidence_score,
            "abstention": False,
            "strategy": strategy,
        })
