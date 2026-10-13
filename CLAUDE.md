# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

GrantLoom is a self-hosted RAG appliance (FastAPI, Python 3.12) that answers questions about grant solicitations and institutional documents with verifiable citations. Phase 1 runs natively on Windows using embedded stores only: SQLite, ChromaDB and KùzuDB, with no Docker. Phase 2 (Docker/VPS, M8–M10) has not started.

Design docs live at the repo root: `PRD.md`, `decisions-log.md` (D1–D26) and `implementation-plan.md`. `data-strategy.md` Part 3 (Jev/Julia-1) describes deferred designs (D22), not existing code. `reviews/` is gitignored scratch space for external reviews.

## Commands

The venv at `.venv` was created by uv (`uv.lock` is checked in). Paths resolve against the project root, so commands work from any directory.

```powershell
uv sync                                         # or: pip install -e .
python -m app                                   # run server on config server.host/port (runs startup checks)
python -m app.cli.db upgrade head               # also: downgrade [rev], current, history (auto-applied at startup)

pytest -q                                       # full suite, no network/Ollama needed
pytest tests/integration/test_ask_pipeline.py::test_rewrite_exhaustion_returns_unverified_warning

python -m app.cli.run_eval                      # offline pipeline smoke run (isolated temp workspace)
python -m app.cli.run_eval --writer openrouter --decision llm_judge [--ids Q01,Q02] --json-out r.json  # needs OPENROUTER_API_KEY
python -m app.cli.reindex                       # rebuild vectors after changing the embedding backend
python -m app.cli.generate_synthetic            # demo corpus into database.synthetic_path
docker compose build && docker compose up -d    # Phase 2 deploy (127.0.0.1:8092, nginx in front)
python3 e2e/run_e2e.py                          # live-deployment Playwright E2E (one instance at a time)
python3 e2e/make_submission_pdf.py              # -> e2e/GRANTLOOM_SUBMISSION.pdf
```

No linter or type checker is configured. Don't run or wait on local Ollama models for testing (too slow); use the offline eval and pytest.

## Configuration rules (strict)

- **No defaults in code (NFR-3).** Every operational value comes from `config/config.json` through the `extra="forbid"` Pydantic models in `app/core/config.py`. A new setting must be added to both. This covers timeouts, limits, thresholds, rate limits, chunk sizes and model names. Constructors take these values explicitly.
- **`.env` holds secrets only:** `MASTER_API_KEY` and `OPENROUTER_API_KEY`. `GRANTLOOM_CONFIG_PATH` overrides the config file location.
- **Prompt text lives in `templates/`.** Prompts go in `templates/prompts/` and are rendered with `render_template(path, key=value)`, which fills `<<KEY>>` placeholders. Refusals go in `templates/refusals/`. Heuristic vocabulary goes in `templates/decision/heuristic_lexicon.json`. Never put prompts in code.

## Architecture

The code follows a hexagonal layout: `api -> services -> ports <- adapters`. `app/api/deps.py` is the composition root, with lazy singletons built from Settings and `reset_dependencies()` to clear them.

**Provider selection:** `app/services/inference.py` decides which provider serves requests, based only on `llm.private_mode`:
- `false`: OpenRouter. The `X-OpenRouter-Key` header is mandatory. The key is validated **once** — when the UI saves it (`POST /api/v1/auth/validate-key`) or on first use — and the digest is then remembered for the process lifetime (`llm.byok_validation_ttl_seconds: 0`; FIFO-capped by `llm.byok_validated_keys_max`). Revoked keys still fail immediately because every real provider call maps upstream 401/402 to RFC 7807 errors.
- `true`: local Ollama. No key is accepted or forwarded.

A server-side key never changes the provider. `InferenceProfile.egress()` feeds the SSE `egress` event and `GET /api/v1/info`.

**Decision backend:** `DecisionBackendPort` has typed methods per gate: `screen_query`, `route_query`, `judge_chunks`, `verify_claims`, `scan_for_injection` and `assess_requirement`. Implementations live in `app/adapters/decision_backends.py`:
- `LLMJudgeDecisionBackend`: JSON verdicts validated by Pydantic schemas.
- `HeuristicDecisionBackend`: regex plus lexical overlap.
- `FallbackDecisionBackend`: wraps a primary backend. When the primary fails it uses the fallback, relabels the verdict's `backend` and increments a metric.

Auth and payment errors are never masked. The judge model must differ in family from the writer (NFR-6, `model_family()`).

**One query pipeline:** `app/services/agent_loop.py`. `AskService` only wires it. `AgentLoopOrchestrator.stream()` yields SSE events; `run()` consumes the same stream for the eval runner. The stages are:
1. `PlannerAgent`: G1 (`InputGuard`), then G2 (`StrategyRouter`).
2. `RetrieverAgent`: hybrid RRF retrieval, plus `StructuredQueryTool` (LLM-written SQL through `SqlGuard`) or `GraphQueryTool` (Cypher templates). Then the G3 `ContextFilter`.
3. `WriterAgent`: streams the draft.
4. `ValidatorAgent`: G4 `OutputGuard` resolves citations against in-context chunks of active docs and verifies each claim.

Up to `max_rewrite_attempts` rewrites follow, each starting with a `rewrite` event carrying `discard_draft`. If verification still fails, `done` carries `verified: false` and `warning`. The exact abstention text is `ABSTENTION_MESSAGE` (FR-Q8).

**Core invariant:** the LLM only emits `[chunk_id]` markers, and the backend turns them into coordinates. Chunk IDs are `doc_id:page:index`, and spreadsheet chunks also carry `sheet_name` and `row_start`/`row_end`. Graph edges carry `source_chunk_id`, and graph deletion purges by the `doc_id:` prefix.

**Ingestion:** `app/workers/ingest_worker.py` runs the outbox stages extracting, chunking, scanning, embedding, indexing, then active.
- Every attempt first purges derived data (NFR-5). Failures are retried up to `max_job_attempts`, and stale `in_progress` jobs are reclaimed.
- Extraction and chunking run via `asyncio.to_thread`.
- `IngestionScanner` counts *chunks*, not regex matches. Tainted chunks are marked `metadata.tainted` and dropped at G3. A document is quarantined when tainted chunks exceed `taint_threshold_chunks`.
- Deletion is a `deleting` outbox job (a saga), and `DELETE` returns 202. The PDF extractor strips running headers and footers, and retrieval suppresses near-duplicate chunks.
- The Chroma collection stores the embedder fingerprint and refuses to mix embedding spaces.

**Compliance:** `ComplianceService` extracts the solicitation's own requirement sentences, retrieves lab evidence from the other active documents, and classifies each via the decision backend. Assessments run with bounded concurrency (`compliance.max_concurrent_assessments`); `POST /api/v1/compliance/{id}/stream` emits `start` → `progress` (per requirement) → `done` so the UI shows a real progress bar. Records persist in extraction order (`REQ-01..n`) even though progress events arrive in completion order.

**Access:** every `/api/v1` router carries `Depends(verify_master_api_key)`. Without `MASTER_API_KEY`, only loopback clients are served — unless `server.public_byok: true` (D27, used by the Docker deploy): remote clients are served without a server key, but every LLM call still requires the caller's live-probed `X-OpenRouter-Key`. Startup (`app/services/startup_checks.py`) refuses a non-loopback host when no key is set and `public_byok` is off, and refuses `public_byok` combined with `private_mode` (free local inference must never be public). Rate limits use slowapi; endpoints need a `request: Request` parameter. The VPS nginx adds TLS, SSE unbuffered proxying, per-IP `limit_req`, and blocks public `/metrics`.

**Frontend:** a zero-build vanilla JS SPA in `app/static/` (decision D19). It uses `apiFetch()`, which attaches `X-API-Key` and `X-OpenRouter-Key` stored in IndexedDB.

## Testing patterns

`tests/helpers.py::build_stack(tmp_path)` wires real adapters (SQLite, Chroma, Kùzu, heuristic decision backend) exactly like the composition root. The `stack` fixture in `tests/conftest.py` provides it.

- `stack.ingest_bytes(name, data)` uploads a document and drains the worker.
- `stack.ask_service(llm)` builds the pipeline.
- `stack.app(llm)` returns a FastAPI app with every dependency overridden, including `get_settings` with test-controlled secrets from `make_settings()`. The developer's `.env` is never read.
- `ScriptedLLM(drafts=[...], json_handler=fn)` is the LLM double. Streamed drafts come from `drafts`; `json_output=True` calls (judge, SQL) go to `json_handler`.
- `parse_sse()` decodes responses.

The golden eval set's expected values must exist in the synthetic corpus (`app/synthetic/fixtures.py`). Check new items against extracted text before adding them.
