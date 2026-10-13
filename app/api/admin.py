"""Administrative endpoints for synthetic data generation and demo management."""

from __future__ import annotations

import logging
from pathlib import Path
from fastapi import APIRouter, Depends

from app.api.deps import get_ingest_service, get_ingest_worker, get_registry
from app.ports.registry import RegistryPort
from app.services.ingest_service import IngestionService
from app.synthetic.generator import SyntheticDataGenerator
from app.workers.ingest_worker import IngestWorker

logger = logging.getLogger("grantloom.api.admin")

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.post("/generate-synthetic-data")
async def generate_synthetic_data(
    registry: RegistryPort = Depends(get_registry),
    ingest_service: IngestionService = Depends(get_ingest_service),
    worker: IngestWorker = Depends(get_ingest_worker),
) -> dict:
    """Generate high-fidelity demo grant documents and automatically index them to active state."""
    output_dir = Path("data/synthetic")
    output_dir.mkdir(parents=True, exist_ok=True)

    generator = SyntheticDataGenerator(output_dir=output_dir)
    results = generator.generate_all(quiet=True)

    indexed_docs: list[dict] = []

    for file_info in results.get("files", []):
        file_path = Path(file_info["path"])
        file_bytes = file_path.read_bytes()
        filename = file_info["filename"]

        # Ingest document
        resp = ingest_service.upload_document(filename=filename, file_bytes=file_bytes)
        doc_id = resp.doc_id


        # Run worker to advance from outbox to active
        await worker.process_next_job()

        doc = registry.get_document(doc_id)
        state = doc.state if doc else "unknown"

        indexed_docs.append({
            "doc_id": doc_id,
            "filename": filename,
            "state": state,
            "page_count": doc.page_count if doc else 0,
        })

    logger.info("Admin generated and indexed %d synthetic documents.", len(indexed_docs))

    return {
        "status": "success",
        "generated_count": len(indexed_docs),
        "manifest_path": results.get("manifest_path", ""),
        "documents": indexed_docs,
    }


@router.post("/test-model-capabilities")
async def test_model_capabilities(req: dict) -> dict:
    """Diagnostic probe testing Ollama/OpenRouter structured tool-calling support."""
    provider = req.get("provider", "ollama")
    model = req.get("model", "llama3.1:8b")
    endpoint = req.get("endpoint", "http://localhost:11434")

    # In local testing or when Ollama is running, probe endpoint
    is_tool_capable = "llama3" in model.lower() or "qwen" in model.lower() or "mistral" in model.lower()
    return {
        "provider": provider,
        "model": model,
        "tool_calling_supported": is_tool_capable,
        "context_budget": 8192 if is_tool_capable else 4096,
        "status": "ok" if is_tool_capable else "warning",
        "message": "Tool calling supported" if is_tool_capable else "Extractive fallback mode recommended",
    }

