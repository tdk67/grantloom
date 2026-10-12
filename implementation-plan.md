# GrantLoom — Implementation Plan & Test Strategy

**Date:** 2026-10-12  
**Status:** Approved  
**Companion Documents:** [PRD.md](file:///c:/Data/work/grantloom/PRD.md) | [decisions-log.md](file:///c:/Data/work/grantloom/decisions-log.md)

---

## 1. Execution Strategy: Two-Step Rollout

To ensure rapid iteration and eliminate container debugging during core algorithmic development, implementation follows a strict **two-step strategy**:

```
Step 1: Native Local Execution (Windows 11)
  ├── Python 3.12 Virtual Environment
  ├── Embedded In-Process Storage (ChromaDB + KùzuDB + SQLite)
  ├── BYOK OpenRouter Cloud Inference
  └── Zero Docker Container Dependencies
         │
         ▼ (Passes Step 1 Definition of Done)
Step 2: VPS Deployment & Demo Verification
  ├── Linux 2-vCPU / 8 GB RAM VPS Host
  ├── Systemd Service or Lightweight Single Docker Container
  ├── Integration with Pre-existing Host Prometheus & Grafana
  └── Nginx Reverse Proxy with TLS & Rate Limiting
```

---

## 2. Milestone Breakdown

### Phase 1: Step 1 — Local Windows Development (Zero Docker)

#### Milestone M0: Foundations & Architecture Scaffolding
- **Objectives:** Establish project workspace, packaging, typed configuration, and abstract ports.
- **Deliverables:**
  - `pyproject.toml` with pinned Python 3.12 dependencies (`fastapi`, `uvicorn`, `pydantic`, `chromadb`, `kuzu`, `openpyxl`, `pypdf`, `httpx`, `prometheus-client`).
  - `app/core/config.py`: Pydantic V2 settings loader reading `config/config.json` and `.env`.
  - `app/ports/`: Complete abstract interface definitions for `VectorStorePort`, `GraphStorePort`, `StructuredStorePort`, `RegistryPort`, `LLMClientPort`, `EmbedderPort`, `DecisionBackendPort`.
  - Base RFC 7807 problem details exception hierarchy in `app/core/errors.py`.
- **Exit Criteria:** Virtualenv runs clean; `pytest` runs skeleton tests; configuration validates strictly on invalid inputs.

#### Milestone M1: Registry, Ingestion Pipeline & Embedded Stores
- **Objectives:** Build document lifecycle, SHA-256 deduplication, async polling, and embedded indexing.
- **Deliverables:**
  - SQLite document registry and outbox tables with WAL mode.
  - Streaming SHA-256 computation on upload with instant deduplication check.
  - Extraction adapters for PDF (`pypdf`/`pdfplumber`), TXT, and XLSX (`openpyxl`).
  - Page-aware, section-aware chunker producing deterministic chunk IDs.
  - `ChromaAdapter`: In-process ChromaDB persistent client indexing dense vectors.
  - `SqliteRegistryAdapter`: Background worker consumer polling outbox and tracking state transitions (`uploaded → validating → extracting → chunking → embedding → indexing → active`).
  - `GET /api/v1/documents/{id}` polling endpoint returning stage, percentage, and page counts.
- **Exit Criteria:** Uploading a 50-page PDF processes asynchronously to `active`; polling updates live; re-uploading the identical file returns instant `200 OK` (idempotent no-op).

#### Milestone M2: Query Pipeline, BYOK Validation & Citation Resolution
- **Objectives:** Deliver interactive question answering with verified citations and SSE streaming.
- **Deliverables:**
  - `OpenRouterClientAdapter` handling live `X-OpenRouter-Key` validation and token generation.
  - G1 Input Guard: Domain check and preliminary prompt-injection filter.
  - Hybrid retrieval: Dense vectors (ChromaDB) + BM25 keyword search (SQLite FTS5) fused via Reciprocal Rank Fusion (RRF).
  - System prompt builder with strict untrusted context boundaries.
  - G4 Output Guard: Resolves cited chunk IDs to physical coordinates (`file_name`, `page`, `line`, `sheet`, `row`).
  - SSE streaming endpoint `POST /api/v1/ask` emitting status events followed by text and citation badges.
- **Exit Criteria:** Single-hop query streams verified answer with clickable citation badges within 15 seconds; invalid OpenRouter keys fail immediately with `401 Unauthorized`.

#### Milestone M3: Structured Spreadsheet & KùzuDB Graph Integration
- **Objectives:** Enable spreadsheet SQL queries and relational graph queries.
- **Deliverables:**
  - XLSX sheet ingestion to typed SQLite tables with preserved original row numbers.
  - `sql_guard`: Read-only AST validation enforcing single `SELECT`, allow-listed tables/columns, timeout, and max 100 row limits.
  - `KuzuAdapter`: Embedded KùzuDB graph initialization with node types (`Document`, `Requirement`, `Person`, `Project`, `Funder`).
  - Entity/relation extraction during ingestion producing edges with mandatory `source_chunk_id`.
  - Parameterized Cypher templates for relationship traversal.
- **Exit Criteria:** User can query a grant budget spreadsheet ("What is the total travel budget?") and receive the verified SQLite row citation; relation query retrieves relevant project/PI links via Kùzu.

#### Milestone M4: Guardrails, Bounded Rewrite Loop & Starter Prompts
- **Objectives:** Implement two-tier injection scanning, rewrite feedback loop, and demo example questions.
- **Deliverables:**
  - Two-tier injection scanner at ingestion (100% regex heuristic + targeted LLM probe on flagged items / 2% sample).
  - Bounded rewrite loop in answer service (max 2 attempts) passing structured claim failure reasons to the writer.
  - Graceful degradation: Warning badge emitted if a claim cannot be verified after 2 rewrite attempts.
  - Metadata endpoint `GET /api/v1/examples` serving curated questions from `config/example_questions.json`.
- **Exit Criteria:** Ingestion flags adversarial test document into quarantine; ungrounded claim triggers rewrite and self-corrects or emits warning badge.

#### Milestone M5: Testing, Eval Harness & Synthetic Data Tooling
- **Objectives:** Validate performance, citation fidelity, and regression gates.
- **Deliverables:**
  - `app/cli/generate_synthetic.py`: CLI tool generating test solicitations and lab proposals locally on demand.
  - Golden evaluation runner testing 50 benchmark questions across 3 NOFOs.
  - Automated evaluation metrics: Citation validity rate, Faithfulness, Retrieval recall.
- **Exit Criteria:** Golden eval achieves ≥ 0.98 citation validity and ≥ 0.90 faithfulness.

---

### Phase 2: Step 2 — VPS Deployment & Demo Verification

#### Milestone M6: VPS Service Packaging & Host Monitoring Hook
- **Objectives:** Deploy onto the 8 GB Linux VPS and connect to existing Prometheus/Grafana.
- **Deliverables:**
  - Systemd service definition / lightweight multi-stage Dockerfile.
  - Configuration of FastAPI Prometheus instrumentator at `/metrics`.
  - Configuration of host's existing Prometheus scrape job to scrape GrantLoom port.
  - Import pre-built GrantLoom dashboard into host's existing Grafana.
- **Exit Criteria:** GrantLoom starts cleanly on VPS; Prometheus scrapes metrics; Grafana displays live query latencies, router decisions, and memory consumption (< 2.5 GB RAM).

#### Milestone M7: Reverse Proxy, TLS & Master Key Protection
- **Objectives:** Secure the public VPS demo endpoint.
- **Deliverables:**
  - Nginx configuration: TLS certificate (Let's Encrypt / Certbot), static frontend hosting, proxy pass to FastAPI.
  - Master key middleware enforcing `X-API-Key` on all API endpoints.
  - Rate limiting rules in Nginx and FastAPI middleware (30 requests/minute).
- **Exit Criteria:** Unauthenticated public requests rejected; authenticated requests succeed over HTTPS.

#### Milestone M8: End-to-End Demo Rehearsal & Delivery
- **Objectives:** Final dry-run of demo workflows on live VPS.
- **Deliverables:**
  - Live demo script verifying NOFO upload, status polling, example questions, and compliance matrix generation.
  - User documentation: Setup guide, BYOK instructions, and system boundaries.
- **Exit Criteria:** Flawless demo run executed within latency benchmarks without host OOM crashes.

---

## 3. Comprehensive Test Plan

Testing follows the pyramid structure with explicit quality gates:

```
          [ Golden Eval Suite ]  -> 50-100 real/synthetic NOFO questions (CI Gate)
       [ Integration Test Suite ] -> API routes, Async Polling, SSE streaming
    [ Component / Adapter Tests ]  -> ChromaDB, KùzuDB, SQLite Guard, Ingestion
 [ Unit Tests: Business Logic ]    -> Chunker, Guards, Citation Resolver, Pydantic Config
```

### 3.1 Unit & Component Tests (`tests/unit`)
1. **Config Validation:** Verify strict failure when limits or models are missing or corrupted in `config.json`.
2. **Chunking Fidelity:** Verify PDF chunks never cross page boundaries; verify line numbers for TXT and sheet/row coordinates for XLSX.
3. **AST SQL Guard:** Verify destruction attempts (`DROP`, `DELETE`, `UPDATE`, stacked queries, function execution) are blocked immediately.
4. **Citation Resolver:** Verify that valid chunk IDs resolve to correct file/page/row metadata, and non-existent IDs raise validation errors.
5. **OpenRouter BYOK Auth:** Mock OpenRouter responses to verify 401/402 live key failure handling.

### 3.2 Integration & Pipeline Tests (`tests/integration`)
1. **Async Ingestion & Polling:** Upload test PDF; poll `GET /api/v1/documents/{id}` until `state == active`; assert chunks in ChromaDB and nodes in KùzuDB.
2. **Idempotent Re-upload:** Post identical document twice; verify immediate `200 OK` on second call without duplicate job in outbox.
3. **SSE Streaming Pipeline:** Post `/api/v1/ask` with mock OpenRouter stream; verify status event order (`guard → route → retrieve → generate → verify → done`).
4. **Rewrite Self-Healing:** Inject hallucinated chunk ID into mock writer output; verify rewrite loop fires and corrects the citation.

### 3.3 Golden Evaluation & Security Suite (`tests/eval`)
1. **Citation Grounding:** ≥ 98% of claims in golden answers cite authentic chunk IDs with matching textual evidence.
2. **Faithfulness:** ≥ 90% faithfulness on ground-truth answer benchmarks.
3. **Adversarial / Injection Defense:**
   - 20 direct prompt injection queries (assert G1 blocks 100%).
   - 10 indirect injection documents (embedded instructions in PDFs; assert L4/quarantine flags them).

---

## 4. Definition of Done (DoD)

### 4.1 Definition of Done for Milestones
A milestone is considered **Done** only when:
- [ ] All code is written in Python 3.12 adhering to layered architecture (`api -> service -> repository/adapter`).
- [ ] No hardcoded configuration values or magic constants exist in application code.
- [ ] Abstract ports are implemented via concrete adapters with zero leakage of store-specific logic into services.
- [ ] All new functions and modules have type annotations and pass `ruff` / `mypy` static analysis.
- [ ] Unit and integration test coverage for the milestone exceeds 85%.
- [ ] All errors return structured RFC 7807 problem details with specific error codes.

### 4.2 Definition of Done for v1 Release
The GrantLoom v1 release is **Done** when:
- [ ] **Local Windows Step 1:** End-to-end ingestion and querying runs on Windows 11 with zero Docker dependencies.
- [ ] **VPS Step 2:** System runs stably on 8 GB VPS consuming < 2.5 GB RAM.
- [ ] **BYOK Security:** OpenRouter keys are validated live, passed in headers, never persisted to disk, and never logged.
- [ ] **Observability:** Host Prometheus scrapes GrantLoom `/metrics`; Grafana dashboard displays operational metrics.
- [ ] **Citation Fidelity:** 100% of answer citations resolve to genuine document coordinates in the test corpus.
- [ ] **Idempotency:** Re-uploading existing documents incurs zero reprocessing.
- [ ] **Documentation:** Setup instructions, API documentation, and architecture diagrams are complete.
