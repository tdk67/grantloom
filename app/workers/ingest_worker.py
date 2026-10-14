"""Outbox worker: ingestion pipeline and deletion saga.

Ingestion stages: extracting -> chunking -> scanning -> embedding -> indexing -> active.
- Stage-idempotent (NFR-5): every attempt first purges derived data from earlier attempts
  (chunks/FTS, vectors, tables, graph edges), so retries never duplicate index entries.
- Failed attempts are retried up to ingestion.max_job_attempts; jobs left 'in_progress' by a crash
  are reclaimed at startup and after ingestion.stale_job_timeout_seconds.
- CPU-bound extraction/chunking runs in a worker thread so the event loop keeps serving requests.
Deletion saga (FR-I6): DELETE marks the document 'deleting' and enqueues a 'deleting' job that purges
vectors, tables, graph edges, the registry rows and the stored file; failures are retried.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

from app.core.config import IngestionConfig
from app.core.errors import GrantLoomError
from app.core.metrics import CHUNKS, DOCUMENTS, INGESTION_JOBS, VECTORS
from app.ports.embedder import EmbedderPort
from app.ports.graph_store import GraphStorePort
from app.ports.models import ChunkVector, DocumentRecord, OutboxJob
from app.ports.registry import RegistryPort
from app.ports.structured_store import StructuredStorePort
from app.ports.vector_store import VectorStorePort
from app.services.chunker import DocumentChunker
from app.services.extractor import DocumentExtractor
from app.services.graph_extraction import GraphEntityExtractor
from app.services.guardrails import IngestionScanner
from app.services.key_vault import EphemeralKeyVault

logger = logging.getLogger("grantloom.workers.ingest_worker")

DELETE_STAGE = "deleting"
DOCUMENT_STATES = ("uploaded", "extracting", "chunking", "scanning", "embedding", "indexing", "active", "quarantined", "failed", "deleting")


class IngestWorker:
    """Consumes outbox jobs: document ingestion and the deletion saga."""

    def __init__(
        self,
        registry: RegistryPort,
        vector_store: VectorStorePort,
        embedder: EmbedderPort,
        scanner: IngestionScanner,
        extractor: DocumentExtractor,
        chunker: DocumentChunker,
        ingestion: IngestionConfig,
        quarantine_retention_days: int,
        structured_store: Optional[StructuredStorePort] = None,
        graph_store: Optional[GraphStorePort] = None,
        key_vault: Optional[EphemeralKeyVault] = None,
        tier2_requires_key: bool = False,
    ) -> None:
        self.registry = registry
        self.vector_store = vector_store
        self.embedder = embedder
        self.scanner = scanner
        self.extractor = extractor
        self.chunker = chunker
        self.config = ingestion
        self.quarantine_retention_days = quarantine_retention_days
        self.structured_store = structured_store
        self.graph_store = graph_store
        self.key_vault = key_vault or EphemeralKeyVault()
        self.tier2_requires_key = tier2_requires_key
        self.graph_extractor = GraphEntityExtractor()
        self._running = False

    # ------------------------------------------------------------------ job dispatch

    async def process_next_job(self) -> bool:
        """Claim and process a single pending outbox job. Returns True if a job was processed."""
        job: Optional[OutboxJob] = self.registry.claim_next_job()
        if not job:
            return False

        doc = self.registry.get_document(job.doc_id)
        if not doc:
            logger.error("Outbox job '%s' refers to missing document '%s'.", job.job_id, job.doc_id)
            self.registry.update_job_status(job.job_id, status="failed", error="Document not found")
            return True

        if job.stage == DELETE_STAGE:
            await self._run_deletion(job, doc)
        elif doc.state == "deleting":
            logger.info("Skipping ingestion job '%s': document '%s' is being deleted.", job.job_id, doc.doc_id)
            self.registry.complete_job(job.job_id)
        else:
            await self._run_ingestion(job, doc)
        self.refresh_gauges()
        return True

    def _progress(self, job: OutboxJob, doc: DocumentRecord, state: str, pct: int, page_count: Optional[int] = None) -> None:
        self.registry.update_job_stage(job.job_id, state)
        self.registry.update_document_state(
            doc.doc_id, state=state, metadata_update={"stage": state, "progress_pct": pct}, page_count=page_count,
        )

    # ------------------------------------------------------------------ ingestion

    def _purge_derived(self, doc_id: str) -> None:
        """Delete-before-insert: remove anything a previous attempt may have written."""
        self.registry.delete_chunks_for_document(doc_id)
        self.vector_store.delete_document(doc_id)
        if self.structured_store:
            self.structured_store.delete_document_tables(doc_id)
        if self.graph_store:
            self.graph_store.delete_document(doc_id)

    def _known_funders(self) -> dict[str, str]:
        if not self.graph_store:
            return {}
        known = {}
        for row in self.graph_store.query_template("list_funders", {}):
            name = row["name"]
            if name.endswith(")") and "(" in name:
                full, acronym = name[:-1].rsplit(" (", 1)
                known[acronym] = full
        return known

    async def _run_ingestion(self, job: OutboxJob, doc: DocumentRecord) -> None:
        logger.info("Ingesting document '%s' (job %s, attempt %d).", doc.doc_id, job.job_id, job.attempts)
        try:
            self._purge_derived(doc.doc_id)

            self._progress(job, doc, "extracting", 10)
            extraction = await asyncio.to_thread(self.extractor.extract, doc.doc_id, doc.file_path, doc.filename)

            self._progress(job, doc, "chunking", 30, page_count=extraction.page_count)
            chunks = await asyncio.to_thread(self.chunker.chunk_document, extraction)

            self._progress(job, doc, "scanning", 40)
            api_key = self.key_vault.get(doc.doc_id)
            unavailable = "no BYOK key available for this upload" if (self.tier2_requires_key and not api_key) else None
            report = await self.scanner.scan(doc.doc_id, chunks, api_key, tier2_unavailable_reason=unavailable)
            scan_meta = {
                "tier1_flagged_chunks": len(report.tier1_flagged_chunk_ids),
                "tainted_chunks": report.tainted_chunk_ids,
                "tier2_status": report.tier2_status,
            }
            if report.quarantined:
                logger.warning("Document '%s' QUARANTINED: %d tainted chunk(s).", doc.doc_id, len(report.tainted_chunk_ids))
                self.registry.update_document_state(
                    doc.doc_id,
                    state="quarantined",
                    error_message=f"Quarantined: {len(report.tainted_chunk_ids)} tainted chunk(s) exceed the threshold.",
                    metadata_update={"stage": "quarantined", "progress_pct": 100, "scan": scan_meta},
                )
                self.registry.complete_job(job.job_id)
                self.registry.log_audit_event("document_quarantined", doc.doc_id, {"scan": scan_meta, "reasons": report.reasons})
                self.key_vault.discard(doc.doc_id)
                INGESTION_JOBS.labels(outcome="quarantined").inc()
                return

            tainted = set(report.tainted_chunk_ids)
            for c in chunks:
                if c.chunk_id in tainted:
                    c.metadata["tainted"] = True
            self.registry.save_chunks(chunks)

            self._progress(job, doc, "embedding", 50)
            batch_size = self.config.embed_batch_size
            for offset in range(0, len(chunks), batch_size):
                batch = chunks[offset : offset + batch_size]
                embeddings = await self.embedder.embed_batch([c.text_content for c in batch])
                self.vector_store.add_chunks([
                    ChunkVector(chunk_id=c.chunk_id, doc_id=c.doc_id, embedding=e, text=c.text_content, metadata=c.metadata)
                    for c, e in zip(batch, embeddings)
                ])
                pct = 50 + int(30 * min(len(chunks), offset + batch_size) / max(1, len(chunks)))
                self.registry.update_document_state(doc.doc_id, state="embedding", metadata_update={"progress_pct": pct})

            self._progress(job, doc, "indexing", 85)
            if self.structured_store:
                for sheet_name, sheet in extraction.structured_sheets.items():
                    self.structured_store.load_sheet(doc.doc_id, sheet_name, sheet["columns"], sheet["rows"])
            if self.graph_store:
                try:
                    nodes, edges = self.graph_extractor.extract(doc.doc_id, doc.filename, chunks, self._known_funders())
                    self.graph_store.add_nodes(nodes)
                    self.graph_store.add_edges(edges)
                    logger.info("Graph indexed for '%s': %d node(s), %d edge(s).", doc.doc_id, len(nodes), len(edges))
                except Exception as g_exc:
                    logger.error("Graph store indexing failed for '%s': %s", doc.doc_id, g_exc)
                    raise StorageError(f"Graph store indexing failed for document '{doc.filename}': {g_exc}")

            self.registry.update_document_state(
                doc.doc_id,
                state="active",
                error_message="",
                metadata_update={
                    "stage": "active",
                    "progress_pct": 100,
                    "chunk_count": len(chunks),
                    "page_count": extraction.page_count,
                    "embedding_fingerprint": self.embedder.fingerprint,
                    "scan": scan_meta,
                },
                page_count=extraction.page_count,
            )
            self.registry.complete_job(job.job_id)
            self.registry.log_audit_event("document_active", doc.doc_id, {"chunk_count": len(chunks), "scan": scan_meta})
            self.key_vault.discard(doc.doc_id)
            INGESTION_JOBS.labels(outcome="active").inc()
            logger.info("Document '%s' active (%d chunks, %d pages).", doc.doc_id, len(chunks), extraction.page_count)

        except GrantLoomError as exc:
            self._fail(job, doc, exc.detail)
        except Exception as exc:  # unexpected bug: record it on the job instead of killing the worker loop
            logger.exception("Unexpected ingestion failure for '%s'.", doc.doc_id)
            self._fail(job, doc, f"{type(exc).__name__}: {exc}")

    def _fail(self, job: OutboxJob, doc: DocumentRecord, error: str) -> None:
        retry = self.registry.retry_or_fail_job(job.job_id, error, self.config.max_job_attempts)
        if retry:
            logger.warning("Ingestion of '%s' failed (attempt %d), will retry: %s", doc.doc_id, job.attempts, error)
            self.registry.update_document_state(
                doc.doc_id, state="uploaded", error_message=f"Attempt {job.attempts} failed: {error}",
                metadata_update={"stage": "retry_pending"},
            )
            INGESTION_JOBS.labels(outcome="retry").inc()
            return
        logger.error("Ingestion of '%s' failed permanently: %s", doc.doc_id, error)
        self.registry.update_document_state(doc.doc_id, state="failed", error_message=error, metadata_update={"stage": "failed"})
        self.registry.log_audit_event("document_processing_failed", doc.doc_id, {"error": error})
        self.key_vault.discard(doc.doc_id)
        INGESTION_JOBS.labels(outcome="failed").inc()

    # ------------------------------------------------------------------ deletion saga

    async def _run_deletion(self, job: OutboxJob, doc: DocumentRecord) -> None:
        try:
            self._purge_derived(doc.doc_id)
            self.registry.delete_document(doc.doc_id)
            if doc.file_path:
                Path(doc.file_path).unlink(missing_ok=True)
            self.registry.log_audit_event("document_deleted", None, {"doc_id": doc.doc_id, "filename": doc.filename})
            INGESTION_JOBS.labels(outcome="deleted").inc()
            logger.info("Deletion saga completed for '%s'.", doc.doc_id)
        except (GrantLoomError, OSError) as exc:
            detail = exc.detail if isinstance(exc, GrantLoomError) else str(exc)
            retry = self.registry.retry_or_fail_job(job.job_id, detail, self.config.max_job_attempts)
            self.registry.update_document_state(
                doc.doc_id, state="deleting", error_message=f"Deletion {'will retry' if retry else 'failed'}: {detail}",
            )
            logger.error("Deletion saga for '%s' failed (retry=%s): %s", doc.doc_id, retry, detail)

    # ------------------------------------------------------------------ maintenance

    def run_maintenance(self) -> None:
        """Reclaim stale jobs and enqueue deletion of quarantined documents past retention."""
        reclaimed = self.registry.requeue_stale_jobs(self.config.stale_job_timeout_seconds)
        if reclaimed:
            logger.warning("Reclaimed %d stale outbox job(s).", reclaimed)
        for doc in self.registry.list_documents_older_than("quarantined", self.quarantine_retention_days):
            logger.info("Quarantine retention expired for '%s'; enqueuing deletion.", doc.doc_id)
            self.registry.update_document_state(doc.doc_id, state="deleting", metadata_update={"stage": "deleting"})
            self.registry.create_outbox_job(doc.doc_id, stage=DELETE_STAGE)
        self.refresh_gauges()

    def refresh_gauges(self) -> None:
        counts = {state: 0 for state in DOCUMENT_STATES}
        for d in self.registry.list_documents():
            counts[d.state] = counts.get(d.state, 0) + 1
        for state, n in counts.items():
            DOCUMENTS.labels(state=state).set(n)
        CHUNKS.set(self.registry.count_chunks())
        VECTORS.set(self.vector_store.count())

    async def run_loop(self) -> None:
        """Continuously poll and process outbox jobs."""
        self._running = True
        reclaimed = self.registry.requeue_stale_jobs(None)
        if reclaimed:
            logger.warning("Startup: reclaimed %d job(s) left in progress by a previous run.", reclaimed)
        self.run_maintenance()
        last_maintenance = time.monotonic()
        logger.info("IngestWorker background loop started.")
        while self._running:
            try:
                if time.monotonic() - last_maintenance > self.config.maintenance_interval_seconds:
                    self.run_maintenance()
                    last_maintenance = time.monotonic()
                if not await self.process_next_job():
                    await asyncio.sleep(self.config.worker_poll_interval_seconds)
            except asyncio.CancelledError:
                break
            except GrantLoomError as exc:
                logger.error("IngestWorker loop error: %s", exc.detail)
                await asyncio.sleep(self.config.worker_poll_interval_seconds)

    def stop(self) -> None:
        self._running = False
