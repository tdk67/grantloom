# GrantLoom — Implementation Plan & Test Strategy

**Document Version:** 1.2  
**Status:** Approved Operational Roadmap  
**Date:** 2026-10-13  
**Companion Documents:** [PRD.md](PRD.md) | [decisions-log.md](decisions-log.md) | [frontend-design.md](frontend-design.md) | [data-strategy.md](data-strategy.md)

---

## 1. Execution Strategy: Two-Phase Delivery

Implementation strictly follows a **two-phase strategy**:

```
Phase 1: Native Local Workstation (Windows 11)
  ├── Python 3.12 Virtual Environment
  ├── Embedded In-Process Storage (ChromaDB + KùzuDB + SQLite)
  ├── BYOK OpenRouter Cloud Inference + Optional Local Ollama Mode
  ├── Thin Vanilla JS Single-Page App (IndexedDB, WCAG AA a11y, Zero Node Build)
  └── Zero Docker Container Dependencies
         │
         ▼ (Passes Phase 1 Definition of Done & Checkpoints)
Phase 2: VPS Deployment & Demo Verification (Linux 8 GB VPS)
  ├── Dockerized Multi-Stage Container (`Dockerfile` + `docker-compose.yml`)
  ├── Integration with Pre-existing Host Prometheus & Grafana
  ├── Nginx Reverse Proxy with TLS, Rate Limiting & SSE Buffering Disabled
  └── Live Demo Rehearsal on Real Solicitations
```

---

## 2. Implementation Progress Ledger

| Milestone | Scope / Deliverable | Status | Checkpoint Verification |
|---|---|---|---|
| **M0** | Foundations, Scaffolding, Strict Config, Ports, RFC 7807 Errors, Alembic Migrations | **COMPLETED** | 100% pass (14 unit tests); `max_documents=50`; Alembic upgrade/downgrade/CLI verified |
| **M1** | Registry, Ingestion Pipeline & Embedded Stores | **COMPLETED** | 100% pass (5 integration tests); Alembic 0001 migration; SHA-256 deduplication verified; ChromaDB indexing verified |
| **M2** | Query Pipeline, BYOK Validation & Citation Resolution | **NEXT** | Pending execution (`pytest tests/integration/test_ask_pipeline.py`) |
| **M3** | Structured Spreadsheet & KùzuDB Graph Integration | **TODO** | Pending |
| **M4** | Guardrails, Bounded Rewrite Loop & Compliance Matrix | **TODO** | Pending |
| **M5** | Synthetic Demo Data Generator (CLI & API) | **IN PROGRESS** | Task 1 completed (CLI & fixtures, 6 unit tests pass); Task 2 (Admin API) pending |
| **M6** | Thin Frontend Implementation (Vanilla JS SPA) | **TODO** | Pending |
| **M7** | Agent Loop Alignment & Synthetic Evaluation Suite | **TODO** | Pending |
| **M8** | VPS Dockerization & Host Monitoring Hook | **TODO** | Pending |
| **M9** | Reverse Proxy, TLS & Master Key Protection | **TODO** | Pending |
| **M10** | End-to-End Demo Rehearsal & Capstone Deliverables | **TODO** | Pending |

---

## 3. Milestone Breakdown with Verifiable Checkpoints

Every milestone contains **Actionable Tasks** and a **Verifiable Checkpoint & Required Evidence**. The code generator must execute the verification commands and output the required evidence before declaring a milestone complete.

---

### Phase 1: Native Windows Development (Zero Docker)

#### Milestone M0: Foundations & Architecture Scaffolding `[COMPLETED]`
* **Actionable Tasks:**
  - [x] 1. Initialize Python 3.12+ virtual environment and `pyproject.toml` with pinned dependencies (`fastapi`, `uvicorn`, `pydantic`, `alembic`, `sqlalchemy`, `chromadb`, `kuzu`, `openpyxl`, `pypdf`, `reportlab`, `httpx`, `prometheus-client`, `slowapi`, `pytest`, `pytest-asyncio`).
  - [x] 2. Implement `app/core/config.py`: typed Pydantic V2 loader parsing `config/config.json` and `.env` with strict validation (no in-code defaults).
  - [x] 3. Create `config/config.json` default schema and `.env.example`.
  - [x] 4. Implement `app/ports/`: abstract interfaces (`VectorStorePort`, `GraphStorePort`, `StructuredStorePort`, `RegistryPort`, `LLMClientPort`, `EmbedderPort`, `DecisionBackendPort`).
  - [x] 5. Implement `app/core/errors.py`: RFC 7807 problem details exception hierarchy with stable error codes.
  - [x] 6. **Database Migration Framework (Liquibase equivalent for Python):**
     * [x] Configure Alembic migration infrastructure (`alembic.ini`, `alembic/env.py`, `alembic/versions/`).
     * [x] Implement `app/db/migrator.py`: programmatic migration runner executing `upgrade head` on application startup and exposing a CLI command (`python -m app.cli.db upgrade head` / `downgrade -1`).
     * [x] Verify database schema changes are strictly versioned via changelog scripts rather than unstructured `CREATE TABLE IF NOT EXISTS` calls.
* **Verifiable Checkpoint & Required Evidence:**
  * **Command:** `pytest tests/unit/test_config.py tests/unit/test_errors.py tests/unit/test_migrations.py`
  * **Evidence:**
    1. Test report showing 100% pass for valid config loading, missing key rejection, and RFC 7807 problem formatting. *(VERIFIED: 14/14 tests pass)*
    2. CLI run proof demonstrating that running `python -c "from app.core.config import get_settings; print(get_settings().limits.max_documents)"` prints `50`. *(VERIFIED: prints 50)*
    3. Migration test asserting `alembic upgrade head` runs cleanly on an empty SQLite database, creates the `alembic_version` tracking table, and allows programmatic status inspection. *(VERIFIED: test_migration_upgrade_and_inspection and CLI pass)*

---

#### Milestone M1: Registry, Ingestion Pipeline & Embedded Stores `[COMPLETED]`
* **Actionable Tasks:**
  - [x] 1. Author versioned Alembic migration `0001_initial_registry_and_outbox.py` defining document registry, outbox queue, chunk metadata, and audit log tables with WAL mode, foreign keys, and indexes.
  - [x] 2. Build SQLite registry and outbox repository in `app/adapters/sqlite_registry.py` operating over the migrated schema with transaction locking.
  - [x] 3. Implement `app/services/ingest_service.py`: streaming SHA-256 computation on upload with instant deduplication (same hash = `200 OK`; in-flight = `202 Accepted`).
  - [x] 4. Implement extractors: PDF via `pypdf` (text layer + `pdfplumber` fallback for tables), TXT, and XLSX via `openpyxl`.
  - [x] 5. Implement page-aware, section-aware chunker producing deterministic chunk IDs (`doc_id:page:index`). Ensure PDF chunks never span pages.
  - [x] 6. Implement `app/adapters/chroma_adapter.py`: in-process ChromaDB persistent client indexing dense vectors with **default ONNX embedder explicitly disabled** (`embedding_function=None`).
  - [x] 7. Implement background worker loop polling SQLite outbox and advancing states (`uploaded → extracting → chunking → embedding → active`).
  - [x] 8. Implement API routes: `POST /api/v1/documents` and `GET /api/v1/documents/{id}` polling endpoint.
* **Verifiable Checkpoint & Required Evidence:**
  * **Command:** `pytest tests/integration/test_ingestion_pipeline.py`
  * **Evidence:**
    1. Upload a multi-page test PDF via test harness; poll `GET /api/v1/documents/{id}` until `state == active`. *(VERIFIED: 3-page PDF processed to active status with chunk count >= 3)*
    2. Upload the exact same file a second time; assert `200 OK` returned immediately with `message: "Document already indexed"` and zero duplicate outbox jobs. *(VERIFIED: duplicate returns 200 OK, outbox job count remains 1)*
    3. Assert ChromaDB collection count equals expected chunk count and SQLite registry status is `active`. *(VERIFIED: ChromaDB count matches registry chunks, deterministic IDs doc_id:page:index verified)*

---

#### Milestone M2: Query Pipeline, BYOK Validation & Citation Resolution
* **Actionable Tasks:**
  1. Implement `app/adapters/openrouter_client.py`: BYOK live key validation against OpenRouter `/api/v1/auth/key` probe (`401` on invalid, `402` on zero credit, `502` on upstream error).
  2. Implement G1 Input Guard: regex injection prefilter, domain check, and language validator.
  3. Implement G2 Strategy Router: dispatches to retrieval strategy (fallback: `hybrid`).
  4. Implement hybrid retrieval: ChromaDB dense vectors + SQLite FTS5 BM25 keyword search fused via Reciprocal Rank Fusion (RRF).
  5. Implement FR-Q8 Abstention: emits *"This information was not found in the indexed documents"* when retrieval score is below threshold.
  6. Implement G4 Output Guard: resolves emitted chunk IDs to physical coordinates (`file_name`, `page`, `line`, `sheet`, `row`).
  7. Implement confidence score calculation (proportion of claims verified against evidence) appended to response metadata.
  8. Implement `POST /api/v1/ask` SSE streaming endpoint emitting canonical events: `guard → route → retrieve → filter → generate → verify → done`.
* **Verifiable Checkpoint & Required Evidence:**
  * **Command:** `pytest tests/integration/test_ask_pipeline.py`
  * **Evidence:**
    1. Mock query test asserting SSE event stream sequence and verifying that cited `chunk_id` markers resolve to genuine file/page coordinates with confidence score.
    2. Test case asserting that query with zero matching chunks returns the exact abstention statement.
    3. Test case with invalid `X-OpenRouter-Key` asserting immediate `401 Unauthorized` RFC 7807 problem response.

---

#### Milestone M3: Structured Spreadsheet & KùzuDB Graph Integration
* **Actionable Tasks:**
  1. Implement XLSX ingestion to typed SQLite tables preserving original row numbers.
  2. Implement `app/verification/sql_guard.py`: AST parser enforcing read-only single `SELECT`, allow-listed tables/columns, timeout ≤ 5s, row limit ≤ 100.
  3. Implement `app/adapters/kuzu_adapter.py`: embedded KùzuDB graph with node types (`Document`, `Requirement`, `Person`, `Project`, `Funder`) and parameterized Cypher templates.
  4. Ensure graph edges capture mandatory `source_chunk_id`.
  5. Add router strategies for `structured` and `graph` queries.
* **Verifiable Checkpoint & Required Evidence:**
  * **Command:** `pytest tests/integration/test_structured_and_graph.py`
  * **Evidence:**
    1. Query test against sample budget spreadsheet ("What is the travel total?") asserting correct SQL generation, AST guard pass, and row coordinate citation.
    2. Malicious SQL test (`SELECT * FROM sqlite_master; DROP TABLE ...`) asserting immediate block by `sql_guard`.
    3. Kùzu query test verifying relationship traversal and source chunk traceability on edges.

---

#### Milestone M4: Guardrails, Bounded Rewrite Loop & Compliance Matrix
* **Actionable Tasks:**
  1. Implement two-tier injection scanner at ingestion (100% regex heuristic + targeted LLM probe on flagged items / 2% sample). Quarantine files exceeding taint threshold.
  2. Implement bounded rewrite loop in answer service (max 2 attempts) with structured failure feedback.
  3. Implement `POST /api/v1/compliance/{solicitation_id}`: extracts mandatory requirements, cross-references lab documents, and classifies status (`Satisfied`, `Partial`, `Gap`).
  4. Implement `GET /api/v1/compliance/{solicitation_id}/export`: generates formatted `.xlsx` export via `openpyxl`.
  5. Implement `GET /api/v1/examples` serving curated starter prompts from `config/example_questions.json`.
* **Verifiable Checkpoint & Required Evidence:**
  * **Command:** `pytest tests/integration/test_compliance_and_guards.py`
  * **Evidence:**
    1. Test uploading adversarial injection file; assert state transitions to `quarantined` and excluded from retrieval.
    2. Compliance test: assert extraction of ≥ 5 requirements, correct classification, and successful generation of downloadable `.xlsx` workbook.

---

#### Milestone M5: Synthetic Demo Data Generator (CLI & Admin Endpoint) `[IN PROGRESS]`
* **Actionable Tasks:**
  - [x] 1. Implement `app/cli/generate_synthetic.py`: standalone CLI utility generating high-fidelity demo grant documents into a local directory:
     * `DOE_Solar_2026_Sample.pdf`: 25-page solicitation containing cost match (20%), equipment threshold ($50,000), and PI eligibility requirements.
     * `Past_Proposal_2024_Sample.pdf`: 15-page past lab proposal citing Dr. Elena Vance's facilities and biosketch.
     * `Lab_Budget_2025_Sample.xlsx`: 3-sheet budget spreadsheet with Personnel, Travel, and Equipment breakdowns.
     *(VERIFIED: 6/6 unit tests pass, files generated in data/synthetic)*
  - [ ] 2. Implement administrative endpoint `POST /api/v1/admin/generate-synthetic-data`: triggers synthetic file generation and automatically ingests them into the SQLite registry and index stores.
* **Verifiable Checkpoint & Required Evidence:**
  * **Command:** `python -m app.cli.generate_synthetic --output-dir data/test_synthetic && pytest tests/unit/test_synthetic_generator.py`
  * **Evidence:**
    1. Verify generated files exist on disk: `DOE_Solar_2026_Sample.pdf`, `Past_Proposal_2024_Sample.pdf`, `Lab_Budget_2025_Sample.xlsx`. *(VERIFIED)*
    2. Assert PDF text layer contains verifiable target keywords and XLSX contains valid numeric cells. *(VERIFIED)*
    3. Endpoint test confirming `POST /api/v1/admin/generate-synthetic-data` indexes all 3 documents to `active` state. *(PENDING: Task 2 endpoint)*

---

#### Milestone M6: Thin Frontend Implementation (Vanilla JS SPA)
* **Actionable Tasks:**
  1. Implement static frontend in `app/static/` matching [frontend-design.md](frontend-design.md): `index.html`, `styles.css`, `app.js`.
  2. **Accessibility & Keyboard Nav:** Implement logical Tab sequence, skip link, focus traps in modals, and ARIA live regions for SSE streams.
  3. **SEO & Crawlers:** Add semantic `<header>`, meta tags, and root static routes for `/robots.txt`, `/sitemap.xml`, and `/llms.txt`.
  4. **IndexedDB Credential Manager:** Store API keys in browser IndexedDB. Implement live validation probe: disable ingestion dropzone and chat input until a valid key is configured.
  5. **Ollama Integration:** Implement local Ollama selector, `/api/tags` model dropdown, short tool-calling diagnostic probe, and dynamic context budget indicator.
  6. **Interactive Citation Drawer with Keyword Highlighting:** Clicking a citation badge slides open the excerpt drawer with search keywords wrapped in `<mark class="kw-highlight">`.
  7. **Confidence Score & Demo Data UI:** Display confidence level badge on query answers; wire up the `🪄 Generate Demo Data` button to the synthetic data endpoint.
  8. Mount and serve `app/static/` at `/` in FastAPI.
* **Verifiable Checkpoint & Required Evidence:**
  * **Command:** Start server with `python -m uvicorn app.main:app --port 8000` and run static file + Playwright automated UI checks.
  * **Evidence:**
    1. `curl -f http://localhost:8000/robots.txt` and `curl -f http://localhost:8000/llms.txt` return valid text content.
    2. Zero console errors on UI load.
    3. UI lockout test: verify upload dropzone is disabled when IndexedDB has no key.
    4. Clicking citation badge slides open drawer with matching keywords highlighted.
    5. Clicking "Generate Demo Data" populates document list with synthetic files.

---

#### Milestone M7: Agent Loop Alignment & Synthetic Evaluation Suite
* **Actionable Tasks:**
  1. Implement explicit multi-step agent reasoning loop (PlannerAgent → RetrieverAgent with discrete tools → WriterAgent → ValidatorAgent) for Capstone Task 8.
  2. Build golden evaluation runner testing 50 benchmark questions across 3 NOFOs with independent judge model family (`qa_judge_model != writer_model`).
  3. Implement metric checks: Citation resolvability = 100%, Faithfulness ≥ 0.95, Abstention accuracy ≥ 0.90, Injection block rate ≥ 0.95.
* **Verifiable Checkpoint & Required Evidence:**
  * **Command:** `python -m app.cli.run_eval --golden-set data/golden_eval.json`
  * **Evidence:**
    1. Printed CLI evaluation report displaying all metrics meeting or exceeding targets:
       * Resolvability: 100%
       * Faithfulness: ≥ 0.95
       * Abstention accuracy: ≥ 0.90
       * Injection block rate: ≥ 0.95
    2. Agent tool execution logs confirming `dense_vector_search`, `bm25_keyword_search`, and `sql_tabular_query` discrete invocations.

---

### Phase 2: VPS Deployment & Demo Verification

#### Milestone M8: VPS Dockerization & Host Monitoring Hook
* **Actionable Tasks:**
  1. Author production multi-stage `Dockerfile` (non-root user, pinned Python 3.12, minimal image size) and `docker-compose.yml`.
  2. Verify that GrantLoom container starts cleanly and exposes port 8000.
  3. Configure Prometheus scraper on host to scrape GrantLoom `/metrics`.
  4. Import GrantLoom dashboard into host's existing Grafana.
  5. Measure total resident set size (RSS) memory under load; assert < 2.5 GB RAM.
* **Verifiable Checkpoint & Required Evidence:**
  * **Command:** `docker compose up -d && curl -f http://localhost:8000/healthz && curl -f http://localhost:8000/metrics`
  * **Evidence:**
    1. `docker ps` showing healthy container.
    2. `/healthz` returns `{"status":"ok"}`.
    3. `/metrics` outputs Prometheus counters for request latencies and router decisions.
    4. `docker stats` output verifying memory usage remains under 2.5 GB RAM.

---

#### Milestone M9: Reverse Proxy, TLS & Master Key Protection
* **Actionable Tasks:**
  1. Configure host Nginx reverse proxy: Let's Encrypt TLS certificate, proxy pass to GrantLoom port 8000.
  2. Set Nginx directives for SSE streaming: `proxy_buffering off;`, `proxy_read_timeout 600s;`, `proxy_set_header X-Accel-Buffering no;`.
  3. Configure Nginx rate limiting (30 requests/minute) with `/api/v1/documents/{id}` polling exemption.
  4. Enforce `X-API-Key` validation and verify sensitive headers are redacted from reverse proxy access logs.
* **Verifiable Checkpoint & Required Evidence:**
  * **Command:** `curl -k https://<vps-domain>/healthz -H "X-API-Key: invalid" -w "%{http_code}"` followed by valid key query.
  * **Evidence:**
    1. Invalid API key returns `401 Unauthorized`.
    2. Valid API key returns `200 OK`.
    3. SSE stream verification over HTTPS confirming tokens stream without proxy buffering delays.

---

#### Milestone M10: End-to-End Demo Rehearsal & Capstone Deliverables
* **Actionable Tasks:**
  1. Execute complete live demo dry-run: click "Generate Demo Data", verify indexed files, run starter questions, inspect confidence scores and keyword-highlighted citations, and download compliance matrix spreadsheet.
  2. Compile capstone documentation package: setup guide, architecture document, **Agent Roles & Tools Specification**, **Limitations and Challenges**, and deployment runbook.
  3. Package submission archive (`grantloom_submission.zip`).
* **Verifiable Checkpoint & Required Evidence:**
  * **Evidence:**
    1. Complete video or log trace of live demo workflow without errors or crashes.
    2. Submission Zip created with all source code, tests, documentation, and configuration examples.

---

## 4. Definition of Done (DoD) Summary Matrix

| Milestone | Status | Checkpoint Requirement | Verification Tool | Pass Threshold |
|---|---|---|---|---|
| **M0** | **Completed** | Pydantic configuration, RFC 7807 errors & Alembic DB migrations | `pytest tests/unit/test_config.py tests/unit/test_migrations.py` | 100% tests pass; missing key fails startup; Alembic applies cleanly |
| **M1** | **Completed** | Ingestion pipeline, Alembic schema & SHA-256 deduplication | `pytest tests/integration/test_ingestion_pipeline.py` | 100% pass; migrated tables verified; 2nd upload is instant no-op (200 OK) |
| **M2** | **Next** | BYOK OpenRouter validation & citation SSE | `pytest tests/integration/test_ask_pipeline.py` | Canonical SSE events emitted; citations resolved with confidence score |
| **M3** | **Todo** | Spreadsheet SQL guard & KùzuDB graph | `pytest tests/integration/test_structured_and_graph.py` | Malicious SQL blocked; row coordinates cited |
| **M4** | **Todo** | Two-tier injection quarantine & compliance | `pytest tests/integration/test_compliance_and_guards.py` | Adversarial doc quarantined; `.xlsx` exported |
| **M5** | **In Progress** | Synthetic demo data creation (CLI & API) | `python -m app.cli.generate_synthetic` | 3 files generated; valid PDF/XLSX structures verified |
| **M6** | **Todo** | Thin Vanilla JS SPA, a11y, SEO, IndexedDB | Browser / Playwright automated walkthrough | 0 console errors; a11y tab sequence; drawer keyword highlight |
| **M7** | **Todo** | Agent loop & Golden eval benchmark | `python -m app.cli.run_eval` | Resolvability 100%, Faithfulness ≥ 0.95 |
| **M8** | **Todo** | Docker container & Prometheus metrics | `docker stats` + `curl /metrics` | RSS memory < 2.5 GB; `/metrics` scraped |
| **M9** | **Todo** | Nginx TLS, SSE unbuffered, rate limit | `curl -i https://<vps-domain>/api/v1/ask` | Unbuffered SSE stream over HTTPS; 401 on bad key |
| **M10** | **Todo** | Live demo rehearsal & Capstone Zip | Demo execution runbook & file inspection | All capstone rubric tasks verified; Zip ready |
