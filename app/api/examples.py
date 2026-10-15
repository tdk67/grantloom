"""Example questions endpoint serving corpus-derived starter prompts.

The starter questions are generated from the actual indexed documents (review-08 F1), so a
freshly-seeded deployment gets questions it can actually answer. When the corpus is empty the
endpoint serves a small static onboarding set so the UI is never blank; the moment documents are
indexed the corpus-derived questions replace it. A failing registry is still a typed error.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_registry, verify_master_api_key
from app.core.errors import ErrorCode, GrantLoomError
from app.ports.registry import RegistryPort
from app.services.example_questions import MAX_EMITTED, build_example_questions

router = APIRouter(prefix="/api/v1/examples", tags=["examples"], dependencies=[Depends(verify_master_api_key)])

_cache: dict[str, list[dict[str, Any]]] = {}

# Static onboarding questions shown only while the corpus is empty. They are deliberately generic
# and replaced the moment any active document exists.
ONBOARDING_EXAMPLES: list[dict[str, Any]] = [
    {
        "id": "ex_onboarding_1",
        "category": "Getting started",
        "question": "What does this grant FOA require for cost sharing?",
        "strategy_hint": "hybrid",
        "description": "Upload a solicitation to make this question answerable.",
        "source_chunks": [],
    },
    {
        "id": "ex_onboarding_2",
        "category": "Getting started",
        "question": "What is the total equipment budget in the lab spreadsheet?",
        "strategy_hint": "structured",
        "description": "Upload a budget spreadsheet to make this question answerable.",
        "source_chunks": [],
    },
    {
        "id": "ex_onboarding_3",
        "category": "Getting started",
        "question": "Who leads the microgrid project and which agency funds it?",
        "strategy_hint": "graph",
        "description": "Upload a proposal and budget to make this question answerable.",
        "source_chunks": [],
    },
]


@router.get("")
async def get_example_questions(
    registry: RegistryPort = Depends(get_registry),
) -> list[dict[str, Any]]:
    """Serve corpus-derived starter prompts; on an empty corpus serve onboarding prompts."""
    try:
        docs = registry.list_documents(state="active")
    except Exception as exc:  # SQLite registry errors surface as StorageError; keep it loud
        raise GrantLoomError(
            f"Example questions are not available: the document registry is unreachable ({exc})",
            code=ErrorCode.DATABASE_ERROR,
            status=500,
            title="Registry Unavailable",
        ) from exc
    if not docs:
        return ONBOARDING_EXAMPLES

    # Deterministic cache: keyed on active doc ids + count so a re-seed refreshes the sidebar.
    key = ":".join(sorted(d.doc_id for d in docs))
    if key not in _cache:
        _cache[key] = build_example_questions(registry, limit=MAX_EMITTED)
    return _cache[key]
