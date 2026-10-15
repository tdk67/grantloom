"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.api.deps import reset_dependencies
from tests.helpers import Stack, build_stack


@pytest.fixture
def stack(tmp_path: Path):
    reset_dependencies()
    s: Stack = build_stack(tmp_path)
    yield s
    s.close()
    reset_dependencies()


@pytest.fixture
async def registry_with_docs(tmp_path: Path):
    """A fresh stack with a deliberately anchor-rich solicitation ingested, so the example-question
    generator can ground its keyword / semantic / graph / structured templates (review-08 F1)."""
    reset_dependencies()
    s: Stack = build_stack(tmp_path)
    try:
        from tests.helpers import make_pdf
        pdf = make_pdf(tmp_path / "DOE_Solar_2026.pdf", [
            [
                # keyword anchors
                "DE-FOA-0003412: Clean Hydrogen Electrolysis & Underground Storage Infrastructure",
                "NSF 26-520: Quantum Sensors for Environmental & Critical Infrastructure Monitoring",
                # semantic anchors
                "During a major grid outage event in August 2025, the microgrid seamlessly transitioned to islanded operation",
                # graph anchors
                "Marcus Holloway, Lead Firmware Engineer, is affiliated with the Urban Microgrid Solar Storage project",
                "United States Department of Energy (DOE) funds the Urban Microgrid Solar Storage Integration project",
                # structured anchors
                "Lab_Budget_2025_Sample.xlsx Equipment total 151000 and Personnel annual base salary 140000",
            ],
            [
                # composite anchors
                "$1,250,000 award with a 24 month Performance Period within the Award Ceiling",
                "DOE Solar FOA cost share is exactly 20.0% while the EPA Community Resilience NOFO allows 0.0%",
            ],
        ])
        await s.ingest_bytes("DOE_Solar_2026.pdf", pdf.read_bytes())
        yield s.registry
    finally:
        s.close()
        reset_dependencies()
