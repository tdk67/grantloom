"""Document Ingestion Service with streaming SHA-256 deduplication and outbox queueing."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional

from app.core.config import Settings, get_settings
from app.core.errors import DocumentLimitExceededError, ExtractionError, ValidationError
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
        settings: Optional[Settings] = None,
        upload_dir: Optional[str | Path] = None,
    ) -> None:
        self.registry = registry
        self.settings = settings or get_settings()
        if upload_dir is None:
            self.upload_dir = Path("data/uploads")
        else:
            self.upload_dir = Path(upload_dir)
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

        # Check deduplication: does this content hash already exist?
        existing = self.registry.get_document_by_hash(content_hash)
        if existing:
            # Clean up temp file
            if temp_path.exists():
                temp_path.unlink()

            if existing.state == "active":
                return IngestionUploadResult(
                    doc_id=existing.doc_id,
                    filename=existing.filename,
                    content_hash=existing.content_hash,
                    file_type=existing.file_type,
                    state="active",
                    is_duplicate=True,
                    message="Document already indexed",
                    record=existing,
                )
            elif existing.state in ("uploaded", "extracting", "chunking", "embedding"):
                return IngestionUploadResult(
                    doc_id=existing.doc_id,
                    filename=existing.filename,
                    content_hash=existing.content_hash,
                    file_type=existing.file_type,
                    state=existing.state,
                    is_duplicate=True,
                    message="Document is currently being processed",
                    record=existing,
                )

        # Check total document capacity limit
        current_doc_count = self.registry.count_documents()
        if current_doc_count >= self.settings.limits.max_documents:
            if temp_path.exists():
                temp_path.unlink()
            raise DocumentLimitExceededError(
                f"Maximum document capacity ({self.settings.limits.max_documents}) reached. Cannot index more documents."
            )

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
            version=1,
            created_at="",
            file_path=str(final_path),
            metadata={
                "file_size_bytes": bytes_written,
                "original_filename": filename,
            },
        )

        self.registry.save_document(record)
        self.registry.create_outbox_job(doc_id=doc_id, stage="extracting")
        self.registry.log_audit_event(
            event_type="document_uploaded",
            doc_id=doc_id,
            details={"filename": filename, "content_hash": content_hash, "file_size": bytes_written},
        )

        logger.info("Enqueued document '%s' (ID: %s, Hash: %s) for ingestion.", filename, doc_id, content_hash)

        return IngestionUploadResult(
            doc_id=doc_id,
            filename=filename,
            content_hash=content_hash,
            file_type=ext,
            state="uploaded",
            is_duplicate=False,
            message="Document accepted and queued for processing",
            record=record,
        )
