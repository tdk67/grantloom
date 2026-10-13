"""FastAPI application factory and entrypoint for GrantLoom."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import make_asgi_app
from slowapi.errors import RateLimitExceeded

from app.api.admin import router as admin_router
from app.api.ask import router as ask_router
from app.api.compliance import router as compliance_router
from app.api.deps import get_graph_store, get_ingest_worker, get_registry, get_vector_store
from app.api.documents import router as documents_router
from app.api.auth import router as auth_router
from app.api.examples import router as examples_router
from app.api.info import router as info_router
from app.core.config import Settings, get_settings
from app.core.errors import ErrorCode, GrantLoomError, RateLimitExceededError, ServiceUnavailableError
from app.core.metrics import HTTP_REQUEST_DURATION
from app.core.rate_limit import limiter
from app.db import migrator
from app.ports.graph_store import GraphStorePort
from app.ports.registry import RegistryPort
from app.ports.vector_store import VectorStorePort
from app.services.startup_checks import run_startup_checks

logger = logging.getLogger("grantloom")
logging.basicConfig(level=logging.INFO)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup checks, database migrations, and the ingestion worker loop."""
    logger.info("Initializing GrantLoom application...")
    settings = get_settings()
    run_startup_checks(settings)

    logger.info("Running database migrations up to head...")
    migrator.upgrade("head")

    worker = get_ingest_worker(settings)
    worker_task = asyncio.create_task(worker.run_loop())
    logger.info("Ingestion background worker task started.")

    yield

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
    app.state.limiter = limiter

    @app.middleware("http")
    async def record_latency(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        HTTP_REQUEST_DURATION.labels(
            method=request.method,
            route=getattr(route, "path", "unmatched"),
            status=str(response.status_code),
        ).observe(time.perf_counter() - started)
        return response

    app.mount("/metrics", make_asgi_app())

    @app.get("/metrics", include_in_schema=False)
    async def metrics_root() -> RedirectResponse:
        # Starlette mounts only match '/metrics/...'; serve the bare path too (Prometheus default).
        return RedirectResponse(url="/metrics/")

    app.include_router(documents_router)
    app.include_router(ask_router)
    app.include_router(compliance_router)
    app.include_router(examples_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(info_router)

    @app.get("/healthz", tags=["health"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    async def readiness_check(
        settings: Settings = Depends(get_settings),
        registry: RegistryPort = Depends(get_registry),
        vector_store: VectorStorePort = Depends(get_vector_store),
        graph_store: GraphStorePort = Depends(get_graph_store),
    ) -> dict[str, object]:
        """FR-O2: confirms the registry, vector index and graph store answer."""
        checks: dict[str, str] = {}
        for name, probe in (("registry", registry.ping), ("vector_store", vector_store.ping), ("graph_store", graph_store.ping)):
            try:
                probe()
                checks[name] = "ok"
            except GrantLoomError as exc:
                checks[name] = exc.detail
        if any(v != "ok" for v in checks.values()):
            raise ServiceUnavailableError("One or more stores are not ready.", extra={"checks": checks})
        return {"status": "ready", "checks": checks}

    @app.get("/robots.txt", include_in_schema=False)
    async def get_robots_txt() -> FileResponse:
        return FileResponse(STATIC_DIR / "robots.txt", media_type="text/plain")

    @app.get("/sitemap.xml", include_in_schema=False)
    async def get_sitemap_xml() -> FileResponse:
        return FileResponse(STATIC_DIR / "sitemap.xml", media_type="application/xml")

    @app.get("/llms.txt", include_in_schema=False)
    async def get_llms_txt() -> FileResponse:
        return FileResponse(STATIC_DIR / "llms.txt", media_type="text/plain")

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    @app.exception_handler(GrantLoomError)
    async def grantloom_error_handler(request: Request, exc: GrantLoomError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.to_problem_details(), media_type="application/problem+json")

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        problem = RateLimitExceededError(f"Rate limit exceeded: {exc.detail}")
        return JSONResponse(status_code=429, content=problem.to_problem_details(), media_type="application/problem+json")

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
        return JSONResponse(status_code=422, content=problem, media_type="application/problem+json")

    return app


app = create_app()
