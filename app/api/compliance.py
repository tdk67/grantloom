"""Compliance matrix API endpoints."""

from __future__ import annotations

import io
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from app.api.deps import get_registry, verify_master_api_key
from app.ports.registry import RegistryPort
from app.services.compliance_service import ComplianceService

router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"])


@router.post("/{solicitation_id}", dependencies=[Depends(verify_master_api_key)])
async def evaluate_compliance(
    solicitation_id: str,
    registry: RegistryPort = Depends(get_registry),
) -> dict:
    """Extract mandatory solicitation requirements, cross-reference lab assets, and return compliance status."""
    service = ComplianceService(registry)
    return service.evaluate_solicitation(solicitation_id)


@router.get("/{solicitation_id}/export")
async def export_compliance_matrix(
    solicitation_id: str,
    registry: RegistryPort = Depends(get_registry),
) -> Response:
    """Generate and download formatted Excel workbook for compliance matrix."""
    service = ComplianceService(registry)
    matrix_report = service.evaluate_solicitation(solicitation_id)
    xlsx_bytes = service.export_excel(matrix_report)

    filename = f"compliance_matrix_{solicitation_id}.xlsx"
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
