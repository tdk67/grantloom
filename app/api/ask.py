"""API route for streaming question answering and citation verification."""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Body, Depends, Header, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_ask_service, get_inference_profile, verify_master_api_key
from app.core.rate_limit import ask_limit, limiter
from app.services.ask_service import AskService
from app.services.inference import InferenceProfile

logger = logging.getLogger("grantloom.api.ask")

router = APIRouter(prefix="/api/v1", tags=["query"], dependencies=[Depends(verify_master_api_key)])


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, description="User question (length limit enforced by G1 from config)")


@router.post("/ask", status_code=status.HTTP_200_OK)
@limiter.limit(ask_limit)
async def ask_endpoint(
    request: Request,
    req: AskRequest = Body(..., embed=False),
    x_openrouter_key: Optional[str] = Header(None, alias="X-OpenRouter-Key"),
    profile: InferenceProfile = Depends(get_inference_profile),
    ask_service: AskService = Depends(get_ask_service),
) -> StreamingResponse:
    """Stream answer generation with real-time SSE event pipeline."""
    # FR-Q1: in BYOK mode the key is mandatory and probed live against OpenRouter before any work.
    api_key = await profile.authorize(x_openrouter_key)

    async def sse_event_stream() -> AsyncIterator[str]:
        async for event_type, data in ask_service.stream_ask(query=req.query, api_key=api_key):
            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(
        sse_event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
