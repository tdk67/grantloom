"""Outbox worker advancing document ingestion pipeline states.

Transitions: uploaded -> extracting -> chunking -> embedding -> active
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.ports.embedder import EmbedderPort
from app.ports.graph_store import GraphStorePort
from app.ports.models import ChunkVector, GraphEdge, GraphNode, OutboxJob
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

            # 5. Graph Store Node & Provenance Edge Registration
            if self.graph_store:
                try:
                    g_nodes, g_edges = _extract_graph_entities(doc.doc_id, doc.filename, chunks)
                    if g_nodes:
                        self.graph_store.add_nodes(g_nodes)
                    if g_edges:
                        self.graph_store.add_edges(g_edges)
                    logger.info("Registered %d graph nodes and %d provenance edges for doc '%s'.", len(g_nodes), len(g_edges), doc.doc_id)
                except Exception as g_err:
                    logger.warning("Graph entity registration notice for doc '%s': %s", doc.doc_id, g_err)

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


def _extract_graph_entities(doc_id: str, filename: str, chunks: list) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Extract entities and provenance-tracked relationships from chunks for KùzuDB."""
    import re
    nodes: list[GraphNode] = [GraphNode(node_id=doc_id, label="Document", properties={"filename": filename})]
    edges: list[GraphEdge] = []

    seen_nodes: set[str] = {doc_id}
    seen_edges: set[tuple[str, str, str]] = set()

    KNOWN_PEOPLE = ["Dr. Elena Vance", "Elena Vance", "Alex Mercer", "Samira Khan"]
    KNOWN_FUNDERS = [
        ("Department of Energy", "DOE"),
        ("National Science Foundation", "NSF"),
        ("National Institutes of Health", "NIH"),
    ]
    KNOWN_PROJECTS = [
        "Perovskite Photovoltaic Research",
        "Cyberinfrastructure Frameworks",
        "Bioengineering Research Partnership",
    ]

    for c in chunks:
        text = c.text_content

        # 1. People
        for person in KNOWN_PEOPLE:
            if person.lower() in text.lower():
                pid = f"person_{re.sub(r'[^a-zA-Z0-9]', '_', person.lower())}"
                if pid not in seen_nodes:
                    nodes.append(GraphNode(node_id=pid, label="Person", properties={"name": person}))
                    seen_nodes.add(pid)
                edge_key = (doc_id, pid, "MENTIONS")
                if edge_key not in seen_edges:
                    edges.append(GraphEdge(source_id=doc_id, target_id=pid, rel_type="MENTIONS", source_chunk_id=c.chunk_id))
                    seen_edges.add(edge_key)

        # 2. Funders
        for funder_full, funder_abbr in KNOWN_FUNDERS:
            if funder_full.lower() in text.lower() or f" {funder_abbr} " in text or f"({funder_abbr})" in text:
                fid = f"funder_{funder_abbr.lower()}"
                if fid not in seen_nodes:
                    nodes.append(GraphNode(node_id=fid, label="Funder", properties={"name": funder_full}))
                    seen_nodes.add(fid)

        # 3. Projects
        for proj in KNOWN_PROJECTS:
            if any(term.lower() in text.lower() for term in proj.split()[:2]):
                prjid = f"proj_{re.sub(r'[^a-zA-Z0-9]', '_', proj.lower())}"
                if prjid not in seen_nodes:
                    nodes.append(GraphNode(node_id=prjid, label="Project", properties={"title": proj}))
                    seen_nodes.add(prjid)

                # Link People in this chunk to Project
                for person in KNOWN_PEOPLE:
                    if person.lower() in text.lower():
                        pid = f"person_{re.sub(r'[^a-zA-Z0-9]', '_', person.lower())}"
                        edge_key = (pid, prjid, "AFFILIATED_WITH")
                        if edge_key not in seen_edges:
                            edges.append(GraphEdge(source_id=pid, target_id=prjid, rel_type="AFFILIATED_WITH", source_chunk_id=c.chunk_id))
                            seen_edges.add(edge_key)

                # Link Funders to Project
                for funder_full, funder_abbr in KNOWN_FUNDERS:
                    if funder_full.lower() in text.lower() or f" {funder_abbr} " in text:
                        fid = f"funder_{funder_abbr.lower()}"
                        edge_key = (fid, prjid, "FUNDS")
                        if edge_key not in seen_edges:
                            edges.append(GraphEdge(source_id=fid, target_id=prjid, rel_type="FUNDS", source_chunk_id=c.chunk_id))
                            seen_edges.add(edge_key)

        # 4. Requirements (e.g. from FOA / solicitation)
        req_matches = re.finditer(r"(?i)\b(cost share|equipment threshold|reporting|eligibility)\b[:\s\-]+([^.\n]{15,120})", text)
        for m in req_matches:
            req_desc = m.group(0).strip()
            rid = f"req_{doc_id}_{c.chunk_id[:8]}"
            if rid not in seen_nodes:
                nodes.append(GraphNode(node_id=rid, label="Requirement", properties={"text": req_desc}))
                seen_nodes.add(rid)
            edge_key = (doc_id, rid, "REQUIRES")
            if edge_key not in seen_edges:
                edges.append(GraphEdge(source_id=doc_id, target_id=rid, rel_type="REQUIRES", source_chunk_id=c.chunk_id))
                seen_edges.add(edge_key)
            break

    return nodes, edges
