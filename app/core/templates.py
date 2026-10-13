"""Template loader for external prompts, refusals, and UI fixtures."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


def get_templates_root() -> Path:
    """Resolve templates root directory."""
    # Priority: current working directory / templates, or relative to package
    cwd_path = Path.cwd() / "templates"
    if cwd_path.exists():
        return cwd_path
    return Path(__file__).resolve().parent.parent.parent / "templates"


@lru_cache(maxsize=32)
def load_template(relative_path: str) -> str:
    """Load text template from templates directory with caching."""
    full_path = get_templates_root() / relative_path
    if not full_path.exists():
        raise FileNotFoundError(f"Required template file not found at: {full_path}")
    return full_path.read_text(encoding="utf-8").strip()
