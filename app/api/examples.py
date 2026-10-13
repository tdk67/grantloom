"""Example questions endpoint serving curated starter prompts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/examples", tags=["examples"])


@router.get("")
async def get_example_questions() -> list[dict[str, Any]]:
    """Serve curated starter prompts for grant research workflows."""
    config_file = Path("config/example_questions.json")
    if config_file.exists():
        try:
            return json.loads(config_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []
