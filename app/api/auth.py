"""BYOK key validation endpoint (D28): the UI validates the visitor's key once, on save.

The server probes OpenRouter live here and remembers the key digest for the process lifetime,
so per-request inference no longer pays a validation round-trip. A key that is revoked later
still fails immediately: every real provider call maps upstream 401/402 to RFC 7807 errors.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Request

from app.api.deps import get_inference_profile, verify_master_api_key
from app.core.rate_limit import admin_limit, limiter
from app.services.inference import InferenceProfile

router = APIRouter(prefix="/api/v1/auth", tags=["auth"], dependencies=[Depends(verify_master_api_key)])


@router.post("/validate-key")
@limiter.limit(admin_limit)
async def validate_byok_key(
    request: Request,
    x_openrouter_key: Optional[str] = Header(None, alias="X-OpenRouter-Key"),
    profile: InferenceProfile = Depends(get_inference_profile),
) -> dict[str, Any]:
    """Validate the caller's OpenRouter key once (live probe) and cache it for this process.

    401 (RFC 7807) when the key is missing or rejected by the provider. In private mode no key
    is used at all and the response says so.
    """
    if not profile.requires_byok_key:
        return {
            "valid": True,
            "mode": profile.mode,
            "provider": profile.client.provider,
            "note": "Private mode: inference runs on the local Ollama daemon; no API key is used.",
        }
    await profile.authorize(x_openrouter_key)  # raises AuthenticationError -> 401 problem+json
    return {
        "valid": True,
        "mode": profile.mode,
        "provider": profile.client.provider,
        "destination": profile.client.destination,
        "writer_model": profile.writer_model,
        "judge_model": profile.judge_model,
        "note": "Key validated against the provider and remembered for this server process.",
    }
