"""Template loader for external prompts, refusals, and decision lexicons.

Templates resolve against the project root (never the process CWD) so the app behaves the same
regardless of where it is launched from.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT

TEMPLATES_ROOT = PROJECT_ROOT / "templates"


def get_templates_root() -> Path:
    """Resolve templates root directory."""
    return TEMPLATES_ROOT


@lru_cache(maxsize=64)
def load_template(relative_path: str) -> str:
    """Load text template from templates directory with caching."""
    full_path = TEMPLATES_ROOT / relative_path
    if not full_path.exists():
        raise FileNotFoundError(f"Required template file not found at: {full_path}")
    return full_path.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=16)
def load_json_template(relative_path: str) -> Any:
    """Load and parse a JSON data template (e.g. the extraction lexicon)."""
    return json.loads(load_template(relative_path))


def render_template(relative_path: str, **values: str) -> str:
    """Fill <<NAME>> placeholders. Plain replacement keeps literal JSON braces in prompts intact."""
    text = load_template(relative_path)
    for key, value in values.items():
        text = text.replace(f"<<{key.upper()}>>", value)
    return text
