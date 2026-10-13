"""Outbox worker advancing document ingestion pipeline states.

Transitions: uploaded -> extracting -> chunking -> embedding -> active
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.ports.embedder import EmbedderPort
from app.ports.graph_store import GraphStorePort
from app.ports.models import ChunkVector, GraphNode, OutboxJob
from app.ports.registry import RegistryPort
from app.ports.structured_store import StructuredStorePort
from app.ports.vector_store import VectorStorePort
from app.services.chunker import DocumentChunker
from app.services.extractor import DocumentExtractor
from app.services.guardrails import IngestionScanner

logger = logging.getLogger("grantloom.workers.ingest_worker")


class IngestWorker:
    """Consumes outbox jobs and progresses documents through extraction, chunking, and vector indexing."""

    def __init__(
        self,
        registry: RegistryPort,
        vector_store: VectorStorePort,
        embedder: EmbedderPort,
        structured_store: Optional[StructuredStorePort] = None,
        graph_store: Optional[GraphStorePort] = None,
        scanner: Optional[IngestionScanner] = None,
        extractor: Optional[DocumentExtractor] = None,
        chunker: Optional[DocumentChunker] = None,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        self.registry = registry
        self.vector_store = vector_store
        self.embedder = embedder
        self.structured_store = structured_store
        self.graph_store = graph_store
        self.scanner = scanner or IngestionScanner()
        self.extractor = extractor or DocumentExtractor()
        self.chunker = chunker or DocumentChunker()
        self.poll_interval = poll_interval_seconds
        self._running = False


    async def process_next_job(self) -> bool:
        """Claim and process a single pending outbox job. Returns True if a job was processed."""
        job: Optional[OutboxJob] = self.registry.claim_next_job()
        if not job:
            return False

        doc = self.registry.get_document(job.doc_id)
        if not doc:
            logger.error("Outbox job '%s' refers to non-existent document '%s'.", job.job_id, job.doc_id)
            self.registry.update_job_status(job.job_id, status="failed", error="Document not found")
            return True

        logger.info("Processing outbox job '%s' for document '%s' (Stage: %s).", job.job_id, doc.doc_id, job.stage)

        try:
            # 1. Extraction Stage
            self.registry.update_document_state(doc.doc_id, state="extracting")
            extraction_result = self.extractor.extract(
                doc_id=doc.doc_id,
                file_path=doc.file_path,
                filename=doc.filename,
            )

            # 2. Chunking Stage
            self.registry.update_document_state(
                doc.doc_id,
                state="chunking",
                page_count=extraction_result.page_count,
            )
            chunks = self.chunker.chunk_document(extraction_result)

            # 2.5 Security Ingestion Scanner (Quarantine gate)
            if self.scanner:
                is_quarantined, tainted_count, reasons = await self.scanner.scan_chunks(chunks)
                if is_quarantined:
                    logger.warning(
                        "Document '%s' was QUARANTINED due to adversarial content (%d chunks tainted): %s",
                        doc.doc_id, tainted_count, reasons
                    )
                    self.registry.update_document_state(
                        doc.doc_id,
                        state="quarantined",
                        error_message=f"Quarantined: {tainted_count} security taint detections.",
                    )
                    self.registry.complete_job(job.job_id)
                    self.registry.log_audit_event(
                        event_type="document_quarantined",
                        doc_id=doc.doc_id,
                        details={"tainted_chunks": tainted_count, "reasons": reasons},
                    )
                    return True

            self.registry.save_chunks(chunks)


            # 3. Embedding Stage
            self.registry.update_document_state(doc.doc_id, state="embedding")
            texts = [c.text_content for c in chunks]
            embeddings = await self.embedder.embed_batch(texts)

            chunk_vectors = [
                ChunkVector(
                    chunk_id=c.chunk_id,
                    doc_id=c.doc_id,
                    embedding=embeddings[idx],
                    text=c.text_content,
                    metadata=c.metadata,
                )
                for idx, c in enumerate(chunks)
            ]
            self.vector_store.add_chunks(chunk_vectors)

            # 4. Structured Tabular Storage (Spreadsheets)
            if self.structured_store and extraction_result.structured_sheets:
                for sheet_name, sheet_data in extraction_result.structured_sheets.items():
                    table_name = f"doc_{doc.doc_id}_{sheet_name}"
                    self.structured_store.load_sheet(
                        table_name=table_name,
                        columns=sheet_data["columns"],
                        rows=sheet_data["rows"],
                    )

            # 5. Graph Store Node Registration
            if self.graph_store:
                try:
                    self.graph_store.add_nodes([
                        GraphNode(node_id=doc.doc_id, label="Document", properties={"filename": doc.filename})
                    ])
                except Exception as g_err:
                    logger.warning("Graph node registration notice for doc '%s': %s", doc.doc_id, g_err)

            # 6. Active State Completion
            self.registry.update_document_state(
                doc.doc_id,
                state="active",
                metadata_update={
                    "chunk_count": len(chunks),
                    "page_count": extraction_result.page_count,
                },
                page_count=extraction_result.page_count,
            )
            self.registry.complete_job(job.job_id)
            self.registry.log_audit_event(
                event_type="document_active",
                doc_id=doc.doc_id,
                details={"chunk_count": len(chunks), "pages": extraction_result.page_count},
            )

            logger.info(
                "Document '%s' successfully indexed as active (%d chunks, %d pages).",
                doc.doc_id,
                len(chunks),
                extraction_result.page_count,
            )
            return True

        except Exception as exc:
            logger.exception("Failed to process ingestion for document '%s': %s", doc.doc_id, exc)
            self.registry.update_document_state(
                doc.doc_id,
                state="failed",
                error_message=str(exc),
            )
            self.registry.update_job_status(job.job_id, status="failed", error=str(exc))
            self.registry.log_audit_event(
                event_type="document_processing_failed",
                doc_id=doc.doc_id,
                details={"error": str(exc)},
            )
            return True

    async def run_loop(self) -> None:
        """Continuously poll and process outbox jobs."""
        self._running = True
        logger.info("IngestWorker background loop started.")
        while self._running:
            try:
                processed = await self.process_next_job()
                if not processed:
                    await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in IngestWorker loop: %s", exc)
                await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        """Stop worker loop."""
        self._running = False
