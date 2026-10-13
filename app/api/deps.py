"""Composition root: builds every adapter and service from Settings (no implicit fallbacks)."""

from __future__ import annotations

import ipaddress
from typing import Optional

from fastapi import Depends, Header, Request

from app.adapters.chroma_adapter import ChromaVectorStoreAdapter
from app.adapters.kuzu_adapter import KuzuGraphStoreAdapter
from app.adapters.sqlite_registry import SqliteRegistryAdapter
from app.adapters.sqlite_structured_store import SqliteStructuredStoreAdapter
from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationError
from app.core.rate_limit import limiter
from app.ports.decision_backend import DecisionBackendPort
from app.ports.embedder import EmbedderPort
from app.ports.graph_store import GraphStorePort
from app.ports.llm_client import LLMClientPort
from app.ports.registry import RegistryPort
from app.ports.structured_store import StructuredStorePort
from app.ports.vector_store import VectorStorePort
from app.services.ask_service import AskService
from app.services.chunker import DocumentChunker
from app.services.compliance_service import ComplianceService
from app.services.extractor import DocumentExtractor
from app.services.guardrails import IngestionScanner
from app.services.inference import (
    InferenceProfile,
    build_decision_backend,
    build_embedder,
    build_inference_profile,
)
from app.services.ingest_service import IngestionService
from app.services.key_vault import EphemeralKeyVault
from app.services.retrieval_service import HybridRetrievalService, corpus_overlap
from app.verification.sql_guard import SqlGuard
from app.workers.ingest_worker import IngestWorker

_instances: dict[str, object] = {}


def _singleton(name: str, factory):
    if name not in _instances:
        _instances[name] = factory()
    return _instances[name]


def get_inference_profile(settings: Settings = Depends(get_settings)) -> InferenceProfile:
    return _singleton("profile", lambda: build_inference_profile(settings))


def get_llm_client(settings: Settings = Depends(get_settings)) -> LLMClientPort:
    return get_inference_profile(settings).client


def get_decision_backend(settings: Settings = Depends(get_settings)) -> DecisionBackendPort:
    registry = get_registry(settings)
    return _singleton(
        "decision",
        lambda: build_decision_backend(settings, get_inference_profile(settings), lambda q: corpus_overlap(registry, q)),
    )


def get_registry(settings: Settings = Depends(get_settings)) -> RegistryPort:
    return _singleton("registry", lambda: SqliteRegistryAdapter(settings.database.sqlite_path))


def get_embedder(settings: Settings = Depends(get_settings)) -> EmbedderPort:
    return _singleton("embedder", lambda: build_embedder(settings))


def get_vector_store(settings: Settings = Depends(get_settings)) -> VectorStorePort:
    return _singleton(
        "vector_store",
        lambda: ChromaVectorStoreAdapter(settings.database.chroma_path, get_embedder(settings).fingerprint),
    )


def get_structured_store(settings: Settings = Depends(get_settings)) -> StructuredStorePort:
    return _singleton(
        "structured_store",
        lambda: SqliteStructuredStoreAdapter(
            settings.database.sqlite_path,
            SqlGuard(max_rows=settings.sql_guard.max_rows, timeout_seconds=settings.sql_guard.timeout_seconds),
        ),
    )


def get_graph_store(settings: Settings = Depends(get_settings)) -> GraphStorePort:
    return _singleton("graph_store", lambda: KuzuGraphStoreAdapter(settings.database.kuzu_path))


def get_key_vault() -> EphemeralKeyVault:
    return _singleton("key_vault", EphemeralKeyVault)


def get_ingest_service(settings: Settings = Depends(get_settings)) -> IngestionService:
    return _singleton("ingest_service", lambda: IngestionService(registry=get_registry(settings), settings=settings))


def get_ingest_worker(settings: Settings = Depends(get_settings)) -> IngestWorker:
    def build() -> IngestWorker:
        profile = get_inference_profile(settings)
        return IngestWorker(
            registry=get_registry(settings),
            vector_store=get_vector_store(settings),
            embedder=get_embedder(settings),
            scanner=IngestionScanner(get_decision_backend(settings), settings.guardrails),
            extractor=DocumentExtractor(
                max_pages_per_doc=settings.limits.max_pages_per_doc,
                rows_per_chunk=settings.chunking.rows_per_chunk,
            ),
            chunker=DocumentChunker(
                target_chunk_size=settings.chunking.target_chunk_size,
                chunk_overlap=settings.chunking.chunk_overlap,
            ),
            ingestion=settings.ingestion,
            quarantine_retention_days=settings.guardrails.quarantine_retention_days,
            structured_store=get_structured_store(settings),
            graph_store=get_graph_store(settings),
            key_vault=get_key_vault(),
            tier2_requires_key=profile.requires_byok_key,
        )

    return _singleton("ingest_worker", build)


def get_retrieval_service(settings: Settings = Depends(get_settings)) -> HybridRetrievalService:
    return _singleton(
        "retrieval_service",
        lambda: HybridRetrievalService(
            vector_store=get_vector_store(settings),
            registry=get_registry(settings),
            embedder=get_embedder(settings),
            settings=settings,
        ),
    )


def get_ask_service(settings: Settings = Depends(get_settings)) -> AskService:
    def build() -> AskService:
        profile = get_inference_profile(settings)
        decision = get_decision_backend(settings)
        return AskService(
            retrieval_service=get_retrieval_service(settings),
            registry=get_registry(settings),
            llm_client=profile.client,
            settings=settings,
            decision_backend=decision,
            writer_model=profile.writer_model,
            egress=profile.egress(decision),
            structured_store=get_structured_store(settings),
            graph_store=get_graph_store(settings),
        )

    return _singleton("ask_service", build)


def get_compliance_service(settings: Settings = Depends(get_settings)) -> ComplianceService:
    return _singleton(
        "compliance_service",
        lambda: ComplianceService(
            registry=get_registry(settings),
            vector_store=get_vector_store(settings),
            embedder=get_embedder(settings),
            decision_backend=get_decision_backend(settings),
            settings=settings,
        ),
    )


def _is_loopback(host: Optional[str]) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def verify_master_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    """Every /api/v1 route requires the master key when one is configured.

    Without a configured key the API only serves loopback clients, so an accidentally exposed
    instance never runs unauthenticated (review-02 §4.2).
    """
    expected_key = settings.secrets.master_api_key
    if expected_key:
        if not x_api_key or x_api_key.strip() != expected_key.strip():
            raise AuthenticationError("Invalid or missing master API key in X-API-Key header.")
        return
    if settings.server.public_byok:
        # D27 public BYOK demo: no server key; LLM calls are individually gated by the
        # caller's X-OpenRouter-Key (InferenceProfile.authorize) and rate limits.
        return
    client_host = request.client.host if request.client else None
    if not _is_loopback(client_host):
        raise AuthenticationError(
            "No MASTER_API_KEY is configured, so only loopback clients are served. Set MASTER_API_KEY to allow remote access."
        )


def reset_dependencies() -> None:
    """Reset cached singletons and rate-limit counters (test isolation)."""
    graph = _instances.get("graph_store")
    if isinstance(graph, KuzuGraphStoreAdapter):
        graph.close()
    _instances.clear()
    limiter.reset()
