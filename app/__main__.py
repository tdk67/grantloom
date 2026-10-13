"""`python -m app` runs the server on the host/port from config/config.json."""

from __future__ import annotations

import uvicorn

from app.core.config import get_settings
from app.services.startup_checks import run_startup_checks

if __name__ == "__main__":
    settings = get_settings()
    run_startup_checks(settings)
    uvicorn.run("app.main:app", host=settings.server.host, port=settings.server.port)
