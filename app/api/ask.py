"""API route for streaming question answering and citation verification."""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Body, Depends, Header, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_ask_service, get_llm_client
from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationError, BadRequestError
from app.ports.llm_client import LLMClientPort
from app.services.ask_service import AskService

logger = logging.getLogger("grantloom.api.ask")

router = APIRouter(prefix="/api/v1", tags=["query"])


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="User question to answer")


@router.post("/ask", status_code=status.HTTP_200_OK)
async def ask_endpoint(
    req: AskRequest = Body(..., embed=False),
    x_openrouter_key: Optional[str] = Header(None, alias="X-OpenRouter-Key"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
    llm_client: LLMClientPort = Depends(get_llm_client),
    ask_service: AskService = Depends(get_ask_service),
) -> StreamingResponse:
    """Stream answer generation with real-time SSE event pipeline."""
    # 1. Master API key verification (if configured in settings)
    master_key = settings.secrets.master_api_key
    if master_key and x_api_key != master_key:
        raise AuthenticationError("Invalid master X-API-Key.")

    # 2. BYOK OpenRouter Key Validation (FR-Q1)
    # If not private mode, validate X-OpenRouter-Key live against auth probe
    if not settings.llm.private_mode:
        if not x_openrouter_key:
            raise AuthenticationError("Header 'X-OpenRouter-Key' is required for query inference.")
        # Perform live auth probe against upstream provider
        await llm_client.validate_api_key(x_openrouter_key)

    async def sse_event_stream() -> AsyncIterator[str]:
        try:
            async for event_type, data in ask_service.stream_ask(
                query=req.query,
                api_key=x_openrouter_key,
            ):
                yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        except Exception as exc:
            logger.error("SSE stream encountered unhandled error: %s", exc)
            err_payload = json.dumps({"error": str(exc), "status": 500})
            yield f"event: error\ndata: {err_payload}\n\n"

    return StreamingResponse(
        sse_event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
