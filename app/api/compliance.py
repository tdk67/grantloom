"""Compliance matrix API endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, Request, Response

from app.api.deps import get_compliance_service, get_inference_profile, verify_master_api_key
from app.core.rate_limit import compliance_limit, limiter
from app.services.compliance_service import ComplianceService
from app.services.inference import InferenceProfile

router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"], dependencies=[Depends(verify_master_api_key)])


@router.post("/{solicitation_id}")
@limiter.limit(compliance_limit)
async def evaluate_compliance(
    request: Request,
    solicitation_id: str,
    x_openrouter_key: Optional[str] = Header(None, alias="X-OpenRouter-Key"),
    profile: InferenceProfile = Depends(get_inference_profile),
    service: ComplianceService = Depends(get_compliance_service),
) -> dict:
    """Extract solicitation requirements, assess lab evidence with the decision backend, persist and return the matrix."""
    api_key = await profile.authorize(x_openrouter_key)
    return await service.evaluate_solicitation(solicitation_id, api_key)


@router.get("/{solicitation_id}")
async def get_compliance_matrix(
    solicitation_id: str,
    service: ComplianceService = Depends(get_compliance_service),
) -> dict:
    """Return the last persisted compliance evaluation."""
    return service.build_report(solicitation_id)


@router.get("/{solicitation_id}/export")
async def export_compliance_matrix(
    solicitation_id: str,
    service: ComplianceService = Depends(get_compliance_service),
) -> Response:
    """Download the persisted compliance matrix as a formatted Excel workbook."""
    xlsx_bytes = service.export_excel(service.build_report(solicitation_id))
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="compliance_matrix_{solicitation_id}.xlsx"'},
    )
