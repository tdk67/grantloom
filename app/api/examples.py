"""Example questions endpoint serving curated starter prompts."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import verify_master_api_key
from app.core.config import PROJECT_ROOT

router = APIRouter(prefix="/api/v1/examples", tags=["examples"], dependencies=[Depends(verify_master_api_key)])

EXAMPLES_PATH = PROJECT_ROOT / "config" / "example_questions.json"


@router.get("")
async def get_example_questions() -> list[dict[str, Any]]:
    """Serve curated starter prompts (a missing or malformed file is a deployment error, not an empty list)."""
    return json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))
