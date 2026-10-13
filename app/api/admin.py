"""Administrative endpoints for synthetic data generation and demo management."""

from __future__ import annotations

import logging
from pathlib import Path
from fastapi import APIRouter, Depends

from app.api.deps import get_ingest_service, get_ingest_worker, get_registry
from app.core.config import Settings, get_settings
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
async def test_model_capabilities(
    req: dict,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Diagnostic probe testing Ollama/OpenRouter structured JSON/tool-calling support."""
    provider = req.get("provider", "ollama")
    model = req.get("model") or settings.llm.ollama_writer_model
    endpoint = req.get("endpoint") or settings.llm.ollama_base_url

    if provider == "ollama":
        from app.adapters.ollama_client import OllamaClientAdapter
        client = OllamaClientAdapter(base_url=endpoint, default_model=model)
        probe_res = await client.test_capabilities(model=model)
        if probe_res.get("reachable") and probe_res.get("json_support"):
            return {
                "provider": provider,
                "model": model,
                "tool_calling_supported": True,
                "context_budget": 8192,
                "status": "ok",
                "message": f"Live probe passed for model '{model}' via Ollama.",
                "probe": probe_res,
            }
        elif probe_res.get("reachable"):
            return {
                "provider": provider,
                "model": model,
                "tool_calling_supported": False,
                "context_budget": 4096,
                "status": "warning",
                "message": f"Model '{model}' responded but failed structured JSON test. Fallback mode recommended.",
                "probe": probe_res,
            }
        else:
            return {
                "provider": provider,
                "model": model,
                "tool_calling_supported": False,
                "context_budget": 4096,
                "status": "failed",
                "message": f"Could not reach Ollama at {endpoint}: {probe_res.get('error')}",
                "probe": probe_res,
            }

    # OpenRouter or other provider probe
    is_tool_capable = any(k in model.lower() for k in ["gpt-4", "claude-3", "qwen", "llama-3", "mistral"])
    return {
        "provider": provider,
        "model": model,
        "tool_calling_supported": is_tool_capable,
        "context_budget": 8192 if is_tool_capable else 4096,
        "status": "ok" if is_tool_capable else "warning",
        "message": "Tool calling supported" if is_tool_capable else "Extractive fallback mode recommended",
    }

