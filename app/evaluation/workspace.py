"""Isolated evaluation workspace manager with multi-document corpus scaling."""

from __future__ import annotations

import gc
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from app.adapters.chroma_adapter import ChromaVectorStoreAdapter
from app.adapters.decision_backends import JevDecisionBackend, LLMJudgeDecisionBackend
from app.adapters.kuzu_adapter import KuzuGraphStoreAdapter
from app.adapters.ollama_client import OllamaClientAdapter
from app.adapters.openrouter_client import OpenRouterClientAdapter
from app.adapters.sqlite_registry import SqliteRegistryAdapter
from app.adapters.sqlite_structured_store import SqliteStructuredStoreAdapter
from app.core.config import Settings, get_settings
from app.db import migrator
from app.ports.decision_backend import DecisionBackendPort
from app.ports.llm_client import LLMClientPort
from app.services.ask_service import AskService
from app.services.chunker import DocumentChunker
from app.services.extractor import DocumentExtractor
from app.services.guardrails import IngestionScanner
from app.services.inference import build_embedder, evaluator_independence_problems
from app.services.ingest_service import IngestionService
from app.services.key_vault import EphemeralKeyVault
from app.services.retrieval_service import HybridRetrievalService
from app.synthetic.generator import SyntheticDataGenerator
from app.verification.sql_guard import SqlGuard
from app.workers.ingest_worker import IngestWorker

logger = logging.getLogger("grantloom.evaluation.workspace")


def _eval_workspace_settings(base: Settings, workspace: Path) -> Settings:
    database = base.database.model_copy(update={
        "sqlite_path": str(workspace / "grantloom_eval.db"),
        "chroma_path": str(workspace / "chromadb_eval"),
        "kuzu_path": str(workspace / "kuzudb_eval"),
        "uploads_path": str(workspace / "uploads_eval"),
        "synthetic_path": str(workspace / "synthetic_eval"),
    })
    return base.model_copy(update={"database": database})


@dataclass
class EvalWorkspace:
    """Encapsulates an isolated evaluation environment with its own databases and services."""
    root_dir: Path
    settings: Settings
    registry: SqliteRegistryAdapter
    vector_store: ChromaVectorStoreAdapter
    structured_store: SqliteStructuredStoreAdapter
    graph_store: KuzuGraphStoreAdapter
    ask_service: AskService
    evaluator: Optional[DecisionBackendPort]
    evaluator_label: str
    writer_label: str
    generated_files: list[dict[str, Any]] = field(default_factory=list)
    solicitation_count: int = 10
    api_key: Optional[str] = None
    _is_temporary: bool = True

    def close(self) -> None:
        """Safely release database connections and locks."""
        if self.graph_store is not None:
            self.graph_store.close()
        if self.vector_store is not None:
            self.vector_store.close()
        self.registry.engine.dispose()
        gc.collect()

    def cleanup(self) -> None:
        """Close connections and delete the workspace directory if temporary."""
        self.close()
        if self._is_temporary and self.root_dir.exists():
            try:
                shutil.rmtree(self.root_dir)
            except OSError as exc:
                logger.warning("Could not cleanly delete temporary eval workspace %s: %s", self.root_dir, exc)

    def __enter__(self) -> "EvalWorkspace":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.cleanup()


async def provision_eval_workspace(
    solicitation_count: int = 10,
    workspace_dir: Optional[Path] = None,
    writer_provider: str = "openrouter",
    writer_model: Optional[str] = None,
    judge_model: Optional[str] = None,
    evaluator_model: Optional[str] = None,
    custom_llm_client: Optional[LLMClientPort] = None,
    custom_decision_backend: Optional[DecisionBackendPort] = None,
) -> EvalWorkspace:
    """Builds and populates an isolated multi-document evaluation workspace.

    Args:
        solicitation_count: Number of distinct funding solicitations to generate (1 to 20).
        workspace_dir: Directory to use; if None, a temporary directory is created.
        writer_provider: 'openrouter' or 'ollama'.
        writer_model: LLM model name for writing answers.
        judge_model: Model name for guardrails and routing.
        evaluator_model: Model name for independent evaluation (must differ from writer).
        custom_llm_client: Optional custom LLM client.
        custom_decision_backend: Optional custom decision backend.
    """
    is_temp = workspace_dir is None
    root = workspace_dir or Path(tempfile.mkdtemp(prefix=f"grantloom_eval_n{solicitation_count}_"))
    root.mkdir(parents=True, exist_ok=True)

    base_settings = get_settings()
    settings = _eval_workspace_settings(base_settings, root)

    # 1. Initialize databases and schemas
    migrator.upgrade("head", db_url=f"sqlite:///{Path(settings.database.sqlite_path).as_posix()}")
    registry = SqliteRegistryAdapter(settings.database.sqlite_path)
    structured_store = SqliteStructuredStoreAdapter(
        settings.database.sqlite_path, SqlGuard(settings.sql_guard.max_rows, settings.sql_guard.timeout_seconds)
    )
    graph_store = KuzuGraphStoreAdapter(settings.database.kuzu_path)

    # 2. Configure LLM clients & independent evaluator
    api_key: Optional[str] = None
    if writer_provider == "openrouter":
        api_key = settings.secrets.openrouter_api_key
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required in .env for evaluation runs.")
        client = OpenRouterClientAdapter(
            base_url=settings.llm.openrouter_base_url,
            timeout_seconds=settings.timeouts.openrouter_seconds,
            log_full_prompts=settings.logging.log_full_prompts,
        )
        await client.validate_api_key(api_key)
        resolved_writer_model = writer_model or settings.models.writer_model
        resolved_judge_model = judge_model or settings.models.judge_model
        resolved_eval_model = evaluator_model or settings.models.judge_model
    elif writer_provider == "ollama":
        client = OllamaClientAdapter(
            base_url=settings.llm.ollama_base_url,
            timeout_seconds=settings.timeouts.ollama_seconds,
            probe_timeout_seconds=settings.timeouts.probe_seconds,
            log_full_prompts=settings.logging.log_full_prompts,
        )
        resolved_writer_model = writer_model or settings.llm.ollama_writer_model
        resolved_judge_model = judge_model or settings.llm.ollama_judge_model
        resolved_eval_model = evaluator_model or settings.llm.ollama_judge_model
    else:
        client = custom_llm_client
        resolved_writer_model = writer_model or "custom"
        resolved_judge_model = judge_model or "custom"
        resolved_eval_model = evaluator_model or "custom"

    # Enforce model independence between writer and evaluator
    independence_issues = evaluator_independence_problems(resolved_writer_model, resolved_eval_model)
    if independence_issues:
        logger.warning("Evaluator independence warning: %s", independence_issues[0])

    llm: LLMClientPort = custom_llm_client or client
    base_judge = LLMJudgeDecisionBackend(client, resolved_judge_model, settings.decision)
    if custom_decision_backend:
        decision_backend = custom_decision_backend
    elif settings.decision.backend == "jev":
        decision_backend = JevDecisionBackend(
            api_key=api_key or "",
            model=settings.decision.jev_model,
            base_url=settings.decision.jev_base_url,
            timeout_seconds=settings.timeouts.openrouter_seconds,
            config=settings.decision,
            judge_delegate=base_judge,
        )
    else:
        decision_backend = base_judge
    evaluator = LLMJudgeDecisionBackend(client, resolved_eval_model, settings.decision)

    key_vault = EphemeralKeyVault()
    embedder = build_embedder(settings)
    vector_store = ChromaVectorStoreAdapter(settings.database.chroma_path, embedder.fingerprint)

    # 3. Generate multi-document corpus
    generator = SyntheticDataGenerator(output_dir=Path(settings.database.synthetic_path))
    generated = generator.generate_all(quiet=True, solicitation_count=solicitation_count)

    # 4. Ingest corpus into isolated databases
    worker = IngestWorker(
        registry=registry,
        vector_store=vector_store,
        embedder=embedder,
        scanner=IngestionScanner(decision_backend, settings.guardrails),
        extractor=DocumentExtractor(settings.limits.max_pages_per_doc, settings.chunking.rows_per_chunk),
        chunker=DocumentChunker(settings.chunking.target_chunk_size, settings.chunking.chunk_overlap),
        ingestion=settings.ingestion,
        quarantine_retention_days=settings.guardrails.quarantine_retention_days,
        structured_store=structured_store,
        graph_store=graph_store,
        key_vault=key_vault,
    )
    ingest = IngestionService(registry=registry, settings=settings)

    # Upload and process documents (solicitations first, then proposals/spreadsheets)
    for info in sorted(generated["files"], key=lambda f: not f["filename"].startswith(("DOE", "EPA", "NSF", "NIH", "ARPA", "NASA", "DOD", "USDA"))):
        file_path = Path(info["path"])
        uploaded = ingest.upload_document(info["filename"], file_path.read_bytes())
        if api_key:
            key_vault.put(uploaded.doc_id, api_key)
        while await worker.process_next_job():
            pass

    not_active = [d.filename for d in registry.list_documents() if d.state != "active"]
    if not_active:
        raise RuntimeError(f"Corpus ingestion failed for {len(not_active)} documents: {not_active}")

    # 5. Build isolated AskService
    ask_service = AskService(
        retrieval_service=HybridRetrievalService(vector_store, registry, embedder, settings),
        registry=registry,
        llm_client=llm,
        settings=settings,
        decision_backend=decision_backend,
        writer_model=resolved_writer_model,
        egress={"mode": "eval", "writer": f"{writer_provider}:{resolved_writer_model}", "provider": writer_provider},
        structured_store=structured_store,
        graph_store=graph_store,
    )

    return EvalWorkspace(
        root_dir=root,
        settings=settings,
        registry=registry,
        vector_store=vector_store,
        structured_store=structured_store,
        graph_store=graph_store,
        ask_service=ask_service,
        evaluator=evaluator,
        evaluator_label=f"{writer_provider}:{resolved_eval_model}",
        writer_label=f"{writer_provider}:{resolved_writer_model}",
        generated_files=generated["files"],
        solicitation_count=solicitation_count,
        api_key=api_key,
        _is_temporary=is_temp,
    )
