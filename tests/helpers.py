"""Shared test wiring: real adapters in a temp directory, configured exactly like the composition root."""

from __future__ import annotations

import gc
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

from fastapi import FastAPI
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.adapters.chroma_adapter import ChromaVectorStoreAdapter
from app.adapters.decision_backends import HeuristicDecisionBackend
from app.adapters.embedder_adapter import DeterministicEmbedder
from app.adapters.kuzu_adapter import KuzuGraphStoreAdapter
from app.adapters.sqlite_registry import SqliteRegistryAdapter
from app.adapters.sqlite_structured_store import SqliteStructuredStoreAdapter
from app.api import deps
from app.core.config import SecretsConfig, Settings, get_settings
from app.core.errors import AuthenticationError
from app.db import migrator
from app.main import create_app
from app.ports.decision_backend import DecisionBackendPort
from app.ports.llm_client import LLMClientPort
from app.services.ask_service import AskService
from app.services.chunker import DocumentChunker
from app.services.compliance_service import ComplianceService
from app.services.extractor import DocumentExtractor
from app.services.guardrails import IngestionScanner
from app.services.inference import InferenceProfile
from app.services.ingest_service import IngestionService
from app.services.key_vault import EphemeralKeyVault
from app.services.retrieval_service import HybridRetrievalService, corpus_overlap
from app.verification.sql_guard import SqlGuard
from app.workers.ingest_worker import IngestWorker

VALID_KEY = "sk-valid-openrouter-key"


def make_settings(master_api_key: Optional[str] = None, **overrides: Any) -> Settings:
    """Project config with test-controlled secrets (never the developer's .env)."""
    secrets = SecretsConfig(_env_file=None, master_api_key=master_api_key, openrouter_api_key=None)
    return get_settings().model_copy(update={"secrets": secrets, **overrides})


class ScriptedLLM(LLMClientPort):
    """LLM double: streamed drafts come from `drafts` (last one repeats); JSON calls go to `json_handler`."""

    def __init__(
        self,
        drafts: Optional[list[str]] = None,
        json_handler: Optional[Callable[[str], str]] = None,
        valid_key: str = VALID_KEY,
    ) -> None:
        self.drafts = drafts or []
        self.json_handler = json_handler
        self.valid_key = valid_key
        self.draft_calls = 0
        self.json_prompts: list[str] = []

    @property
    def provider(self) -> str:
        return "scripted"

    @property
    def destination(self) -> str:
        return "scripted.local"

    async def validate_api_key(self, api_key: str) -> bool:
        if api_key != self.valid_key:
            raise AuthenticationError("Invalid OpenRouter API key.")
        return True

    async def describe_model(self, model: str, api_key: Optional[str] = None) -> dict[str, Any]:
        return {"found": True, "tool_calling": True, "context_length": 32768}

    def _next_draft(self) -> str:
        draft = self.drafts[min(self.draft_calls, len(self.drafts) - 1)] if self.drafts else ""
        self.draft_calls += 1
        return draft

    async def generate_response(self, prompt: str, model: str, system_prompt: Optional[str] = None,
                                temperature: float = 0.0, api_key: Optional[str] = None, json_output: bool = False) -> str:
        if json_output:
            self.json_prompts.append(prompt)
            return self.json_handler(prompt) if self.json_handler else '{"sql": null}'
        return self._next_draft()

    async def stream_response(self, prompt: str, model: str, system_prompt: Optional[str] = None,
                              temperature: float = 0.0, api_key: Optional[str] = None) -> AsyncIterator[str]:
        yield self._next_draft()


def make_pdf(path: Path, pages: list[list[str]]) -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    for lines in pages:
        y = 750
        for line in lines:
            c.drawString(72, y, line)
            y -= 24
        c.showPage()
    c.save()
    return path


@dataclass
class Stack:
    settings: Settings
    registry: SqliteRegistryAdapter
    vector_store: ChromaVectorStoreAdapter
    embedder: DeterministicEmbedder
    structured_store: SqliteStructuredStoreAdapter
    graph_store: KuzuGraphStoreAdapter
    decision: DecisionBackendPort
    worker: IngestWorker
    ingest: IngestionService
    retrieval: HybridRetrievalService
    key_vault: EphemeralKeyVault

    async def ingest_bytes(self, filename: str, data: bytes) -> str:
        doc_id = self.ingest.upload_document(filename, data).doc_id
        while await self.worker.process_next_job():
            pass
        return doc_id

    def ask_service(self, llm: LLMClientPort, decision: Optional[DecisionBackendPort] = None) -> AskService:
        return AskService(
            retrieval_service=self.retrieval,
            registry=self.registry,
            llm_client=llm,
            settings=self.settings,
            decision_backend=decision or self.decision,
            writer_model="mock-writer",
            egress={"mode": "test", "provider": llm.provider},
            structured_store=self.structured_store,
            graph_store=self.graph_store,
        )

    def compliance(self, decision: Optional[DecisionBackendPort] = None) -> ComplianceService:
        return ComplianceService(self.registry, self.vector_store, self.embedder, decision or self.decision, self.settings)

    def profile(self, llm: LLMClientPort, mode: str = "byok") -> InferenceProfile:
        return InferenceProfile(mode=mode, client=llm, writer_model="mock-writer", judge_model="mock-judge",
                                embedding_description="deterministic")

    def app(self, llm: Optional[LLMClientPort] = None, mode: str = "byok") -> FastAPI:
        llm = llm or ScriptedLLM()
        app = create_app()
        overrides: dict[Callable, Callable] = {
            deps.get_settings: lambda: self.settings,
            deps.get_registry: lambda: self.registry,
            deps.get_vector_store: lambda: self.vector_store,
            deps.get_embedder: lambda: self.embedder,
            deps.get_structured_store: lambda: self.structured_store,
            deps.get_graph_store: lambda: self.graph_store,
            deps.get_decision_backend: lambda: self.decision,
            deps.get_ingest_service: lambda: self.ingest,
            deps.get_ingest_worker: lambda: self.worker,
            deps.get_retrieval_service: lambda: self.retrieval,
            deps.get_key_vault: lambda: self.key_vault,
            deps.get_inference_profile: lambda: self.profile(llm, mode),
            deps.get_llm_client: lambda: llm,
            deps.get_ask_service: lambda: self.ask_service(llm),
            deps.get_compliance_service: lambda: self.compliance(),
        }
        app.dependency_overrides.update(overrides)
        return app

    def close(self) -> None:
        self.graph_store.close()
        self.vector_store.close()
        self.registry.engine.dispose()
        gc.collect()


def build_stack(tmp_path: Path, settings: Optional[Settings] = None, decision: Optional[DecisionBackendPort] = None) -> Stack:
    settings = settings or make_settings()
    db_file = tmp_path / "grantloom.db"
    migrator.upgrade("head", db_url=f"sqlite:///{db_file.as_posix()}")
    registry = SqliteRegistryAdapter(db_file)
    embedder = DeterministicEmbedder(dimension=settings.models.embedding_dimension)
    vector_store = ChromaVectorStoreAdapter(tmp_path / "chromadb", embedder.fingerprint)
    structured_store = SqliteStructuredStoreAdapter(
        str(db_file), SqlGuard(settings.sql_guard.max_rows, settings.sql_guard.timeout_seconds)
    )
    graph_store = KuzuGraphStoreAdapter(str(tmp_path / "kuzudb"))
    decision = decision or HeuristicDecisionBackend(settings.decision.heuristic, lambda q: corpus_overlap(registry, q))
    key_vault = EphemeralKeyVault()
    worker = IngestWorker(
        registry=registry,
        vector_store=vector_store,
        embedder=embedder,
        scanner=IngestionScanner(decision, settings.guardrails),
        extractor=DocumentExtractor(settings.limits.max_pages_per_doc, settings.chunking.rows_per_chunk),
        chunker=DocumentChunker(settings.chunking.target_chunk_size, settings.chunking.chunk_overlap),
        ingestion=settings.ingestion,
        quarantine_retention_days=settings.guardrails.quarantine_retention_days,
        structured_store=structured_store,
        graph_store=graph_store,
        key_vault=key_vault,
    )
    ingest = IngestionService(registry=registry, settings=settings, upload_dir=tmp_path / "uploads")
    retrieval = HybridRetrievalService(vector_store, registry, embedder, settings)
    return Stack(settings, registry, vector_store, embedder, structured_store, graph_store, decision,
                 worker, ingest, retrieval, key_vault)


def parse_sse(raw_body: str) -> list[tuple[str, dict]]:
    import json

    events = []
    for block in raw_body.strip().split("\n\n"):
        name = data = ""
        for line in block.strip().split("\n"):
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        if name and data:
            events.append((name, json.loads(data)))
    return events
