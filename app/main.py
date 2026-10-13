"""FastAPI application factory and entrypoint for GrantLoom."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

from app.api.deps import get_ingest_worker
from app.api.documents import router as documents_router
from app.core.config import get_settings
from app.core.errors import ErrorCode, GrantLoomError
from app.db import migrator

logger = logging.getLogger("grantloom")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: run database migrations and manage background worker loop."""
    logger.info("Initializing GrantLoom application...")
    settings = get_settings()

    # 1. Automatic Database Migrations (Liquibase equivalent)
    try:
        logger.info("Running database migrations up to head...")
        migrator.upgrade("head")
    except Exception as exc:
        logger.error("Database migration failed on startup: %s", exc)
        raise

    # 2. Start Ingestion Outbox Worker Loop
    worker = get_ingest_worker(settings)
    worker_task = asyncio.create_task(worker.run_loop())
    logger.info("Ingestion background worker task started.")

    yield

    # Cleanup on shutdown
    logger.info("Shutting down GrantLoom...")
    worker.stop()
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    logger.info("GrantLoom shutdown complete.")


def create_app() -> FastAPI:
    """Create and configure FastAPI instance."""
    app = FastAPI(
        title="GrantLoom Intelligence System",
        description="AI Agent-Based Grant Intelligence & Decision Support System",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Mount Prometheus metrics endpoint (/metrics)
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    # Register Routers
    app.include_router(documents_router)

    # Health check
    @app.get("/healthz", tags=["health"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    # RFC 7807 Error Handlers
    @app.exception_handler(GrantLoomError)
    async def grantloom_error_handler(request: Request, exc: GrantLoomError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=exc.to_problem_details(),
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        problem = {
            "type": "urn:grantloom:error:validation-error",
            "title": "Validation Error",
            "status": 422,
            "detail": str(exc),
            "code": ErrorCode.VALIDATION_ERROR.value,
            "extra": {"errors": exc.errors()},
        }
        return JSONResponse(
            status_code=422,
            content=problem,
            media_type="application/problem+json",
        )

    return app


app = create_app()
