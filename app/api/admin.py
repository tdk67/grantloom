"""Administrative endpoints for synthetic data generation and model diagnostics."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict

from app.api.deps import (
    get_inference_profile,
    get_ingest_service,
    get_ingest_worker,
    get_registry,
    verify_master_api_key,
)
from app.core.config import Settings, get_settings
from app.core.rate_limit import admin_limit, limiter
from app.ports.registry import RegistryPort
from app.services.inference import InferenceProfile
from app.services.ingest_service import IngestionService
from app.synthetic.generator import SyntheticDataGenerator
from app.workers.ingest_worker import IngestWorker

logger = logging.getLogger("grantloom.api.admin")

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(verify_master_api_key)])


@router.post("/generate-synthetic-data")
@limiter.limit(admin_limit)
async def generate_synthetic_data(
    request: Request,
    settings: Settings = Depends(get_settings),
    registry: RegistryPort = Depends(get_registry),
    ingest_service: IngestionService = Depends(get_ingest_service),
    worker: IngestWorker = Depends(get_ingest_worker),
) -> dict:
    """Generate the demo grant documents and index them to active state."""
    generator = SyntheticDataGenerator(output_dir=Path(settings.database.synthetic_path))
    results = generator.generate_all(quiet=True)

    indexed_docs: list[dict] = []
    for file_info in results.get("files", []):
        resp = ingest_service.upload_document(
            filename=file_info["filename"], file_bytes=Path(file_info["path"]).read_bytes()
        )
        while await worker.process_next_job():
            pass
        doc = registry.get_document(resp.doc_id)
        indexed_docs.append({
            "doc_id": resp.doc_id,
            "filename": file_info["filename"],
            "state": doc.state if doc else "missing",
            "page_count": doc.page_count if doc else 0,
            "error_message": doc.error_message if doc else None,
        })

    logger.info("Admin generated and indexed %d synthetic documents.", len(indexed_docs))
    return {
        "status": "success",
        "generated_count": len(indexed_docs),
        "manifest_path": results.get("manifest_path", ""),
        "documents": indexed_docs,
    }


class CapabilityProbeRequest(BaseModel):
    """Only configured models of the active provider can be probed; no caller-supplied endpoints (SSRF)."""

    model_config = ConfigDict(extra="forbid")
    role: Literal["writer", "judge"] = "writer"


@router.post("/test-model-capabilities")
@limiter.limit(admin_limit)
async def test_model_capabilities(
    request: Request,
    req: CapabilityProbeRequest,
    x_openrouter_key: Optional[str] = Header(None, alias="X-OpenRouter-Key"),
    profile: InferenceProfile = Depends(get_inference_profile),
) -> dict:
    """Report provider-published facts (tool calling, context length) for the configured writer/judge model."""
    model = profile.writer_model if req.role == "writer" else profile.judge_model
    facts = await profile.client.describe_model(model, x_openrouter_key)
    if not facts["found"]:
        status, message = "failed", f"Model '{model}' is not available from {profile.client.provider}."
    elif facts["tool_calling"]:
        status, message = "ok", f"'{model}' supports tool calling."
    else:
        status, message = "warning", f"'{model}' does not advertise tool calling; the JSON-mode fallback is used."
    return {
        "provider": profile.client.provider,
        "destination": profile.client.destination,
        "role": req.role,
        "model": model,
        "tool_calling_supported": facts["tool_calling"],
        "context_length": facts["context_length"],
        "status": status,
        "message": message,
    }
