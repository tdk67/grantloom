"""Access control, SSRF-safe diagnostics, startup safety checks, readiness, and embedding-space guard."""

from __future__ import annotations

import httpx
import pytest

from app.adapters.chroma_adapter import ChromaVectorStoreAdapter, EmbeddingSpaceMismatchError
from app.adapters.embedder_adapter import DeterministicEmbedder
from app.api import deps
from app.core.config import SecretsConfig
from app.ports.models import ChunkVector
from app.services.startup_checks import StartupCheckError, run_startup_checks
from tests.helpers import VALID_KEY, ScriptedLLM, make_settings

MASTER = "test-master-key"

PROTECTED = [
    ("GET", "/api/v1/documents"),
    ("GET", "/api/v1/documents/doc_x"),
    ("DELETE", "/api/v1/documents/doc_x"),
    ("POST", "/api/v1/ask"),
    ("POST", "/api/v1/compliance/doc_x"),
    ("GET", "/api/v1/compliance/doc_x"),
    ("GET", "/api/v1/compliance/doc_x/export"),
    ("GET", "/api/v1/examples"),
    ("POST", "/api/v1/admin/generate-synthetic-data"),
    ("POST", "/api/v1/admin/test-model-capabilities"),
    ("POST", "/api/v1/auth/validate-key"),
    ("POST", "/api/v1/compliance/doc_x/stream"),
    ("GET", "/api/v1/info"),
]


def _client(app, client_host: str = "127.0.0.1") -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(client_host, 5000)), base_url="http://test")


async def _call(client: httpx.AsyncClient, method: str, path: str, headers: dict | None = None) -> httpx.Response:
    return await client.request(method, path, headers=headers or {}, json={"query": "q"} if path.endswith("/ask") else None)


@pytest.mark.asyncio
async def test_every_api_route_requires_the_master_key_when_configured(stack):
    stack.settings = make_settings(master_api_key=MASTER)
    async with _client(stack.app(ScriptedLLM())) as client:
        for method, path in PROTECTED:
            assert (await _call(client, method, path)).status_code == 401, path
            wrong = await _call(client, method, path, {"X-API-Key": "nope"})
            assert wrong.status_code == 401, path
        ok = await client.get("/api/v1/documents", headers={"X-API-Key": MASTER})
        assert ok.status_code == 200


@pytest.mark.asyncio
async def test_without_master_key_only_loopback_clients_are_served(stack):
    app = stack.app(ScriptedLLM())
    async with _client(app, "203.0.113.7") as remote:
        for method, path in PROTECTED:
            assert (await _call(remote, method, path)).status_code == 401, path
    async with _client(app, "127.0.0.1") as local:
        assert (await local.get("/api/v1/documents")).status_code == 200


@pytest.mark.asyncio
async def test_public_byok_mode_serves_remote_clients_without_master_key(stack):
    """D27: public BYOK demo — no server key, but LLM calls still require X-OpenRouter-Key."""
    stack.settings = make_settings(server=make_settings().server.model_copy(update={"public_byok": True}))
    app = stack.app(ScriptedLLM(), mode="byok")
    async with _client(app, "203.0.113.7") as remote:
        assert (await remote.get("/api/v1/documents")).status_code == 200
        assert (await remote.get("/api/v1/examples")).status_code == 200
        no_key = await remote.post("/api/v1/ask", json={"query": "what is the cost share?"})
        assert no_key.status_code == 401


@pytest.mark.asyncio
async def test_capability_probe_rejects_caller_supplied_endpoints(stack):
    async with _client(stack.app(ScriptedLLM())) as client:
        ssrf = await client.post("/api/v1/admin/test-model-capabilities", json={"endpoint": "http://169.254.169.254/"})
        assert ssrf.status_code == 422
        ok = await client.post("/api/v1/admin/test-model-capabilities", json={"role": "judge"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["model"] == "mock-judge" and body["context_length"] == 32768 and body["destination"] == "scripted.local"


@pytest.mark.asyncio
async def test_validate_key_endpoint_probes_once_and_caches(stack):
    """D28: POST /api/v1/auth/validate-key live-probes the key; 401 on garbage, 200 + cached on valid."""
    app = stack.app(ScriptedLLM())
    async with _client(app) as client:
        bad = await client.post("/api/v1/auth/validate-key", headers={"X-OpenRouter-Key": "sk-or-v1-garbage"})
        assert bad.status_code == 401
        missing = await client.post("/api/v1/auth/validate-key")
        assert missing.status_code == 401
        ok = await client.post("/api/v1/auth/validate-key", headers={"X-OpenRouter-Key": VALID_KEY})
        assert ok.status_code == 200
        body = ok.json()
        assert body["valid"] is True and body["mode"] == "byok"
        profile = app.dependency_overrides[deps.get_inference_profile]()
        assert profile.key_cache.is_fresh(VALID_KEY)  # remembered: later asks skip the probe


def test_validated_key_cache_process_lifetime_and_cap():
    from app.services.inference import ValidatedKeyCache

    cache = ValidatedKeyCache(ttl_seconds=0, max_entries=2)  # D28: 0 = valid for the process lifetime
    cache.remember("k1")
    assert cache.is_fresh("k1") and not cache.is_fresh("k2")
    cache.remember("k2")
    cache.remember("k3")  # FIFO-evicts k1
    assert not cache.is_fresh("k1") and cache.is_fresh("k2") and cache.is_fresh("k3")

    expiring = ValidatedKeyCache(ttl_seconds=60, max_entries=10)
    expiring.remember("a")
    assert expiring.is_fresh("a")


def test_startup_refuses_public_bind_without_master_key():
    public = make_settings(server=make_settings().server.model_copy(update={"host": "0.0.0.0"}))
    with pytest.raises(StartupCheckError):
        run_startup_checks(public)
    # D27: public_byok allows the keyless public bind in cloud mode (warning), never in private mode
    byok = make_settings(server=make_settings().server.model_copy(update={"host": "0.0.0.0", "public_byok": True}))
    assert any("public BYOK" in w for w in run_startup_checks(byok))
    private = byok.model_copy(update={"llm": byok.llm.model_copy(update={"private_mode": True})})
    with pytest.raises(StartupCheckError):
        run_startup_checks(private)
    secured = public.model_copy(update={"secrets": SecretsConfig(_env_file=None, master_api_key=MASTER)})
    run_startup_checks(secured)


def test_startup_warns_on_shared_writer_judge_family():
    llm = make_settings().llm.model_copy(update={"ollama_judge_model": "qwen2.5:7b"})
    warnings = run_startup_checks(make_settings(llm=llm))
    assert any("NFR-6" in w and "qwen" in w for w in warnings)


def test_vector_store_refuses_mixed_embedding_spaces(tmp_path):
    path = tmp_path / "chroma"
    first = DeterministicEmbedder(dimension=8)
    store = ChromaVectorStoreAdapter(path, first.fingerprint)
    store.add_chunks([ChunkVector("d:1:0", "d", [0.1] * 8, "text")])
    with pytest.raises(EmbeddingSpaceMismatchError):
        ChromaVectorStoreAdapter(path, "openrouter:text-embedding-3-small:1536")
    assert ChromaVectorStoreAdapter(path, first.fingerprint).count() == 1


@pytest.mark.asyncio
async def test_readiness_and_metrics(stack):
    async with _client(stack.app(ScriptedLLM())) as client:
        ready = await client.get("/readyz")
        await client.get("/api/v1/documents")
        metrics = await client.get("/metrics/")
    assert ready.status_code == 200 and ready.json()["checks"] == {"registry": "ok", "vector_store": "ok", "graph_store": "ok"}
    assert "grantloom_http_request_duration_seconds" in metrics.text
    assert "grantloom_guard_verdicts_total" in metrics.text


@pytest.mark.asyncio
async def test_rate_limit_returns_problem_json(stack, monkeypatch):
    limited = make_settings(rate_limits=make_settings().rate_limits.model_copy(update={"ask": "1/minute"}))
    monkeypatch.setattr("app.core.rate_limit.get_settings", lambda: limited)
    async with _client(stack.app(ScriptedLLM())) as client:
        headers = {"X-OpenRouter-Key": "sk-valid-openrouter-key"}
        first = await client.post("/api/v1/ask", headers=headers, json={"query": "What is the grant budget?"})
        second = await client.post("/api/v1/ask", headers=headers, json={"query": "What is the grant budget?"})
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["content-type"] == "application/problem+json" and second.json()["code"] == "RATE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_info_reports_mode_and_byok_requirement(stack):
    async with _client(stack.app(ScriptedLLM())) as client:
        info = (await client.get("/api/v1/info")).json()
    assert info["mode"] == "byok" and info["requires_byok_key"] is True
    assert info["data_leaves_machine"] is True and info["destination"] == "scripted.local"
