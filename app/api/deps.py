"""Dependency injection providers for FastAPI routes."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from app.adapters.chroma_adapter import ChromaVectorStoreAdapter
from app.adapters.embedder_adapter import DeterministicEmbedder, OpenRouterEmbedder
from app.adapters.sqlite_registry import SqliteRegistryAdapter
from app.core.config import Settings, get_settings
from app.ports.embedder import EmbedderPort
from app.ports.registry import RegistryPort
from app.ports.vector_store import VectorStorePort
from app.services.chunker import DocumentChunker
from app.services.extractor import DocumentExtractor
from app.services.ingest_service import IngestionService
from app.workers.ingest_worker import IngestWorker


_registry_instance: Optional[RegistryPort] = None
_vector_store_instance: Optional[VectorStorePort] = None
_embedder_instance: Optional[EmbedderPort] = None
_ingest_service_instance: Optional[IngestionService] = None
_ingest_worker_instance: Optional[IngestWorker] = None


def get_registry(settings: Optional[Settings] = None) -> RegistryPort:
    global _registry_instance
    if _registry_instance is None:
        cfg = settings or get_settings()
        _registry_instance = SqliteRegistryAdapter(cfg.database.sqlite_path)
    return _registry_instance


def get_vector_store(settings: Optional[Settings] = None) -> VectorStorePort:
    global _vector_store_instance
    if _vector_store_instance is None:
        cfg = settings or get_settings()
        _vector_store_instance = ChromaVectorStoreAdapter(cfg.database.chroma_path)
    return _vector_store_instance


def get_embedder(settings: Optional[Settings] = None) -> EmbedderPort:
    global _embedder_instance
    if _embedder_instance is None:
        cfg = settings or get_settings()
        # If openrouter_api_key is available, use OpenRouterEmbedder, else DeterministicEmbedder
        api_key = cfg.secrets.openrouter_api_key
        if api_key:
            _embedder_instance = OpenRouterEmbedder(
                api_key=api_key,
                model=cfg.models.embedding_model,
            )
        else:
            _embedder_instance = DeterministicEmbedder()
    return _embedder_instance


def get_ingest_service(settings: Optional[Settings] = None) -> IngestionService:
    global _ingest_service_instance
    if _ingest_service_instance is None:
        cfg = settings or get_settings()
        reg = get_registry(cfg)
        _ingest_service_instance = IngestionService(registry=reg, settings=cfg)
    return _ingest_service_instance


def get_ingest_worker(settings: Optional[Settings] = None) -> IngestWorker:
    global _ingest_worker_instance
    if _ingest_worker_instance is None:
        cfg = settings or get_settings()
        reg = get_registry(cfg)
        vs = get_vector_store(cfg)
        emb = get_embedder(cfg)
        ext = DocumentExtractor(max_pages_per_doc=cfg.limits.max_pages_per_doc)
        chk = DocumentChunker()
        _ingest_worker_instance = IngestWorker(
            registry=reg,
            vector_store=vs,
            embedder=emb,
            extractor=ext,
            chunker=chk,
        )
    return _ingest_worker_instance


def reset_dependencies() -> None:
    """Reset cached singletons (useful for test isolation)."""
    global _registry_instance, _vector_store_instance, _embedder_instance
    global _ingest_service_instance, _ingest_worker_instance
    _registry_instance = None
    _vector_store_instance = None
    _embedder_instance = None
    _ingest_service_instance = None
    _ingest_worker_instance = None
