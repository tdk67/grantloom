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

| Gate | What runs | Latency (Native Jev) |
|---|---|---|
| **G1 input** | Regex prefilter (non-English scripts, canonical injection phrases), followed by native TypeSafe Jev decisions (`noul` questions for prompt injection and domain relevance). | **~600–800 ms** |
| **G2 router** | Native TypeSafe Jev decisions (`choice` across `hybrid`, `keyword`, `structured`, `graph`, `composite`, `semantic`). | **~750–850 ms** |
| **G3 context** | Drops chunks tainted at ingestion, injection-like chunks and irrelevant chunks; reranks by verdict. | **~800 ms** |
| **G4 output** | Citation coordinate resolver (physical file, page, sheet, row) + per-claim verification via native Jev decisions (`choice` for `grounded`, `contradicted`, `unsupported`). | **~725–850 ms** |
| **Ingestion** | Tier 1 regex on every chunk; Tier 2 semantic check on flagged chunks plus a random sample; quarantine when tainted *chunks* exceed `guardrails.taint_threshold_chunks`. Graph extraction indexes entities into KùzuDB with full error diagnostics. | Batch |

The **decision backend** (`decision.backend`) supports:
* `jev` (**default & production**): Direct integration with the OpenRouter Decisions API (`https://openrouter.ai/api/alpha/decisions` via `typesafe/jev-1.13`). Uses typed non-autoregressive decision primitives (`choice`, `noul`, `score`) achieving sub-second gate latencies (~600–850ms) across G1, G2, G4, and independent evaluation verification.
* `llm_judge`: Prompt-based judge running on a conversational LLM (e.g. `openai/gpt-4o-mini`).

#### Evaluation of Alternative Guardrail Libraries (Meta Prompt-Guard vs. Native Jev)
To evaluate industry-standard non-LLM guardrail frameworks for G1, Meta's `Prompt-Guard-86M_onnx` was benchmarked directly outside the pipeline using ONNX Runtime on CPU against canonical grant and adversarial queries:
* **Latency:** Prompt-Guard ONNX averaged **1,458 ms – 2,142 ms** per query on CPU—measurably *slower* than native Jev cloud decisions (~600–800 ms).
* **Accuracy & False Positives:** Prompt-Guard exhibited a **100% false positive rate** on legitimate technical grant queries, classifying domain terms like *"standard NFPA 855 and IEEE 1547"*, *"high-temperature limits for lithium iron phosphate cells"*, and *"total requested budget across all equipment line items"* as malicious `INJECTION` with 1.00 probability.
* **Conclusion:** Native Jev via `/api/alpha/decisions` correctly passed 100% of benign technical queries while accurately flagging adversarial prompt injections in 607 ms and out-of-domain queries in 534 ms. Native Jev was selected as the primary G1/G2/G4 decision engine.

### Retriever tools & Multi-Document Agentic RAG

GrantLoom fuses semantic vector search and lexical search with tool-assisted structured tabular querying:
* **Dense Semantic Vector Search:** Embedded with `openai/text-embedding-3-small` (1536 dimensions) via OpenRouter into ChromaDB, replacing deterministic non-semantic hashes.
* **Lexical BM25 Search:** SQLite FTS5 bigram index with query sanitization.
* **Multi-Document Chunk Diversification:** When cross-document queries are routed to `composite` or `hybrid` (e.g., comparing cost-share requirements across solicitations or matching proposals to FOAs), a two-pass retention filter caps chunks from any single document to `max_per_doc = 4`. This prevents a single 10-page document from monopolizing all top-10 slots and guarantees evidence from secondary documents (proposals, commitment letters) is retained.
* **Structured AST SQL Guard:** `sql_tabular_query` translates questions over spreadsheet cell ranges into safe, read-only SQLite SQL queries, returning exact arithmetic sums and averages.
* **Knowledge Graph Traversal:** `graph_traverse_query` traverses institutional relationships (personnel, facilities, past awards) in embedded KùzuDB.

### Verifier & Rewrite Loop Optimization

* **Concise Token Budgeting:** The Writer is capped at `models.max_tokens: 350` and guided by system prompts to emit concise, 1–3 sentence grounded answers. This prevents verbose draft generation delays (reducing generation time from 30–50s down to 3–6s).
* **Targeted Rewrite Feedback:** When G4 verification detects an ungrounded claim or unresolvable citation, the writer prompt receives both the **previous draft** and specific issue reasons (`UNSUPPORTED`, `CONTRADICTED`, `UNRESOLVED`). The writer is explicitly instructed to drop unsupported claims or cleanly abstain if evidence is absent, preventing repetitive 3-turn rewrite loops.
* **Jev-Powered Verifier:** Both the pipeline G4 output guard and the benchmark evaluation harness use native Jev (`typesafe/jev-1.13`) rather than slow LLM judge calls, guaranteeing sub-second verification and model family independence (NFR-6).

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
python -m app.cli.run_benchmark --solicitation-count 10      # Canonical 10-solicitation benchmark (TC01-TC09)
python -m app.cli.run_benchmark --dilution-test              # Multi-scale dilution test (N=1, 5, 10 solicitations)
python -m app.cli.run_eval --decision jev                    # Full 50-item golden evaluation with native Jev
```

* **Answer accuracy / recall@k** use whole-value matching (normalized numbers, word boundaries, or an
  explicit `expected_regex`); recall is measured on the chunks actually given to the writer.
* **Faithfulness & Evaluator** is judged by native TypeSafe Jev (`typesafe/jev-1.13`), enforcing strict model independence from the writer (NFR-6) with sub-second decision latency (~725 ms).
* **RAG Triad Metrics:** The benchmark harness measures:
  1. **Context Relevance:** Share of retrieved chunks relevant to the ground truth target.
  2. **Groundedness / Faithfulness:** Percentage of answer claims directly supported by cited evidence coordinates.
  3. **Answer Relevance:** Direct semantic satisfaction of the user's inquiry without extraneous digression.
  4. **Citation Resolvability:** 100% verification that all bracketed chunk markers match active, in-context document coordinates.

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
* **Reindexing on Embedding Migration:** When migrating or updating `models.embedding_backend` (e.g. from deterministic hash to OpenRouter embeddings), run `python -m app.cli.reindex` to purge and re-embed all active document vectors under the updated model fingerprint (D24, D35).

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
