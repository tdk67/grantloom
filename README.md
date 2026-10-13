# GrantLoom 🧶

> A backend-first, self-hosted RAG appliance for academic research labs: it cross-references funding
> solicitations against institutional documents and answers with citations that resolve to file,
> page, line, sheet and row coordinates.

**Status:** Phase 1 (native Windows, embedded stores) implemented; Phase 2 deployed — live demo at
**https://grantloom.taskmind-ai.com** (Docker on the project VPS, public BYOK mode).
Test suite and evaluation numbers below are reproducible with the commands shown — see
[Evaluation](#evaluation) for exactly what each number does and does not measure.

---

## What it does

* **Ingestion** of PDF (text layer), TXT, CSV and XLSX through an asynchronous SQLite outbox:
  extract → chunk → injection scan → embed → index (vectors, FTS5, spreadsheet tables, graph).
  SHA-256 deduplication; same filename with new content becomes version *n+1*; failed jobs are retried
  and jobs orphaned by a crash are reclaimed; deletion is a saga that purges every store.
* **Question answering** through one agent pipeline (Planner → Retriever → Writer → Validator) used by
  both the API and the evaluation runner, streamed as Server-Sent Events.
* **Citations by construction**: the writer only emits `[chunk_id]` markers; the backend resolves them
  against the registry (in-context chunks of active documents only) and verifies each claim against
  its cited evidence. Failing drafts are rewritten up to `guardrails.max_rewrite_attempts` times;
  if verification still fails, the answer is returned with an explicit *unverified* warning.
* **Compliance matrix**: requirement sentences are extracted from the solicitation itself, persisted
  with their source chunk, matched against lab evidence, classified Satisfied / Partial / Gap, and
  exported to `.xlsx` with a readiness *estimate*.
* **BYOK or private**: either OpenRouter with the user's own key (validated live, never stored or
  logged) or local Ollama models with no data leaving the machine.

## Architecture

```
Browser SPA (vanilla JS)  ──HTTP/SSE──▶  FastAPI  (X-API-Key, rate limits, RFC 7807 errors)
                                            │
            ┌───────────────────────────────┼───────────────────────────────┐
            ▼                               ▼                               ▼
   Ingestion worker (outbox)     Agent pipeline (/ask, eval)        Compliance service
            │                               │
            ▼                               ▼
   Hexagonal ports:  Registry · VectorStore · StructuredStore · GraphStore · Embedder · LLMClient · DecisionBackend
            │
            ▼
   SQLite (WAL, FTS5, tables) · ChromaDB (embedded) · KùzuDB (embedded) · OpenRouter or Ollama
```

### Guardrail gates and the decision backend

| Gate | What runs |
|---|---|
| **G1 input** | Regex prefilter (non-English scripts, canonical injection phrases), then the decision backend's semantic screen (paraphrased injections, domain relevance). |
| **G2 router** | Decision backend picks `hybrid` / `keyword` / `structured` / `graph` (`semantic` only with a semantic embedder); below `decision.router_min_confidence` it falls back to `hybrid`. |
| **G3 context** | Drops chunks tainted at ingestion, injection-like chunks and irrelevant chunks; reranks by verdict. |
| **G4 output** | Resolves citations, then verifies every claim against the evidence it cites. |
| **Ingestion** | Tier 1 regex on every chunk; Tier 2 semantic check on flagged chunks plus a random sample; quarantine when tainted *chunks* exceed `guardrails.taint_threshold_chunks`. |

The **decision backend** (`decision.backend`) is `llm_judge` — a prompt-based judge on the configured
judge model — with a `heuristic` (regex + lexical overlap) fallback. Every verdict reports which backend
produced it (SSE `backend` fields, `grantloom_decision_fallbacks_total`). **Jev and Julia-1 from the data
strategy are deferred and not implemented** (decisions-log D22).

### Retriever tools

`dense_vector_search` and `bm25_keyword_search` (fused with RRF), `sql_tabular_query` (the writer model
generates a SELECT over the spreadsheet schemas, executed through the AST guard + SQLite authorizer; the
rows behind the result become citable chunks) and `graph_traverse_query` (entities named in the question
are linked to parameterized Cypher templates; edges carry the chunk they were extracted from). Tool
results reach the writer and the G4 verifier.

## Quickstart

```powershell
uv sync                                   # or: python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -e .
Copy-Item .env.example .env               # secrets only: MASTER_API_KEY, OPENROUTER_API_KEY
python -m app.cli.db upgrade head         # also applied automatically at startup
python -m app                             # serves on server.host:server.port from config/config.json
```

Open <http://localhost:8000/>, open **Settings** and enter your OpenRouter key (BYOK mode) and, if the
server sets `MASTER_API_KEY`, the server access key — on the public demo instance no server key is
needed, only your own OpenRouter key). Click **Generate Demo Data** to index the 6-document synthetic corpus.

The key you save is validated once against OpenRouter (D28), so asking a question does not pay a
validation round-trip each time.

### Configuration

* `config/config.json` holds every operational setting (no defaults in code); `.env` holds secrets only.
* `llm.private_mode: true` → local Ollama (`ollama_writer_model`, `ollama_judge_model`); `false` →
  OpenRouter with a per-request `X-OpenRouter-Key`. The writer and judge must come from different model
  families (startup warning otherwise).
* `models.embedding_backend`: `deterministic` (default; local lexical hashing — **not semantic**) or
  `openrouter` (needs `OPENROUTER_API_KEY`). Switching requires `python -m app.cli.reindex`.
* Without `MASTER_API_KEY` the API only answers loopback clients, and the server refuses to start on a
  non-loopback `server.host` — unless `server.public_byok: true` (public demo mode, D27): the instance
  serves everyone, but every LLM-costing call still requires the caller's own `X-OpenRouter-Key`
  (live-probed), and nginx applies per-IP rate limits. `public_byok` never applies to
  `private_mode: true` (a public bind without a key would expose free local inference — startup refuses).
* Models: writer `deepseek/deepseek-v4-flash`, judge `openai/gpt-4o-mini` (cheap, different families
  per NFR-6). Swap via `models.writer_model` / `models.judge_model`.

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/documents` | Upload; `200` for an already-indexed hash, `202` otherwise, `409` for quarantined content |
| GET | `/api/v1/documents`, `/api/v1/documents/{id}` | State, stage, progress %, version, scan results |
| DELETE | `/api/v1/documents/{id}` | `202` — deletion saga |
| POST | `/api/v1/ask` | SSE: `egress, guard, route, plan, tool*, retrieve, filter, generate*, verify, rewrite?, done` |
| POST / GET | `/api/v1/compliance/{id}` | Evaluate / read the stored matrix; `GET .../export` → `.xlsx` |
| POST | `/api/v1/compliance/{id}/stream` | Same evaluation as SSE: `start` → `progress` per requirement → `done` (drives the UI progress bar) |
| POST | `/api/v1/auth/validate-key` | Validate the BYOK key once, on save; the server then skips per-request probes (D28) |
| GET | `/api/v1/info` | Mode, provider, egress destination, role models, decision backend |
| GET | `/api/v1/examples` | Starter questions |
| POST | `/api/v1/admin/generate-synthetic-data`, `/api/v1/admin/test-model-capabilities` | Demo corpus; provider-reported facts for the configured models |
| GET | `/healthz`, `/readyz`, `/metrics` | Liveness, store readiness, Prometheus |

All `/api/v1` routes require `X-API-Key` when `MASTER_API_KEY` is set.

## Testing

```powershell
pytest -q                                              # full suite (no network, no Ollama needed)
pytest tests/integration/test_ask_pipeline.py -q       # one module
pytest tests/integration/test_ask_pipeline.py::test_rewrite_exhaustion_returns_unverified_warning
```

## Evaluation

`config/eval/golden_eval.json` holds 50 questions whose expected values were checked against the
synthetic corpus (38 answerable, 7 unanswerable, 5 canonical injections);
`config/eval/adversarial_heldout.json` holds 15 paraphrased injections that avoid the regex patterns.
The runner builds an isolated temporary workspace (it never touches `data/`), ingests the generated corpus
through the real worker, and asks every question through the same pipeline as the API.

```powershell
python -m app.cli.run_eval                                   # offline smoke run (no model)
python -m app.cli.run_eval --writer openrouter --decision llm_judge --json-out report.json   # OPENROUTER_API_KEY in .env
```

* **Answer accuracy / recall@k** use whole-value matching (normalized numbers, word boundaries, or an
  explicit `expected_regex`); recall is measured on the chunks actually given to the writer.
* **Faithfulness** is judged by an independent judge of a different model family (NFR-6) and only when a
  real writer runs; the offline writer is an extractive stub, so faithfulness is *not measured* there.

### Results (2026-10-13, this workstation, deterministic embeddings)

Offline run (`python -m app.cli.run_eval`: extractive stub writer, heuristic decision backend). This is a
pipeline/retrieval check, not a model-quality claim:

| Metric | Result | PRD target | Note |
|---|---|---|---|
| Recall@k (answer present in writer context) | 97.4% | ≥ 85% | lexical embeddings + BM25 |
| False abstention (answerable questions) | 0.0% | – | |
| Answer accuracy | 52.6% | – | stub copies one sentence; not a model score |
| Citation resolvability | 100% | 100% | |
| Faithfulness | not measured | ≥ 95% | circular for an extractive stub |
| Abstention accuracy (unanswerable) | 100% | ≥ 90% | |
| Injection block rate, canonical | 100% | ≥ 95% | regex prefilter |
| Injection block rate, held-out paraphrases | **0%** | ≥ 95% | the heuristic backend cannot see paraphrases; needs `llm_judge` |

**Real-model numbers are not yet measured.** Run
`python -m app.cli.run_eval --writer openrouter --decision llm_judge --json-out report.json` with
`OPENROUTER_API_KEY` in `.env` (writer `models.writer_model`, independent judge `models.judge_model`).
Local Ollama is excluded from all standard test and evaluation plans due to slow local inference; standard testing uses the fast offline pipeline runner (`pytest`, `python -m app.cli.run_eval`) or OpenRouter cloud models.

**Known limitations:** embeddings are lexical by default; graph extraction is pattern-based (titles,
roles, credentials, "Project Title:" fields) and misses entities without such cues; spreadsheet formulas
stored without cached values are re-evaluated at extraction only for the safe arithmetic subset
(`SUM(range)`, `+ - * /` over cell references) — `IF`/`VLOOKUP`/cross-sheet formulas stay empty;
small local judge models are noticeably less reliable than hosted ones and occasionally return
malformed verdicts (the fallback handles these and reports it).

## Deployment (Phase 2 — live at https://grantloom.taskmind-ai.com)

Multi-stage `Dockerfile` (python:3.12-slim, non-root) + `docker-compose.yml`:

```bash
docker compose build && docker compose up -d   # serves 127.0.0.1:8092 (loopback only)
```

* `deploy/config.docker.json` (mounted read-only via `GRANTLOOM_CONFIG_PATH`) sets
  `server.host: 0.0.0.0` and `server.public_byok: true` — the public demo runs **without a master
  API key** (D27): every visitor supplies their own `X-OpenRouter-Key`, which is live-probed per
  session; uploads/documents are open but rate-limited.
* State lives in the `grantloom_data` Docker volume (`/app/data`: SQLite, Chroma, Kùzu, uploads);
  memory is capped at 2.5 GB (actual ~230 MB).
* nginx (`/etc/nginx/sites-available/grantloom.taskmind-ai.com`): TLS via Let's Encrypt with
  HTTP→HTTPS redirect, `proxy_buffering off` + 3600 s timeouts for SSE, `limit_req` 30 r/m per IP
  (burst 15, 429), and `/metrics` denied publicly (host Prometheus scrapes `127.0.0.1:8092` directly).
* First-run demo data: `curl -X POST https://grantloom.taskmind-ai.com/api/v1/admin/generate-synthetic-data`
  or the **Generate Demo Data** button in the UI.

## Repository layout

```
app/
  api/          FastAPI routers + deps.py (composition root)
  adapters/     SQLite registry & tables, Chroma, Kùzu, OpenRouter, Ollama, embedders, decision backends
  ports/        Hexagonal interfaces and DTOs
  services/     agent_loop (the pipeline), ask_service (facade), guardrails, retrieval, retriever_tools,
                compliance, ingestion, graph_extraction, inference (provider/role wiring), startup checks
  workers/      Outbox worker (ingestion + deletion saga)
  verification/ SQL guard, injection patterns
  cli/          db, generate_synthetic, run_eval, reindex
  static/       Vanilla JS SPA
config/         config.json, example questions, eval sets
templates/      Prompts, refusals, heuristic decision lexicon
alembic/        Migrations
tests/          unit + integration (helpers.py wires real adapters in tmp dirs)
```

Design documents: [PRD.md](PRD.md), [decisions-log.md](decisions-log.md),
[implementation-plan.md](implementation-plan.md), [data-strategy.md](data-strategy.md).
