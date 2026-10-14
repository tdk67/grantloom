"""Compliance matrix API endpoints."""

from __future__ import annotations

import json
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse

from app.api.deps import get_compliance_service, get_inference_profile, verify_master_api_key
from app.core.errors import GrantLoomError
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


@router.post("/{solicitation_id}/stream")
@limiter.limit(compliance_limit)
async def evaluate_compliance_stream(
    request: Request,
    solicitation_id: str,
    x_openrouter_key: Optional[str] = Header(None, alias="X-OpenRouter-Key"),
    profile: InferenceProfile = Depends(get_inference_profile),
    service: ComplianceService = Depends(get_compliance_service),
) -> StreamingResponse:
    """SSE variant of the evaluation: `start` -> `progress` (per requirement) -> `done` (full matrix).

    Gives the UI a real progress bar instead of a multi-minute blind wait.
    """
    api_key = await profile.authorize(x_openrouter_key)

    async def sse_event_stream() -> AsyncIterator[str]:
        try:
            async for event_type, data in service.evaluate_solicitation_stream(solicitation_id, api_key):
                yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        except GrantLoomError as exc:
            yield f"event: error\ndata: {json.dumps(exc.to_problem_details())}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'title': 'Compliance Evaluation Error', 'detail': str(exc), 'status': 500})}\n\n"

    return StreamingResponse(
        sse_event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


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
