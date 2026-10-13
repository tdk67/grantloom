# GrantLoom Phase 2 (M8): multi-stage production image.
# Stage 1 builds the wheel + dependencies; stage 2 runs as a non-root user.
# The container listens on 0.0.0.0:8000 (via GRANTLOOM_CONFIG_PATH); the compose
# file publishes it on 127.0.0.1 only — nginx terminates TLS in front of it.

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Project metadata first for better layer caching
COPY pyproject.toml uv.lock README.md ./
COPY app ./app

RUN pip install --prefix=/install .

# ---------------------------------------------------------------------------

FROM python:3.12-slim AS runtime

RUN groupadd --system grantloom && useradd --system --gid grantloom --home /app grantloom

WORKDIR /app

COPY --from=builder /install /usr/local
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY templates ./templates
COPY config/config.json ./config/config.json

# Persistent state (SQLite, Chroma, Kùzu, uploads) lives in the /app/data volume
RUN mkdir -p /app/data && chown -R grantloom:grantloom /app /app/data

USER grantloom

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4); sys.exit(0 if r.status==200 else 1)"

CMD ["python", "-m", "app"]
