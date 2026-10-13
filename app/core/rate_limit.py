"""Per-client rate limiting (slowapi). Limits come from config `rate_limits` (NFR-3)."""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

limiter = Limiter(key_func=get_remote_address)


def ask_limit() -> str:
    return get_settings().rate_limits.ask


def upload_limit() -> str:
    return get_settings().rate_limits.upload


def compliance_limit() -> str:
    return get_settings().rate_limits.compliance


def admin_limit() -> str:
    return get_settings().rate_limits.admin
