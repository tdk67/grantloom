"""Ask facade: assembles the agent pipeline (app/services/agent_loop.py) from its collaborators.

There is exactly one query pipeline; this class only wires it so the API, the eval runner and the
tests build it the same way.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional

from app.core.config import Settings
from app.ports.decision_backend import DecisionBackendPort
from app.ports.graph_store import GraphStorePort
from app.ports.llm_client import LLMClientPort
from app.ports.registry import RegistryPort
from app.ports.structured_store import StructuredStorePort
from app.services.agent_loop import (
    AgentLoopOrchestrator,
    AgentRunResult,
    PlannerAgent,
    RetrieverAgent,
    ValidatorAgent,
    WriterAgent,
)
from app.services.guardrails import ContextFilter, InputGuard, OutputGuard, StrategyRouter
from app.services.retrieval_service import HybridRetrievalService
from app.services.retriever_tools import GraphQueryTool, StructuredQueryTool


class AskService:
    """Builds and exposes the Planner -> Retriever -> Writer -> Validator pipeline."""

    def __init__(
        self,
        retrieval_service: HybridRetrievalService,
        registry: RegistryPort,
        llm_client: LLMClientPort,
        settings: Settings,
        decision_backend: DecisionBackendPort,
        writer_model: str,
        egress: dict[str, Any],
        structured_store: Optional[StructuredStorePort] = None,
        graph_store: Optional[GraphStorePort] = None,
        sql_model: Optional[str] = None,
    ) -> None:
        structured_tool = (
            StructuredQueryTool(structured_store, registry, llm_client, sql_model or writer_model, settings)
            if structured_store is not None else None
        )
        graph_tool = GraphQueryTool(graph_store, registry, settings) if graph_store is not None else None

        planner = PlannerAgent(
            input_guard=InputGuard(decision_backend, settings.guardrails, settings.decision),
            strategy_router=StrategyRouter(decision_backend, settings.decision),
            structured_tool=structured_tool,
            graph_tool=graph_tool,
            semantic_embeddings=settings.models.embedding_backend != "deterministic",
        )
        retriever = RetrieverAgent(
            retrieval_service=retrieval_service,
            context_filter=ContextFilter(decision_backend, settings.decision, settings.retrieval),
            structured_tool=structured_tool,
            graph_tool=graph_tool,
        )
        self.orchestrator = AgentLoopOrchestrator(
            planner=planner,
            retriever=retriever,
            writer=WriterAgent(llm_client, writer_model, registry),
            validator=ValidatorAgent(OutputGuard(registry, decision_backend, settings.guardrails.citation_excerpt_chars)),
            settings=settings,
            egress=egress,
        )

    def stream_ask(self, query: str, api_key: Optional[str] = None) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Stream SSE events: egress -> guard -> route -> plan -> tool* -> retrieve -> filter -> generate -> verify -> done."""
        return self.orchestrator.stream(query, api_key)

    async def run(self, query: str, api_key: Optional[str] = None) -> AgentRunResult:
        return await self.orchestrator.run(query, api_key)
