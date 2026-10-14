# GrantLoom 🧶

> A backend-first, self-hosted RAG appliance for academic research labs: it cross-references funding
> solicitations against institutional documents and answers with citations that resolve to file,
> page, line, sheet and row coordinates.

**Status:** Phase 1 (native Windows, embedded stores) implemented; Phase 2 containerized and verified.
Test suite and evaluation numbers below are reproducible with the commands shown — see
[Evaluation](#evaluation) for exactly what each number does and does not measure.

---

## The Grant Intelligence & Compliance Business Process

To understand why GrantLoom is architected the way it is, it is essential to understand the real-world operational and financial lifecycle of competitive research grants from an institutional business perspective:

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   THE GRANT LIFECYCLE                                       │
│                                                                                             │
│  [20+ Agency Solicitations]             [Institutional Evidence Vault]                      │
│  DOE, NSF, NIH, ARPA-E, NASA            Past Proposals, Budgets (.xlsx), Facilities, CVs    │
│            │                                              │                                 │
│            ▼                                              ▼                                 │
│  ┌───────────────────┐                          ┌───────────────────┐                       │
│  │ 1. Opportunity    │  Filter by eligibility,  │ 2. Capability     │  Match equipment,     │
│  │    Funneling      │  cost-share & caps       │    Synthesis      │  PI record & space    │
│  └─────────┬─────────┘                          └─────────┬─────────┘                       │
│            │                                              │                                 │
│            └──────────────────────┬───────────────────────┘                                 │
│                                   ▼                                                         │
│                      ┌─────────────────────────┐                                            │
│                      │ 3. Budget & Math Guard  │  SQL aggregation over spreadsheet cells    │
│                      └────────────┬────────────┘  (salaries, fringe, equipment, cost share) │
│                                   ▼                                                         │
│                      ┌─────────────────────────┐                                            │
│                      │ 4. Compliance Matrix    │  Sentence-level solicitation audit:        │
│                      │    & Gap Analysis       │  Satisfied · Partial · Gap (XLSX export)   │
│                      └────────────┬────────────┘                                            │
│                                   ▼                                                         │
│                      ┌─────────────────────────┐                                            │
│                      │ 5. Citation-Grounded    │  Co-writing with strict [chunk_id] markers │
│                      │    Proposal Drafting    │  resolving to page/sheet/row coordinates   │
│                      └─────────────────────────┘                                            │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1. The Stakes & The Players
* **Federal Funding Agencies (DOE, NSF, NIH, ARPA-E, NASA, DoD, USDA, EPA)**: Issue multi-million dollar Notice of Funding Opportunities (NOFOs / Solicitations / RFPs) containing 20 to 80 pages of dense statutory, technical, and budgetary rules.
* **Research Laboratories & Universities**: Academic labs, medical centers, and clean-tech startups depend on grant awards ($1M to $10M+) to fund research personnel, postdoctoral salaries, graduate stipends, and specialized capital equipment.
* **The Disqualification Trap**: Grants have strict administrative compliance barriers. Missing a single mandatory clause—such as failing to satisfy a 20% non-federal cost-matching formula, omitting Contracting Officer prior-approval documentation for equipment over $50,000, or exceeding a 25-page narrative ceiling—results in **immediate administrative disqualification without substantive merit review**.

### 2. The 5-Stage Business Workflow

1. **Opportunity Discovery & Pipeline Screening (The 20-Solicitation Funnel)**:
   * A lab does not write proposals in a vacuum; it monitors an active pipeline of 15–20 open federal solicitations across agencies.
   * Senior scientists must rapidly screen opportunities: *Are we legally eligible as prime? Can the university commit the mandated non-federal matching funds? Does our PI meet the leadership threshold (e.g. ≥ 5 years post-terminal degree)?*
2. **Institutional Evidence Cross-Referencing**:
   * Proposals require synthesizing fragmented institutional records:
     * **Past Proposals (`Past_Proposal.pdf`)**: Demonstrates past award performance, established track record, and prior uptime metrics (e.g., 99.4% microgrid reliability).
     * **Facilities & Capabilities Statements (`Facilities_Capability_Statement.pdf`)**: Proves the physical existence of required equipment (e.g., 4,500 sq ft testing space, 100 kW regenerative AC grid emulator, RTDS testbeds).
     * **Biosketches & Letters of Commitment (`Cost_Share_Commitment_Letter.pdf`)**: Verifies non-federal cash commitments and PI credentials.
3. **Tabular Budget Verification & Mathematical Grounding**:
   * Grant budgets are governed by strict formulas stored in multi-sheet Excel workbooks (`Lab_Budget.xlsx`).
   * Generic text LLMs fail catastrophically at tabular math. GrantLoom's `SqlGuard` runs deterministic SQL queries directly over spreadsheet cell schemas to verify personnel salaries, effort months, travel sub-allocations, and equipment direct costs.
4. **Automated Compliance Matrix & Readiness Auditing**:
   * Before committing weeks of writing, the team audits the solicitation's mandatory requirement sentences (`must`, `shall`, `required`, `may not exceed`).
   * GrantLoom grades institutional readiness per requirement: **Satisfied** (grounded in active evidence), **Partial** (preliminary evidence exists but missing documentation), or **Gap** (missing compliance asset), exporting a styled audit report to Excel.
5. **Verifiable Co-Writing (Citations by Construction)**:
   * During proposal drafting, every assertion must trace back to immutable source coordinates (filename, page number, sheet name, row numbers).
   * GrantLoom's language model is restricted to emitting `[chunk_id]` tokens that the backend resolves and validates against the source document chunks, eliminating ghost citations and hallucinations.

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
| **G2 router** | Decision backend picks `hybrid` / `keyword` / `structured` / `graph` / `composite` (`semantic` only with a semantic embedder); below `decision.router_min_confidence` it falls back to `hybrid`. When `composite` is selected for complex queries, the agent executes multi-source retrieval across tabular SQL, relationship graph, and narrative vectors. |
| **G3 context** | Drops chunks tainted at ingestion, injection-like chunks and irrelevant chunks; reranks by verdict. |
| **G4 output** | Resolves citations, then verifies every claim against the evidence it cites. |
| **Ingestion** | Tier 1 regex on every chunk; Tier 2 semantic check on flagged chunks plus a random sample; quarantine when tainted *chunks* exceed `guardrails.taint_threshold_chunks`. Graph extraction indexes entities into KùzuDB with full error diagnostics. |

The **decision backend** (`decision.backend`) supports:
* `llm_judge`: a prompt-based judge on the configured judge model (OpenRouter or Ollama).
* `jev`: the TypeSafe Jev decision model over OpenRouter — the same prompt-based judge pointed at a
  high-speed, non-autoregressive discriminative model, reported under its own `jev:<model>` label.

There is deliberately **no heuristic or fallback decision backend** (decisions-log D31): keyword rules
cannot judge meaning or catch paraphrased injections. A judge that fails after its bounded retries
raises a `DECISION_BACKEND_ERROR` (RFC-7807) — the request fails loudly instead of silently degrading.
Every verdict reports which backend produced it (SSE `backend` fields); retries are metered in
`grantloom_decision_retries_total`.

### Retriever tools & Agentic RAG

`dense_vector_search` and `bm25_keyword_search` (fused with RRF), `sql_tabular_query` (the writer model
generates a SELECT over the spreadsheet schemas, executed through the AST guard + SQLite authorizer; the
rows behind the result become citable chunks) and `graph_traverse_query` (entities named in the question
are linked to parameterized Cypher templates; edges carry the chunk they were extracted from). For complex
cross-source queries, the **composite agentic RAG** strategy coordinates and merges evidence from all tools
before synthesis and G4 claim verification. Tool results reach the writer and the G4 verifier.
The abstention gate (FR-Q8) answers "no information found" unless the dense cosine score reaches
`retrieval.min_relevance_threshold` **or** the lexical overlap of the query with a BM25 hit reaches
`retrieval.min_bm25_overlap`. The two signals are on different scales and are calibrated separately;
the overlap signal is an honest lexical heuristic, not a semantic relevance score.

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

A Playwright E2E suite (`e2e/run_e2e.py`) exercises the **live deployment** through a real browser
(ingestion, grounded query + citations, injection refusal, compliance streaming + xlsx export,
Grafana, deletion saga) and produces `e2e/GRANTLOOM_SUBMISSION.pdf` as submission evidence — see
[e2e/README.md](e2e/README.md).

## Evaluation

`config/eval/golden_eval.json` holds 50 questions whose expected values were checked against the
synthetic corpus (38 answerable, 7 unanswerable, 5 canonical injections);
`config/eval/adversarial_heldout.json` holds 15 paraphrased injections that avoid the regex patterns.
The runner builds an isolated temporary workspace (it never touches `data/`), ingests the generated corpus
through the real worker, and asks every question through the same pipeline as the API.

```powershell
python -m app.cli.run_eval                                   # stub writer + real judge (OPENROUTER_API_KEY in .env)
python -m app.cli.run_eval --writer openrouter --decision llm_judge --json-out report.json   # full real-model run
python -m app.cli.run_eval --decision jev                    # judge with the TypeSafe Jev decision model
```

* **Answer accuracy / recall@k** use whole-value matching (normalized numbers, word boundaries, or an
  explicit `expected_regex`); recall is measured on the chunks actually given to the writer.
* **Faithfulness** is judged by an independent judge of a different model family (NFR-6) and only when a
  real writer runs; the offline writer is an extractive stub, so faithfulness is *not measured* there.

### Results (2026-10-13, this workstation, deterministic embeddings)

Historical offline run (2026-10-13, extractive stub writer + the lexical decision double that now
lives in `tests/stub_backend.py`; since D31 the CLI requires a real judge). This is a
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
| Injection block rate, held-out paraphrases | **0%** | ≥ 95% | the offline test double cannot see paraphrases; needs `llm_judge` |

**Real-model numbers are not yet measured.** Run
`python -m app.cli.run_eval --writer openrouter --decision llm_judge --json-out report.json` with
`OPENROUTER_API_KEY` in `.env` (writer `models.writer_model`, independent judge `models.judge_model`).
Local Ollama is excluded from all standard test and evaluation plans due to slow local inference. `pytest` runs fully offline (it injects a lexical decision double); `run_eval` always needs a real judge model, so it requires `OPENROUTER_API_KEY` (or `--writer ollama`).

**Known limitations:** embeddings are lexical by default; graph extraction is pattern-based (titles,
roles, credentials, "Project Title:" fields) and misses entities without such cues; spreadsheet formulas
stored without cached values are re-evaluated at extraction only for the safe arithmetic subset
(`SUM(range)`, `+ - * /` over cell references) — `IF`/`VLOOKUP`/cross-sheet formulas stay empty;
small local judge models are noticeably less reliable than hosted ones and occasionally return
malformed verdicts (the judge retries a bounded number of times, metered as
`grantloom_decision_retries_total`, then fails the request loudly — there is no fallback backend).

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
templates/      Prompts, refusals, extraction lexicon (entity/requirement cues — not decisions)
alembic/        Migrations
tests/          unit + integration (helpers.py wires real adapters in tmp dirs)
```

Design documents: [PRD.md](PRD.md), [decisions-log.md](decisions-log.md),
[implementation-plan.md](implementation-plan.md), [data-strategy.md](data-strategy.md).
