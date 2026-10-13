"""Deployment mode / data-egress facts for the UI (which provider runs, whether a BYOK key is needed)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_decision_backend, get_inference_profile, verify_master_api_key
from app.ports.decision_backend import DecisionBackendPort
from app.services.inference import InferenceProfile

router = APIRouter(prefix="/api/v1/info", tags=["info"], dependencies=[Depends(verify_master_api_key)])


@router.get("")
async def deployment_info(
    profile: InferenceProfile = Depends(get_inference_profile),
    decision: DecisionBackendPort = Depends(get_decision_backend),
) -> dict[str, Any]:
    return {**profile.egress(decision), "requires_byok_key": profile.requires_byok_key}
