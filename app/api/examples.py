"""Example questions endpoint serving curated starter prompts."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import verify_master_api_key
from app.core.config import PROJECT_ROOT
from app.core.errors import ErrorCode, GrantLoomError

router = APIRouter(prefix="/api/v1/examples", tags=["examples"], dependencies=[Depends(verify_master_api_key)])

EXAMPLES_PATH = PROJECT_ROOT / "config" / "example_questions.json"


@router.get("")
async def get_example_questions() -> list[dict[str, Any]]:
    """Serve curated starter prompts. A missing or malformed file is a deployment error, raised as a
    typed GrantLoomError so the problem-details handler renders it (review-04 C7) — never a raw
    framework 500 and never a silent empty list."""
    try:
        return json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GrantLoomError(
            f"Example questions are not available: {EXAMPLES_PATH.name} is missing or malformed.",
            code=ErrorCode.CONFIG_ERROR,
            status=500,
            title="Deployment Configuration Error",
        ) from exc
