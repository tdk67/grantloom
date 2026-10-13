"""Document Ingestion Service with streaming SHA-256 deduplication and outbox queueing."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional

from app.core.config import Settings
from app.core.errors import ConflictError, DocumentLimitExceededError, DocumentNotFoundError, ValidationError
from app.ports.models import DocumentRecord
from app.ports.registry import RegistryPort

logger = logging.getLogger("grantloom.services.ingest")

ALLOWED_EXTENSIONS = {"pdf", "txt", "xlsx", "xls", "csv"}


@dataclass
class IngestionUploadResult:
    doc_id: str
    filename: str
    content_hash: str
    file_type: str
    state: str
    is_duplicate: bool
    message: str
    record: Optional[DocumentRecord] = None


class IngestionService:
    """Handles upload validation, SHA-256 content deduplication, and outbox job creation."""

    def __init__(
        self,
        registry: RegistryPort,
        settings: Settings,
        upload_dir: Optional[str | Path] = None,
    ) -> None:
        self.registry = registry
        self.settings = settings
        self.upload_dir = Path(upload_dir) if upload_dir is not None else Path(settings.database.uploads_path)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def upload_document(self, filename: str, file_bytes: bytes) -> IngestionUploadResult:
        """Convenience method accepting raw bytes."""
        import io
        return self.process_upload(filename=filename, stream=io.BytesIO(file_bytes), content_length=len(file_bytes))

    def process_upload(

        self,
        filename: str,
        stream: BinaryIO,
        content_length: Optional[int] = None,
    ) -> IngestionUploadResult:
        """Stream uploaded file, compute SHA-256, enforce capacity limits, and deduplicate."""
        # Validate and sanitize filename and extension
        filename = Path(filename).name
        if not filename or "." not in filename:
            raise ValidationError(f"Invalid filename: '{filename}'. File must have an extension.")

        ext = filename.rsplit(".", 1)[-1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValidationError(
                f"Unsupported file extension '.{ext}'. Supported extensions are: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
            )

        # Enforce file size limit if provided in headers
        max_bytes = self.settings.limits.max_file_size_mb * 1024 * 1024
        if content_length and content_length > max_bytes:
            raise ValidationError(
                f"File size exceeds maximum allowed limit of {self.settings.limits.max_file_size_mb} MB."
            )

        # Stream file to disk while calculating SHA-256
        temp_id = str(uuid.uuid4())
        temp_path = self.upload_dir / f"temp_{temp_id}.{ext}"

        hasher = hashlib.sha256()
        bytes_written = 0

        try:
            with open(temp_path, "wb") as f_out:
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    if bytes_written > max_bytes:
                        raise ValidationError(
                            f"File size exceeds maximum allowed limit of {self.settings.limits.max_file_size_mb} MB."
                        )
                    hasher.update(chunk)
                    f_out.write(chunk)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

        content_hash = hasher.hexdigest()

        # Check deduplication: does this content hash already exist? (FR-I2)
        existing = self.registry.get_document_by_hash(content_hash)
        if existing:
            temp_path.unlink(missing_ok=True)
            return self._handle_duplicate(existing)
        # Check total document capacity limit
        current_doc_count = self.registry.count_documents()
        if current_doc_count >= self.settings.limits.max_documents:
            if temp_path.exists():
                temp_path.unlink()
            raise DocumentLimitExceededError(
                f"Maximum document capacity ({self.settings.limits.max_documents}) reached. Cannot index more documents."
            )

        # Same filename with new content -> next version of that document (FR-I2)
        previous = self.registry.get_latest_document_by_filename(filename)
        version = previous.version + 1 if previous else 1

        # Create new document record
        doc_id = f"doc_{uuid.uuid4().hex[:12]}"
        final_path = self.upload_dir / f"{doc_id}.{ext}"
        temp_path.rename(final_path)

        record = DocumentRecord(
            doc_id=doc_id,
            filename=filename,
            content_hash=content_hash,
            file_type=ext,
            page_count=0,
            state="uploaded",
            version=version,
            created_at="",
            file_path=str(final_path),
            metadata={
                "file_size_bytes": bytes_written,
                "original_filename": filename,
                "stage": "uploaded",
                "progress_pct": 0,
                **({"previous_version_doc_id": previous.doc_id} if previous else {}),
            },
        )

        self.registry.save_document(record)
        self.registry.create_outbox_job(doc_id=doc_id, stage="extracting")
        self.registry.log_audit_event(
            event_type="document_uploaded",
            doc_id=doc_id,
            details={"filename": filename, "content_hash": content_hash, "file_size": bytes_written, "version": version},
        )

        logger.info("Enqueued document '%s' (ID: %s, Hash: %s) for ingestion.", filename, doc_id, content_hash)

        return IngestionUploadResult(
            doc_id=doc_id,
            filename=filename,
            content_hash=content_hash,
            file_type=ext,
            state="uploaded",
            is_duplicate=False,
            message="Document accepted and queued for processing" + (f" as version {version}" if version > 1 else ""),
            record=record,
        )

    IN_FLIGHT_STATES = ("uploaded", "extracting", "chunking", "scanning", "embedding", "indexing")

    def _handle_duplicate(self, existing: DocumentRecord) -> IngestionUploadResult:
        """Identical bytes were uploaded before: never insert a second row for the same hash."""
        def result(state: str, message: str) -> IngestionUploadResult:
            return IngestionUploadResult(
                doc_id=existing.doc_id,
                filename=existing.filename,
                content_hash=existing.content_hash,
                file_type=existing.file_type,
                state=state,
                is_duplicate=True,
                message=message,
                record=existing,
            )

        if existing.state == "active":
            return result("active", "Document already indexed")
        if existing.state in self.IN_FLIGHT_STATES:
            return result(existing.state, "Document is currently being processed")
        if existing.state == "failed":
            self.registry.update_document_state(
                existing.doc_id, state="uploaded", error_message="", metadata_update={"stage": "uploaded", "progress_pct": 0},
            )
            self.registry.create_outbox_job(doc_id=existing.doc_id, stage="extracting")
            self.registry.log_audit_event("document_requeued", existing.doc_id, {"reason": "re-upload of failed document"})
            return result("uploaded", "Previously failed document re-queued for processing")
        if existing.state == "quarantined":
            raise ConflictError(
                f"Identical content was quarantined as document '{existing.doc_id}'. Review it and release or delete it first."
            )
        raise ConflictError(f"Identical content belongs to document '{existing.doc_id}', which is being deleted.")

    def request_deletion(self, doc_id: str) -> DocumentRecord:
        """FR-I6: mark the document 'deleting' (immediately excluded from retrieval and citations) and
        enqueue the purge saga for the worker. Idempotent for documents already being deleted."""
        doc = self.registry.get_document(doc_id)
        if doc is None:
            raise DocumentNotFoundError(f"Document with ID '{doc_id}' not found.")
        if doc.state != "deleting":
            self.registry.update_document_state(doc_id, state="deleting", metadata_update={"stage": "deleting"})
            self.registry.create_outbox_job(doc_id=doc_id, stage="deleting")
            self.registry.log_audit_event("document_deletion_requested", doc_id, {"filename": doc.filename})
        return doc
