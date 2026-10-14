"""Prometheus instrumentation (FR-O2). Metrics are module-level so repeated create_app() calls
(tests) never register a collector twice."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUEST_DURATION = Histogram(
    "grantloom_http_request_duration_seconds",
    "HTTP request latency by route template.",
    ["method", "route", "status"],
)
GUARD_VERDICTS = Counter(
    "grantloom_guard_verdicts_total",
    "Guardrail gate verdicts.",
    ["gate", "outcome", "backend"],
)
ROUTER_DECISIONS = Counter(
    "grantloom_router_decisions_total",
    "G2 strategy router decisions.",
    ["strategy", "backend"],
)
DECISION_RETRIES = Counter(
    "grantloom_decision_retries_total",
    "Decision judge call retries after a transient upstream failure or an invalid verdict.",
    ["kind"],
)
TOOL_INVOCATIONS = Counter(
    "grantloom_tool_invocations_total",
    "Retriever agent tool executions.",
    ["tool", "outcome"],
)
REWRITES = Counter(
    "grantloom_rewrites_total",
    "Bounded rewrite loop iterations triggered by the validator.",
    ["reason"],
)
INGESTION_JOBS = Counter(
    "grantloom_ingestion_jobs_total",
    "Outbox ingestion job outcomes.",
    ["outcome"],
)
DOCUMENTS = Gauge("grantloom_documents", "Registered documents by state.", ["state"])
CHUNKS = Gauge("grantloom_chunks", "Registered document chunks.")
VECTORS = Gauge("grantloom_vectors", "Vectors in the dense index.")
