"""Dependency injection providers for FastAPI routes."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from fastapi import Depends

from app.adapters.chroma_adapter import ChromaVectorStoreAdapter
from app.adapters.embedder_adapter import DeterministicEmbedder, OpenRouterEmbedder
from app.adapters.kuzu_adapter import KuzuGraphStoreAdapter
from app.adapters.openrouter_client import OpenRouterClientAdapter
from app.adapters.sqlite_registry import SqliteRegistryAdapter
from app.adapters.sqlite_structured_store import SqliteStructuredStoreAdapter
from app.core.config import Settings, get_settings
from app.ports.embedder import EmbedderPort
from app.ports.graph_store import GraphStorePort
from app.ports.llm_client import LLMClientPort
from app.ports.registry import RegistryPort
from app.ports.structured_store import StructuredStorePort
from app.ports.vector_store import VectorStorePort
from app.services.ask_service import AskService
from app.services.chunker import DocumentChunker
from app.services.extractor import DocumentExtractor
from app.services.ingest_service import IngestionService
from app.services.retrieval_service import HybridRetrievalService
from app.workers.ingest_worker import IngestWorker


_registry_instance: Optional[RegistryPort] = None
_vector_store_instance: Optional[VectorStorePort] = None
_embedder_instance: Optional[EmbedderPort] = None
_structured_store_instance: Optional[StructuredStorePort] = None
_graph_store_instance: Optional[GraphStorePort] = None
_ingest_service_instance: Optional[IngestionService] = None
_ingest_worker_instance: Optional[IngestWorker] = None
_llm_client_instance: Optional[LLMClientPort] = None
_retrieval_service_instance: Optional[HybridRetrievalService] = None
_ask_service_instance: Optional[AskService] = None



def get_registry(settings: Settings = Depends(get_settings)) -> RegistryPort:
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = SqliteRegistryAdapter(settings.database.sqlite_path)
    return _registry_instance


def get_vector_store(settings: Settings = Depends(get_settings)) -> VectorStorePort:
    global _vector_store_instance
    if _vector_store_instance is None:
        _vector_store_instance = ChromaVectorStoreAdapter(settings.database.chroma_path)
    return _vector_store_instance


def get_embedder(settings: Settings = Depends(get_settings)) -> EmbedderPort:
    global _embedder_instance
    if _embedder_instance is None:
        # If openrouter_api_key is available, use OpenRouterEmbedder, else DeterministicEmbedder
        api_key = settings.secrets.openrouter_api_key
        if api_key:
            _embedder_instance = OpenRouterEmbedder(
                api_key=api_key,
                model=settings.models.embedding_model,
            )
        else:
            _embedder_instance = DeterministicEmbedder()
    return _embedder_instance


def get_ingest_service(settings: Settings = Depends(get_settings)) -> IngestionService:
    global _ingest_service_instance
    if _ingest_service_instance is None:
        reg = get_registry(settings)
        _ingest_service_instance = IngestionService(registry=reg, settings=settings)
    return _ingest_service_instance


def get_structured_store(settings: Settings = Depends(get_settings)) -> StructuredStorePort:
    global _structured_store_instance
    if _structured_store_instance is None:
        _structured_store_instance = SqliteStructuredStoreAdapter(settings.database.sqlite_path)
    return _structured_store_instance


def get_graph_store(settings: Settings = Depends(get_settings)) -> GraphStorePort:
    global _graph_store_instance
    if _graph_store_instance is None:
        _graph_store_instance = KuzuGraphStoreAdapter(settings.database.kuzu_path)
    return _graph_store_instance


def get_ingest_worker(settings: Settings = Depends(get_settings)) -> IngestWorker:
    global _ingest_worker_instance
    if _ingest_worker_instance is None:
        reg = get_registry(settings)
        vs = get_vector_store(settings)
        emb = get_embedder(settings)
        ss = get_structured_store(settings)
        gs = get_graph_store(settings)
        ext = DocumentExtractor(max_pages_per_doc=settings.limits.max_pages_per_doc)
        chk = DocumentChunker()
        _ingest_worker_instance = IngestWorker(
            registry=reg,
            vector_store=vs,
            embedder=emb,
            structured_store=ss,
            graph_store=gs,
            extractor=ext,
            chunker=chk,
        )
    return _ingest_worker_instance


def get_llm_client(settings: Settings = Depends(get_settings)) -> LLMClientPort:
    global _llm_client_instance
    if _llm_client_instance is None:
        _llm_client_instance = OpenRouterClientAdapter()
    return _llm_client_instance


def get_retrieval_service(settings: Settings = Depends(get_settings)) -> HybridRetrievalService:
    global _retrieval_service_instance
    if _retrieval_service_instance is None:
        vs = get_vector_store(settings)
        reg = get_registry(settings)
        emb = get_embedder(settings)
        _retrieval_service_instance = HybridRetrievalService(
            vector_store=vs,
            registry=reg,
            embedder=emb,
            settings=settings,
        )
    return _retrieval_service_instance


def get_ask_service(settings: Settings = Depends(get_settings)) -> AskService:
    global _ask_service_instance
    if _ask_service_instance is None:
        ret = get_retrieval_service(settings)
        reg = get_registry(settings)
        llm = get_llm_client(settings)
        _ask_service_instance = AskService(
            retrieval_service=ret,
            registry=reg,
            llm_client=llm,
            settings=settings,
        )
    return _ask_service_instance



def reset_dependencies() -> None:
    """Reset cached singletons (useful for test isolation)."""
    global _registry_instance, _vector_store_instance, _embedder_instance
    global _structured_store_instance, _graph_store_instance
    global _ingest_service_instance, _ingest_worker_instance
    global _llm_client_instance, _retrieval_service_instance, _ask_service_instance
    if _graph_store_instance is not None:
        try:
            _graph_store_instance.close()
        except Exception:
            pass
    _registry_instance = None
    _vector_store_instance = None
    _embedder_instance = None
    _structured_store_instance = None
    _graph_store_instance = None
    _ingest_service_instance = None
    _ingest_worker_instance = None
    _llm_client_instance = None
    _retrieval_service_instance = None
    _ask_service_instance = None

