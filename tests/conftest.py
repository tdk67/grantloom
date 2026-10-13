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
