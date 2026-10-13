"""SQLite implementation of Document Registry and Outbox Port."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional, Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.core.errors import StorageError
from app.ports.models import DocumentChunk, DocumentRecord, KeywordSearchResult, OutboxJob
from app.ports.registry import RegistryPort

logger = logging.getLogger("grantloom.adapters.sqlite_registry")


class SqliteRegistryAdapter(RegistryPort):
    """Thread-safe SQLite adapter for document registry, chunks, and outbox queue."""

    def __init__(self, db_path_or_url: str | Path | Engine) -> None:
        if isinstance(db_path_or_url, Engine):
            self.engine = db_path_or_url
        else:
            raw_path = str(db_path_or_url)
            if not raw_path.startswith("sqlite"):
                db_path = Path(raw_path)
                db_path.parent.mkdir(parents=True, exist_ok=True)
                url = f"sqlite:///{db_path.as_posix()}"
            else:
                url = raw_path
            self.engine = create_engine(url, connect_args={"check_same_thread": False})

        # Ensure WAL mode and foreign keys
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL;"))
            conn.execute(text("PRAGMA foreign_keys=ON;"))
            conn.commit()

    def save_document(self, record: DocumentRecord) -> None:
        """Register a new document record."""
        query = text("""
            INSERT INTO documents (
                doc_id, filename, content_hash, file_type, page_count,
                state, version, file_path, error_message, metadata_json
            ) VALUES (
                :doc_id, :filename, :content_hash, :file_type, :page_count,
                :state, :version, :file_path, :error_message, :metadata_json
            )
        """)
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    query,
                    {
                        "doc_id": record.doc_id,
                        "filename": record.filename,
                        "content_hash": record.content_hash,
                        "file_type": record.file_type,
                        "page_count": record.page_count,
                        "state": record.state,
                        "version": record.version,
                        "file_path": record.file_path,
                        "error_message": record.error_message,
                        "metadata_json": json.dumps(record.metadata),
                    },
                )
        except Exception as exc:
            raise StorageError(f"Failed to save document '{record.doc_id}': {exc}") from exc

    def get_document(self, doc_id: str) -> Optional[DocumentRecord]:
        """Fetch document record by unique document ID."""
        query = text("""
            SELECT doc_id, filename, content_hash, file_type, page_count,
                   state, version, file_path, error_message, metadata_json, created_at
            FROM documents WHERE doc_id = :doc_id
        """)
        try:
            with self.engine.connect() as conn:
                row = conn.execute(query, {"doc_id": doc_id}).mappings().fetchone()
                if not row:
                    return None
                return self._row_to_document_record(row)
        except Exception as exc:
            raise StorageError(f"Failed to fetch document '{doc_id}': {exc}") from exc

    def get_document_by_hash(self, content_hash: str) -> Optional[DocumentRecord]:
        """Fetch document record by canonical SHA-256 content hash."""
        query = text("""
            SELECT doc_id, filename, content_hash, file_type, page_count,
                   state, version, file_path, error_message, metadata_json, created_at
            FROM documents WHERE content_hash = :content_hash
            ORDER BY created_at DESC LIMIT 1
        """)
        try:
            with self.engine.connect() as conn:
                row = conn.execute(query, {"content_hash": content_hash}).mappings().fetchone()
                if not row:
                    return None
                return self._row_to_document_record(row)
        except Exception as exc:
            raise StorageError(f"Failed to fetch document by hash '{content_hash}': {exc}") from exc

    def list_documents(self, state: Optional[str] = None) -> list[DocumentRecord]:
        """List all registered documents sorted by newest created first, optionally filtering by state."""
        if state:
            query = text("""
                SELECT doc_id, filename, content_hash, file_type, page_count,
                       state, version, file_path, error_message, metadata_json, created_at
                FROM documents WHERE state = :state ORDER BY created_at DESC
            """)
            params = {"state": state}
        else:
            query = text("""
                SELECT doc_id, filename, content_hash, file_type, page_count,
                       state, version, file_path, error_message, metadata_json, created_at
                FROM documents ORDER BY created_at DESC
            """)
            params = {}
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(query, params).mappings().fetchall()
                return [self._row_to_document_record(row) for row in rows]
        except Exception as exc:
            raise StorageError(f"Failed to list documents: {exc}") from exc


    def update_document_state(
        self,
        doc_id: str,
        state: str,
        metadata_update: Optional[dict[str, Any]] = None,
        error_message: Optional[str] = None,
        page_count: Optional[int] = None,
    ) -> None:
        """Transition document state machine and update metadata."""
        current = self.get_document(doc_id)
        if not current:
            raise StorageError(f"Document '{doc_id}' not found for state update.")

        merged_metadata = dict(current.metadata)
        if metadata_update:
            merged_metadata.update(metadata_update)

        updates = ["state = :state", "metadata_json = :metadata_json", "updated_at = CURRENT_TIMESTAMP"]
        params: dict[str, Any] = {
            "doc_id": doc_id,
            "state": state,
            "metadata_json": json.dumps(merged_metadata),
        }

        if error_message is not None:
            updates.append("error_message = :error_message")
            params["error_message"] = error_message

        if page_count is not None:
            updates.append("page_count = :page_count")
            params["page_count"] = page_count

        sql = f"UPDATE documents SET {', '.join(updates)} WHERE doc_id = :doc_id"
        try:
            with self.engine.begin() as conn:
                conn.execute(text(sql), params)
        except Exception as exc:
            raise StorageError(f"Failed to update document '{doc_id}' state to '{state}': {exc}") from exc

    def delete_document(self, doc_id: str) -> None:
        """Remove document record, chunks, and outbox jobs from SQLite."""
        try:
            with self.engine.begin() as conn:
                conn.execute(text("DELETE FROM chunks_fts WHERE doc_id = :doc_id"), {"doc_id": doc_id})
                conn.execute(text("DELETE FROM document_chunks WHERE doc_id = :doc_id"), {"doc_id": doc_id})
                conn.execute(text("DELETE FROM outbox_jobs WHERE doc_id = :doc_id"), {"doc_id": doc_id})
                conn.execute(text("DELETE FROM documents WHERE doc_id = :doc_id"), {"doc_id": doc_id})
        except Exception as exc:
            raise StorageError(f"Failed to delete document '{doc_id}': {exc}") from exc

    def count_documents(self) -> int:
        """Return total active/registered document count."""
        try:
            with self.engine.connect() as conn:
                count = conn.execute(text("SELECT COUNT(*) FROM documents")).scalar()
                return int(count or 0)
        except Exception as exc:
            raise StorageError(f"Failed to count documents: {exc}") from exc

    def create_outbox_job(self, doc_id: str, stage: str = "extracting") -> str:
        """Enqueue a background outbox processing job."""
        job_id = str(uuid.uuid4())
        query = text("""
            INSERT INTO outbox_jobs (job_id, doc_id, stage, status, attempts)
            VALUES (:job_id, :doc_id, :stage, 'pending', 0)
        """)
        try:
            with self.engine.begin() as conn:
                conn.execute(query, {"job_id": job_id, "doc_id": doc_id, "stage": stage})
            return job_id
        except Exception as exc:
            raise StorageError(f"Failed to create outbox job for document '{doc_id}': {exc}") from exc

    def claim_next_job(self) -> Optional[OutboxJob]:
        """Claim the next pending outbox job atomically."""
        select_query = text("""
            SELECT job_id, doc_id, stage, status, attempts, last_error, created_at, updated_at
            FROM outbox_jobs
            WHERE status = 'pending'
            ORDER BY created_at ASC LIMIT 1
        """)
        update_query = text("""
            UPDATE outbox_jobs
            SET status = 'in_progress', attempts = attempts + 1, updated_at = CURRENT_TIMESTAMP
            WHERE job_id = :job_id AND status = 'pending'
        """)
        try:
            with self.engine.begin() as conn:
                row = conn.execute(select_query).mappings().fetchone()
                if not row:
                    return None
                result = conn.execute(update_query, {"job_id": row["job_id"]})
                if result.rowcount == 0:
                    # Concurrently claimed by another worker
                    return None
                return OutboxJob(
                    job_id=row["job_id"],
                    doc_id=row["doc_id"],
                    stage=row["stage"],
                    status="in_progress",
                    attempts=row["attempts"] + 1,
                    last_error=row["last_error"],
                    created_at=str(row["created_at"]),
                    updated_at=str(row["updated_at"]),
                )
        except Exception as exc:
            raise StorageError(f"Failed to claim next outbox job: {exc}") from exc

    def update_job_status(
        self,
        job_id: str,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        """Update outbox job status and error message."""
        query = text("""
            UPDATE outbox_jobs
            SET status = :status, last_error = :error, updated_at = CURRENT_TIMESTAMP
            WHERE job_id = :job_id
        """)
        try:
            with self.engine.begin() as conn:
                conn.execute(query, {"job_id": job_id, "status": status, "error": error})
        except Exception as exc:
            raise StorageError(f"Failed to update job '{job_id}': {exc}") from exc

    def complete_job(self, job_id: str) -> None:
        """Mark outbox job completed and remove from active queue."""
        query = text("""
            UPDATE outbox_jobs
            SET status = 'completed', updated_at = CURRENT_TIMESTAMP
            WHERE job_id = :job_id
        """)
        try:
            with self.engine.begin() as conn:
                conn.execute(query, {"job_id": job_id})
        except Exception as exc:
            raise StorageError(f"Failed to complete job '{job_id}': {exc}") from exc

    def save_chunks(self, chunks: Sequence[DocumentChunk]) -> None:
        """Batch persist extracted document chunks with coordinates into document_chunks and chunks_fts."""
        if not chunks:
            return

        chunk_insert = text("""
            INSERT OR REPLACE INTO document_chunks (
                chunk_id, doc_id, chunk_index, page_number, sheet_name,
                row_start, row_end, line_start, line_end, char_count,
                token_count, text_content, content_hash, metadata_json
            ) VALUES (
                :chunk_id, :doc_id, :chunk_index, :page_number, :sheet_name,
                :row_start, :row_end, :line_start, :line_end, :char_count,
                :token_count, :text_content, :content_hash, :metadata_json
            )
        """)
        fts_delete = text("DELETE FROM chunks_fts WHERE chunk_id = :chunk_id")
        fts_insert = text("""
            INSERT INTO chunks_fts (chunk_id, doc_id, text_content)
            VALUES (:chunk_id, :doc_id, :text_content)
        """)

        try:
            with self.engine.begin() as conn:
                for c in chunks:
                    conn.execute(
                        chunk_insert,
                        {
                            "chunk_id": c.chunk_id,
                            "doc_id": c.doc_id,
                            "chunk_index": c.chunk_index,
                            "page_number": c.page_number,
                            "sheet_name": c.sheet_name,
                            "row_start": c.row_start,
                            "row_end": c.row_end,
                            "line_start": c.line_start,
                            "line_end": c.line_end,
                            "char_count": c.char_count,
                            "token_count": c.token_count,
                            "text_content": c.text_content,
                            "content_hash": c.content_hash,
                            "metadata_json": json.dumps(c.metadata),
                        },
                    )
                    conn.execute(fts_delete, {"chunk_id": c.chunk_id})
                    conn.execute(
                        fts_insert,
                        {
                            "chunk_id": c.chunk_id,
                            "doc_id": c.doc_id,
                            "text_content": c.text_content,
                        },
                    )
        except Exception as exc:
            raise StorageError(f"Failed to batch save {len(chunks)} chunks: {exc}") from exc

    def get_chunks_for_document(self, doc_id: str) -> list[DocumentChunk]:
        """Fetch all chunks belonging to a document."""
        query = text("""
            SELECT chunk_id, doc_id, chunk_index, page_number, sheet_name,
                   row_start, row_end, line_start, line_end, char_count,
                   token_count, text_content, content_hash, metadata_json
            FROM document_chunks
            WHERE doc_id = :doc_id
            ORDER BY chunk_index ASC
        """)
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(query, {"doc_id": doc_id}).mappings().fetchall()
                return [self._row_to_document_chunk(row) for row in rows]
        except Exception as exc:
            raise StorageError(f"Failed to fetch chunks for document '{doc_id}': {exc}") from exc

    def get_chunk(self, chunk_id: str) -> Optional[DocumentChunk]:
        """Fetch single chunk by chunk ID."""
        query = text("""
            SELECT chunk_id, doc_id, chunk_index, page_number, sheet_name,
                   row_start, row_end, line_start, line_end, char_count,
                   token_count, text_content, content_hash, metadata_json
            FROM document_chunks WHERE chunk_id = :chunk_id
        """)
        try:
            with self.engine.connect() as conn:
                row = conn.execute(query, {"chunk_id": chunk_id}).mappings().fetchone()
                if not row:
                    return None
                return self._row_to_document_chunk(row)
        except Exception as exc:
            raise StorageError(f"Failed to fetch chunk '{chunk_id}': {exc}") from exc

    def log_audit_event(
        self,
        event_type: str,
        doc_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """Persist an audit log entry."""
        log_id = str(uuid.uuid4())
        query = text("""
            INSERT INTO audit_logs (log_id, event_type, doc_id, details_json)
            VALUES (:log_id, :event_type, :doc_id, :details_json)
        """)
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    query,
                    {
                        "log_id": log_id,
                        "event_type": event_type,
                        "doc_id": doc_id,
                        "details_json": json.dumps(details or {}),
                    },
                )
        except Exception as exc:
            logger.warning("Failed to record audit event '%s': %s", event_type, exc)

    def search_bm25(self, query: str, top_k: int) -> list[KeywordSearchResult]:
        """Perform full-text BM25 search over chunks_fts table."""
        import re
        from app.core.constants import ENGLISH_STOP_WORDS

        raw_tokens = re.findall(r"\b[a-zA-Z0-9_\-]{2,}\b", query)
        if not raw_tokens:
            return []

        tokens = [t for t in raw_tokens if t.lower() not in ENGLISH_STOP_WORDS]
        if not tokens:
            return []

        match_query = " OR ".join(f'"{t}"' for t in tokens)
        sql = text("""
            SELECT f.chunk_id, f.doc_id, f.text_content, bm25(chunks_fts) as rank
            FROM chunks_fts f
            JOIN documents d ON d.doc_id = f.doc_id
            WHERE chunks_fts MATCH :match_query AND d.state = 'active'
            ORDER BY rank ASC
            LIMIT :top_k
        """)
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(sql, {"match_query": match_query, "top_k": top_k}).mappings().fetchall()
                results: list[KeywordSearchResult] = []
                for row in rows:
                    raw_rank = float(row["rank"])
                    # Negative BM25 score where more negative = higher relevance
                    score = abs(raw_rank)
                    results.append(
                        KeywordSearchResult(
                            chunk_id=row["chunk_id"],
                            doc_id=row["doc_id"],
                            score=score,
                            text=row["text_content"],
                            metadata={"bm25_rank": raw_rank},
                        )
                    )
                return results
        except Exception as exc:
            raise StorageError(f"BM25 FTS search failed: {exc}") from exc

    def delete_chunks_for_document(self, doc_id: str) -> None:
        """Remove a document's chunks and FTS rows so a retried ingestion starts from a clean slate."""
        try:
            with self.engine.begin() as conn:
                conn.execute(text("DELETE FROM chunks_fts WHERE doc_id = :doc_id"), {"doc_id": doc_id})
                conn.execute(text("DELETE FROM document_chunks WHERE doc_id = :doc_id"), {"doc_id": doc_id})
        except Exception as exc:
            raise StorageError(f"Failed to purge chunks for document '{doc_id}': {exc}") from exc

    def get_latest_document_by_filename(self, filename: str) -> Optional[DocumentRecord]:
        """Return the highest-version document registered under this filename."""
        query = text("""
            SELECT doc_id, filename, content_hash, file_type, page_count,
                   state, version, file_path, error_message, metadata_json, created_at
            FROM documents WHERE filename = :filename
            ORDER BY version DESC LIMIT 1
        """)
        try:
            with self.engine.connect() as conn:
                row = conn.execute(query, {"filename": filename}).mappings().fetchone()
                return self._row_to_document_record(row) if row else None
        except Exception as exc:
            raise StorageError(f"Failed to fetch document by filename '{filename}': {exc}") from exc

    def update_job_stage(self, job_id: str, stage: str) -> None:
        """Record the pipeline stage an in-progress job has reached."""
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text("UPDATE outbox_jobs SET stage = :stage, updated_at = CURRENT_TIMESTAMP WHERE job_id = :job_id"),
                    {"job_id": job_id, "stage": stage},
                )
        except Exception as exc:
            raise StorageError(f"Failed to update stage of job '{job_id}': {exc}") from exc

    def retry_or_fail_job(self, job_id: str, error: str, max_attempts: int) -> bool:
        """Return the job to 'pending' if attempts remain, else mark it permanently 'failed'."""
        try:
            with self.engine.begin() as conn:
                attempts = conn.execute(
                    text("SELECT attempts FROM outbox_jobs WHERE job_id = :job_id"), {"job_id": job_id}
                ).scalar()
                retry = attempts is not None and int(attempts) < max_attempts
                conn.execute(
                    text("""
                        UPDATE outbox_jobs
                        SET status = :status, last_error = :error, updated_at = CURRENT_TIMESTAMP
                        WHERE job_id = :job_id
                    """),
                    {"job_id": job_id, "status": "pending" if retry else "failed", "error": error},
                )
                return retry
        except Exception as exc:
            raise StorageError(f"Failed to record failure of job '{job_id}': {exc}") from exc

    def requeue_stale_jobs(self, older_than_seconds: Optional[int]) -> int:
        """Reclaim jobs stuck in 'in_progress' (e.g. after a crash)."""
        if older_than_seconds is None:
            sql = "UPDATE outbox_jobs SET status = 'pending', updated_at = CURRENT_TIMESTAMP WHERE status = 'in_progress'"
            params: dict[str, Any] = {}
        else:
            sql = (
                "UPDATE outbox_jobs SET status = 'pending', updated_at = CURRENT_TIMESTAMP "
                "WHERE status = 'in_progress' AND updated_at < datetime('now', :age)"
            )
            params = {"age": f"-{int(older_than_seconds)} seconds"}
        try:
            with self.engine.begin() as conn:
                return int(conn.execute(text(sql), params).rowcount or 0)
        except Exception as exc:
            raise StorageError(f"Failed to requeue stale outbox jobs: {exc}") from exc

    def list_documents_older_than(self, state: str, days: int) -> list[DocumentRecord]:
        """Documents in the given state whose last update is older than N days."""
        query = text("""
            SELECT doc_id, filename, content_hash, file_type, page_count,
                   state, version, file_path, error_message, metadata_json, created_at
            FROM documents
            WHERE state = :state AND updated_at < datetime('now', :age)
        """)
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(query, {"state": state, "age": f"-{int(days)} days"}).mappings().fetchall()
                return [self._row_to_document_record(row) for row in rows]
        except Exception as exc:
            raise StorageError(f"Failed to list aged '{state}' documents: {exc}") from exc

    def count_chunks(self) -> int:
        try:
            with self.engine.connect() as conn:
                return int(conn.execute(text("SELECT COUNT(*) FROM document_chunks")).scalar() or 0)
        except Exception as exc:
            raise StorageError(f"Failed to count chunks: {exc}") from exc

    def ping(self) -> None:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1 FROM documents LIMIT 1"))
        except Exception as exc:
            raise StorageError(f"Registry not reachable: {exc}") from exc


    REQUIREMENT_COLUMNS = (
        "req_id", "solicitation_id", "source_chunk_id", "category", "requirement_text",
        "status", "evidence_chunk_id", "rationale", "decision_backend",
    )

    def replace_requirements(self, solicitation_id: str, requirements: Sequence[dict[str, Any]]) -> None:
        cols = ", ".join(self.REQUIREMENT_COLUMNS)
        marks = ", ".join(f":{c}" for c in self.REQUIREMENT_COLUMNS)
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM compliance_requirements WHERE solicitation_id = :sid"), {"sid": solicitation_id}
                )
                for req in requirements:
                    conn.execute(
                        text(f"INSERT INTO compliance_requirements ({cols}) VALUES ({marks})"),
                        {c: req.get(c) for c in self.REQUIREMENT_COLUMNS},
                    )
        except Exception as exc:
            raise StorageError(f"Failed to persist requirements for '{solicitation_id}': {exc}") from exc

    def list_requirements(self, solicitation_id: str) -> list[dict[str, Any]]:
        cols = ", ".join(self.REQUIREMENT_COLUMNS)
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    text(f"SELECT {cols} FROM compliance_requirements WHERE solicitation_id = :sid ORDER BY rowid"),
                    {"sid": solicitation_id},
                ).mappings().fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:
            raise StorageError(f"Failed to list requirements for '{solicitation_id}': {exc}") from exc

    @staticmethod
    def _row_to_document_record(row: Any) -> DocumentRecord:
        raw_meta = row.get("metadata_json") or "{}"
        try:
            metadata = json.loads(raw_meta)
        except Exception:
            metadata = {}
        return DocumentRecord(
            doc_id=row["doc_id"],
            filename=row["filename"],
            content_hash=row["content_hash"],
            file_type=row["file_type"],
            page_count=row["page_count"],
            state=row["state"],
            version=row["version"],
            created_at=str(row["created_at"]),
            file_path=row.get("file_path", ""),
            error_message=row.get("error_message"),
            metadata=metadata,
        )

    @staticmethod
    def _row_to_document_chunk(row: Any) -> DocumentChunk:
        raw_meta = row.get("metadata_json") or "{}"
        try:
            metadata = json.loads(raw_meta)
        except Exception:
            metadata = {}
        return DocumentChunk(
            chunk_id=row["chunk_id"],
            doc_id=row["doc_id"],
            chunk_index=row["chunk_index"],
            page_number=row["page_number"],
            sheet_name=row.get("sheet_name"),
            row_start=row.get("row_start"),
            row_end=row.get("row_end"),
            line_start=row.get("line_start"),
            line_end=row.get("line_end"),
            char_count=row["char_count"],
            token_count=row["token_count"],
            text_content=row["text_content"],
            content_hash=row["content_hash"],
            metadata=metadata,
        )
