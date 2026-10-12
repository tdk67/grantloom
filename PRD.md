# GrantLoom — Product Requirements Document (PRD) v0.2

**Date:** 2026-10-12  
**Status:** Approved Architectural Baseline.  
**Target Environment:** Python 3.12 (Native on Windows for Step 1; Dockerized on 8 GB VPS for Step 2).  
**Inputs:** `idea.md`, `market-research.md`, `data-strategy.md`, `project-review.md`, `project-requirements.txt`, and user design decisions of 2026-10-12.

---

## 1. Purpose and Positioning

GrantLoom is a **backend-first, self-hosted RAG service** designed for research labs and academic teams. A lab uploads funding solicitations (NOFOs / RFPs), past successful grant proposals, researcher CVs, institutional policies, and budget spreadsheets. It queries this corpus to receive:
1. **Answers with verifiable citations** (exact file, page, or spreadsheet sheet/row coordinates).
2. **Compliance & gap matrices** comparing solicitations against the lab's evidence.

### Core Architectural Pillars
- **Zero-Docker Embedded Execution for Local Dev (Step 1):** Native Python 3.12 on Windows using embedded persistent stores (ChromaDB for dense vectors, KùzuDB for graph relationships, and SQLite for registry, structured data, and BM25 keyword search).
- **Lightweight VPS Footprint (Step 2):** Runs within a 2-vCPU / 8 GB VPS demo environment (< 2.5 GB RAM footprint), hooking directly into the host's **pre-existing Prometheus and Grafana** instances.
- **Bring Your Own Key (BYOK) for OpenRouter:** Users supply their own OpenRouter API key via HTTP headers. No backend LLM wallet or server token liability; keys are validated live, used ephemerally, and never persisted or logged.
- **Citation Validity by Construction:** The LLM never invents filenames or page numbers; it cites chunk IDs that the backend resolves and verifies against the document registry.
- **Hexagonal, Layered, Clean-Code Engineering:** Strict separation of concerns (`api -> service -> repository/adapter`), pluggable ports, zero hardcoded limits, fail-fast mechanics, and structured RFC 7807 errors.

---

## 2. Decisions Log & Resolved Conflicts

### 2.1 Decisions Log

| ID | Decision | Details / Rationale |
|----|----------|---------------------|
| D1 | **Backend Stack** | Python 3.12 + FastAPI (async). Native Windows support without Docker for Step 1. |
| D2 | **Vector & Keyword Store** | **ChromaDB** (embedded persistent mode) for dense vectors + **SQLite FTS5** for BM25 keyword retrieval, fused via Reciprocal Rank Fusion (RRF). Pluggable via `VectorStorePort`. |
| D3 | **Graph Store** | **KùzuDB** (`kuzu` embedded in-process graph database). Replaces heavy Neo4j; standard Cypher querying, < 100 MB RAM, zero external containers. Pluggable via `GraphStorePort`. |
| D4 | **Structured Store** | Excel and CSV loaded into **SQLite**; LLM writes read-only SQL validated by an AST SQL guard. |
| D5 | **Source of Truth** | **Document registry** (SQLite with content hash, version, and state machine) + **outbox**; all vector, graph, and SQL indexes are derived and rebuildable from original files. |
| D6 | **Access Control** | Single-user, no user registration/login screens. Server protected by static master key (`X-API-Key`) defined in `.env`. UI passes this header. |
| D7 | **LLM & Inference Model** | **Bring Your Own Key (BYOK)** via OpenRouter. Keys passed in `X-OpenRouter-Key` request header. Validated live before use. Never stored on disk or logged. Client UI keeps it in browser storage (IndexedDB/localStorage). |
| D8 | **Private Mode** | Config-driven switch (`llm.private_mode: false`). When enabled for testing on Windows, routes to local Ollama. Disabled on the demo VPS. |
| D9 | **Decision Layer** | Julia-1 container dropped to conserve RAM. Pluggable `DecisionBackendPort` uses Cloud prompts (or lightweight rule heuristics) for G1–G4 guardrails. |
| D10 | **Monitoring & Metrics** | FastAPI exposes `/metrics` for Prometheus scrape. GrantLoom **reuses existing host Prometheus and Grafana**; no duplicate containers spawned. |
| D11 | **Async Ingestion & Idempotency** | Ingestion runs asynchronously with status polling. Content-hash (SHA-256) deduplication guarantees that re-uploading an identical document is an instantaneous no-op. |
| D12 | **Output QA Loop** | Bounded rewrite loop (max 2 iterations). Structured failure feedback sent to writer; unresolvable claims degrade gracefully with explicit UI warnings. |
| D13 | **Zero Hardcoding** | All limits (file size, doc counts, page caps, timeouts, thresholds) loaded from `config.json`. Zero hardcoded defaults in code. |
| D14 | **Starter Questions** | `GET /api/v1/examples` serves curated domain prompts loaded from `config/example_questions.json` for immediate demo exploration. |
| D15 | **Synthetic Data Tooling** | Synthetic test proposals/NOFOs are **not** checked into Git. Provided via an on-demand generator script (`python -m app.cli.generate_synthetic_docs`). |
| D16 | **Engineering Principles** | Layered (`api -> service -> adapter`), Hexagonal ports, Fail-fast with RFC 7807, intelligent retries only (transient 429/503; no 4xx retries), zero dead code. |

### 2.2 Resolved Architectural Conflicts

| ID | Conflict | Resolution |
|----|----------|------------|
| **C1** | Full stack memory (> 6 GB) vs 8 GB host with existing services | **Resolved:** Replaced Neo4j with in-process KùzuDB (~100 MB). Replaced standalone Qdrant container with embedded ChromaDB. Dropped Julia-1 container. Hook into existing Prometheus/Grafana. Total GrantLoom memory drops to **< 2.5 GB RAM**. |
| **C2** | Private mode on 2-vCPU VPS | **Resolved:** Private mode is a configuration toggle (`llm.private_mode: false`). Demo runs in cloud BYOK mode. Local Ollama testing supported only on developer's local machine. |
| **C3** | Public demo VPS vs "No Login" | **Resolved:** Protected by `X-API-Key` server secret header + reverse proxy TLS/rate limiting. No login UI required. |
| **C4** | Neo4j JVM memory footprint | **Resolved:** Dropped Neo4j completely. Adopted **KùzuDB** behind `GraphStorePort`. Kùzu runs embedded in Python 3.12, parses Cypher natively, and requires no background daemon. |

---

## 3. Strict Engineering & Quality Principles

The implementation must unconditionally adhere to the following architectural rules:

### 3.1 Hexagonal (Ports & Adapters) Architecture
Business logic (`services/`) must depend only on abstract interfaces (`ports/`), never concrete third-party SDKs:
- `VectorStorePort` (implemented by `ChromaAdapter`)
- `GraphStorePort` (implemented by `KuzuAdapter`)
- `StructuredStorePort` (implemented by `SqliteStructuredAdapter`)
- `RegistryPort` (implemented by `SqliteRegistryAdapter`)
- `LLMClientPort` (implemented by `OpenRouterClientAdapter`)
- `EmbedderPort` (implemented by `OpenRouterEmbedderAdapter` / optional `OllamaEmbedderAdapter`)
- `DecisionBackendPort` (implemented by `PromptJudgeAdapter`)

### 3.2 Layered Responsibility Boundaries
```
API Layer (Routers, DTOs, HTTP status, Headers)
   └── Service Layer (Orchestration, Guards, Rewrite Loops, Business Rules)
         └── Domain / Ports (Pure business models, interfaces)
               └── Adapters / Repositories (SQL, Chroma, Kùzu, OpenRouter API calls)
```
- **Rule:** API endpoints must never execute SQL queries, Cypher statements, or low-level store calls directly.
- **Rule:** Services must remain framework-agnostic (no FastAPI `Request` objects passed into service methods).

### 3.3 Zero Hardcoding & Configuration Separation
- **Secrets (`.env`):** Restricted solely to private keys and credentials (e.g. `GRANTLOOM_SERVER_API_KEY`). A sanitized `.env.example` must be maintained.
- **Config (`config/config.json`):** All operational settings, model designations, token budgets, thresholds, rate limits, timeouts, and quota caps (e.g. `max_documents`, `max_file_size_mb`).
- **Local Overrides (`config/config.local.json`):** Git-ignored file for machine-specific overrides.
- **Prompts & Templates:** All system prompts, guard questions, refusal responses, and report templates live in versioned files under `prompts/` and `templates/`.

### 3.4 Fail-Fast & Explicit Error Handling
- **No Error Swallowing:** Never use generic `except Exception: pass` or mock fallbacks that conceal real system failures.
- **RFC 7807 Problem Details:** All API errors return a standard JSON structure with `type`, `title`, `status`, `detail`, and `instance`.
- **Intelligent Retries Only:** Retries with exponential backoff and jitter are permitted **only** for transient upstream issues (HTTP 429 Rate Limit, HTTP 502/503/504). **Never retry** client errors (HTTP 400, 401, 403, 404, 422) or unrecoverable validation faults.

### 3.5 Clean Code, Single Responsibility (SRP) & DRY
- **File Size Ceiling:** Modules must target 200–500 lines. Files exceeding 1,000 lines are prohibited.
- **Dead Code Elimination:** Zero orphaned helper functions, unused imports, obsolete variables, or commented-out blocks.

---

## 4. System Architecture

### 4.1 Step 1 vs Step 2 Target Architecture

```mermaid
flowchart TD
  subgraph Client [Client / Browser UI]
    UI["Single-Page App (Vanilla JS)"]
    LS[("Browser Storage\nStores OpenRouter Key & Server Key")]
  end

  subgraph Host [Host System (Windows Dev or Linux VPS)]
    NG["Reverse Proxy / Nginx\nTLS, Rate Limiting, Static UI"]
    
    subgraph GrantLoom [GrantLoom Python 3.12 Backend]
      API["FastAPI App (API Process)\nRouters: ask, docs, examples, health"]
      SVC["Service Orchestrator\nGuards G1-G4, Router, Citations"]
      WRK["Ingestion Worker Process\nExtract, Chunk, Scan, Index"]
      
      subgraph EmbeddedStores [Embedded File Stores (Zero Docker)]
        REG[("SQLite DB\nRegistry, Outbox, Structured Tables, FTS5")]
        VEC[("ChromaDB\nPersistent Dense Vectors")]
        GRP[("KùzuDB\nPersistent Graph (Cypher)")]
      end
    end

    subgraph HostMonitoring [Existing Host Monitoring]
      PROM["Prometheus (Already running on host)"]
      GRAF["Grafana (Already running on host)"]
    end
  end

  subgraph Cloud [External Cloud APIs]
    OR["OpenRouter API\n(User's BYOK Account)"]
  end

  UI -->|"X-API-Key\nX-OpenRouter-Key"| NG
  NG --> API
  API --> SVC
  SVC --> REG
  SVC --> VEC
  SVC --> GRP
  SVC -->|"BYOK Header"| OR
  API -->|"Enqueue Job"| REG
  WRK -->|"Poll Outbox"| REG
  WRK --> VEC
  WRK --> GRP
  WRK -->|"BYOK Ingestion Scan"| OR
  PROM -->|"Scrape /metrics"| API
  GRAF --> PROM
```

### 4.2 Two-Process Concurrency Model
1. **API Process (`app.main:app`):** Handles incoming client HTTP/SSE requests. Performs read-only queries against SQLite, ChromaDB, and KùzuDB (`read_only=True`). Enqueues ingestion jobs to the SQLite `outbox` table.
2. **Worker Process (`app.worker:run_worker`):** Polls the SQLite `outbox` table. Executes CPU-intensive extraction, chunking, and write-indexing to ChromaDB and KùzuDB. Holds write locks during transactions.

### 4.3 Memory Budget (Verified for 8 GB Host)

| Component | Nature | Memory Footprint | Note |
|-----------|--------|------------------|------|
| GrantLoom API (FastAPI) | Python 3.12 process | ~250–350 MB | Handles async requests & SSE |
| GrantLoom Ingestion Worker | Python 3.12 process | ~400–700 MB | Peaks during multi-page PDF parsing |
| ChromaDB | Embedded in-process | ~150–250 MB | HNSW index on disk |
| KùzuDB | Embedded in-process | ~80–150 MB | In-process columnar graph engine |
| SQLite (Registry + FTS5) | Embedded in-process | ~50–100 MB | WAL mode, shared cache |
| Host Prometheus + Grafana | Existing host services | 0 MB (reused) | Reused from host; no new containers |
| **Total GrantLoom Footprint** | | **~1.0–1.6 GB RAM** | **Comfortably fits 8 GB host with > 5 GB headroom** |

### 4.4 Module Layout
```
app/
  api/                    # HTTP layer (FastAPI routers, request models, problem details)
    routers/              # ask, documents, examples, admin, health
    middleware/           # api_key_auth, rate_limiter, openrouter_key_validator
  ports/                  # Hexagonal abstract interfaces
    vector_store.py       # VectorStorePort
    graph_store.py        # GraphStorePort
    structured_store.py   # StructuredStorePort
    registry.py           # RegistryPort
    llm_client.py         # LLMClientPort
    embedder.py           # EmbedderPort
    decision_backend.py   # DecisionBackendPort
  adapters/               # Concrete implementations of ports
    chroma_adapter.py     # ChromaDB implementation
    kuzu_adapter.py       # KùzuDB implementation
    sqlite_registry.py    # SQLite registry & outbox
    sqlite_structured.py  # SQLite tables & FTS5 search
    openrouter_client.py  # OpenRouter HTTP client with BYOK
    openrouter_embed.py   # OpenRouter embedding client
    prompt_judge.py       # LLM-based decision judge
  services/               # Business domain & orchestration
    document_service.py   # Upload, hashing, status polling, deletion
    ingest_service.py     # Extraction, sanitization, chunking, scanning
    retrieval_service.py  # Hybrid (dense+BM25), graph, SQL, RRF fusion
    answer_service.py     # G1-G4 pipeline, SSE streaming, rewrite loop
    citation_service.py   # Chunk ID resolution and ground-truth verification
  core/                   # Shared plumbing
    config.py             # Typed Pydantic configuration loader
    errors.py             # Custom domain exceptions
    logging.py            # Structured JSON logger (anonymized)
  cli/                    # Standalone CLI tools
    generate_synthetic.py # On-demand test data generator
prompts/                  # External versioned prompt templates (Markdown/YAML)
templates/                # Refusal templates, report layouts
config/                   # config.json, config.example.json, example_questions.json
```

---

## 5. Document Lifecycle & Ingestion Pipeline

### 5.1 Asynchronous Lifecycle & State Machine
Every uploaded document transitions through an audited state sequence recorded in SQLite:
`uploaded → validating → scanning → extracting → sanitizing → chunking → embedding → indexing → verifying → active`
- **Error States:** `rejected` (validation fail), `quarantined` (tainted/injection suspect), `failed_retryable`.
- **Concurrency & Resumability:** The worker advances the state only after the respective step is durable. If the worker restarts, uncompleted jobs in `outbox` resume from their last verified milestone.

### 5.2 Strict Ingestion Idempotency
To prevent double-processing and wasted tokens:
1. **Streaming SHA-256:** On `POST /documents`, the API computes the document's SHA-256 hash in 64 KB chunks without loading the entire file into memory.
2. **Registry Hash Lookup:**
   - **Match Found (`state = active`):** Instant `200 OK`. Returns existing `doc_id`, metadata, and `message: "Document already indexed"`. Zero background work enqueued.
   - **Match Found (`state = processing`):** Instant `202 Accepted`. Returns existing `doc_id` and polling URL.
   - **Identical Filename + New Hash:** Generates a new version entry (`version: 2`) under the document record.
   - **New Hash:** Enqueues an ingestion task into the `outbox` table and returns `202 Accepted` with a new `doc_id`.

### 5.3 Asynchronous Status Polling
Clients track ingestion by polling `GET /documents/{doc_id}`:
```json
{
  "doc_id": "doc_9f83a1b",
  "filename": "NIH_R01_Solicitation.pdf",
  "state": "indexing",
  "progress_pct": 82,
  "pages_processed": 45,
  "total_pages": 55,
  "created_at": "2026-10-12T19:40:00Z",
  "error": null
}
```

### 5.4 Bring Your Own Key (BYOK) Propagation
1. The client provides `X-OpenRouter-Key` in the upload request header.
2. The key is validated live against OpenRouter's auth endpoint.
3. The key is passed into the worker's transient in-memory job context.
4. **Security Invariant:** The OpenRouter key is **never written to the SQLite database**, never saved to disk, and never output in logs or traces.

### 5.5 Ingestion Hardening Layers
- **L1 Basic Limits:** Validate magic bytes, extension, file size (`limits.max_file_size_mb`), page count (`limits.max_pages_per_file`). Reject password protection or decompression bombs.
- **L2 Sandboxed Extraction:** `pypdf` / `pdfplumber` / `openpyxl` extraction executed with memory and CPU execution timeouts.
- **L3 Hidden Content Scan:** Strip zero-width Unicode characters, off-page text coordinates, and microscopic fonts.
- **L4 Two-Tier Injection Scan:**
  - *Tier 1 (Heuristic):* Fast regex evaluation for adversarial instructions ("ignore previous instructions", "system prompt override").
  - *Tier 2 (Targeted LLM check):* LLM injection check triggers **only** if Tier 1 scores above threshold or on a configurable random sample (e.g. 2%). Chunk verdicts cached by content hash.
- **L5 Quarantine & Human Review:** Documents exceeding the taint threshold enter `quarantined` state. Excluded from retrieval until explicitly released via `POST /documents/{id}/release`.

---

## 6. Question Answering & Retrieval Pipeline

```
User Query + X-OpenRouter-Key
    │
    ▼
[Middleware: Live Key Validation & Rate Limit Check]
    │
    ▼
[G1 Input Guard: Domain Relevance & Prompt Injection Scan]
    │── (Refusal / Malicious) ──► Strict Polite Refusal Template (No Retrieval)
    ▼ (Pass)
[G2 Router: Strategy Selection] ──► {hybrid, graph, structured, composite}
    │
    ▼
[Multi-Store Retrieval]
    ├── Dense Vectors: ChromaDB
    ├── Keyword BM25: SQLite FTS5 (Fused via RRF)
    ├── Graph Relations: KùzuDB (Parameterized Cypher Templates)
    └── Tabular Data: Read-Only SQLite (Validated via SQL Guard)
    │
    ▼
[G3 Reranker & Context Filter] ──► Top K chunks; drop injection-suspect passages
    │
    ▼
[Writer LLM Generation (OpenRouter BYOK)]
    │── System Prompt: Untrusted Context Blocks; Must Cite Chunk IDs
    ▼
[G4 Output Guard: Citation Resolution & Claim Support Verification]
    │── (Passed ≥ 0.98 Claims) ──► Stream SSE Answer + Resolved Source Badges
    └── (Failed Claims) ──► Rewrite Loop (Max 2 Attempts) ──► Graceful Warning
```

### 6.1 G1–G4 Guardrails
- **G1 Input Guard:** Rejects prompt injections and out-of-domain queries (e.g. general coding, creative writing) before consuming retrieval tokens.
- **G2 Router:** Classifies query requirements. Chooses between dense+sparse hybrid, graph relationships, or SQL queries. Low-confidence queries default to hybrid.
- **G3 Reranker:** Ranks candidate chunks and drops irrelevant or flagged context.
- **G4 Output Guard:**
  - Resolves emitted `chunk_id` markers to physical file coordinates (`filename`, `page`, `sheet`, `row`).
  - Verifies that cited text spans genuinely exist in the registry.
  - Verifies claim support. If ungrounded claims are detected, triggers a structured rewrite loop.

### 6.2 Graph Retrieval with KùzuDB
- In v1, the model **never writes free-form Cypher**.
- Queries select from vetted parameterized templates:
  - `find_requirements_for_topic(topic_keyword)`
  - `find_investigators_by_expertise(skill)`
  - `find_past_proposals_by_funder(funder_name)`
- Parameter slots are sanitized against known entity names.

### 6.3 Structured Spreadsheet Retrieval
- Excel and CSV sheets stored as SQLite tables.
- LLM generates a single `SELECT` query.
- Evaluated by `sql_guard`: verified against allow-listed table/column names, read-only mode, strict execution timeout, row limit (max 100 rows).

---

## 7. Configuration & Quota Limits

All system capacities, thresholds, and operational flags reside in `config/config.json`. **No hardcoded values exist in application source code.**

### 7.1 Configuration Schema (`config/config.json`)
```json
{
  "limits": {
    "max_documents": 50,
    "max_file_size_mb": 25,
    "max_pages_per_file": 100,
    "max_query_length_chars": 1000,
    "max_concurrent_ingest_jobs": 2
  },
  "models": {
    "writer_model": "anthropic/claude-3.5-sonnet",
    "qa_judge_model": "openai/gpt-4o-mini",
    "router_model": "openai/gpt-4o-mini",
    "embedding_model": "text-embedding-3-small"
  },
  "llm": {
    "provider": "openrouter",
    "private_mode": false,
    "local_endpoint": "http://localhost:11434/v1",
    "request_timeout_seconds": 30,
    "max_retries": 3,
    "retry_backoff_factor": 1.5
  },
  "guardrails": {
    "max_rewrite_attempts": 2,
    "injection_regex_threshold": 0.7,
    "injection_sample_rate": 0.02,
    "quarantine_retention_days": 14
  },
  "retrieval": {
    "dense_top_k": 20,
    "sparse_top_k": 20,
    "rerank_top_k": 5,
    "rrf_k": 60,
    "router_min_confidence": 0.65
  },
  "logging": {
    "log_full_prompts": false,
    "log_level": "INFO"
  }
}
```

---

## 8. API Contract & Endpoints

All endpoints require the server access header `X-API-Key: <server_key>`. LLM-interacting endpoints require `X-OpenRouter-Key: <user_key>`. Errors adhere to RFC 7807 problem details.

| Method | Endpoint | Headers Required | Description |
|--------|----------|------------------|-------------|
| `POST` | `/api/v1/documents` | `X-API-Key`, `X-OpenRouter-Key` | Multipart upload. Computes SHA-256. Returns `doc_id`, status (`active` or `processing`). |
| `GET` | `/api/v1/documents` | `X-API-Key` | List indexed documents, page counts, versions, and states. |
| `GET` | `/api/v1/documents/{id}` | `X-API-Key` | Status polling endpoint: returns stage progress and error details. |
| `DELETE` | `/api/v1/documents/{id}` | `X-API-Key` | Deletes document and cascades removal from ChromaDB, KùzuDB, and SQLite. |
| `POST` | `/api/v1/documents/{id}/release` | `X-API-Key` | Releases a quarantined document for indexing. |
| `GET` | `/api/v1/documents/{id}/preview` | `X-API-Key` | Fetch citation source excerpt (page or row). |
| `POST` | `/api/v1/ask` | `X-API-Key`, `X-OpenRouter-Key` | SSE endpoint streaming status events, generated text, and citation badges. |
| `POST` | `/api/v1/compliance/{solicitation_id}` | `X-API-Key`, `X-OpenRouter-Key` | Generates requirement compliance/gap matrix. |
| `GET` | `/api/v1/examples` | `X-API-Key` | Serves starter demo questions from `config/example_questions.json`. |
| `GET` | `/metrics` | None (or basic auth) | Exposes Prometheus metrics for the host's existing Prometheus scraper. |
| `GET` | `/healthz` | None | Liveness probe. |
| `GET` | `/readyz` | None | Readiness probe (verifies database connections). |

---

## 9. Resolution of Design Decisions (Section 14)

| # | Decision Item | Final Resolution |
|---|---------------|------------------|
| **O1** | Target Host & Deployment | Target **8 GB host**, running GrantLoom stack in < 2.5 GB RAM. Existing host Prometheus/Grafana reused. |
| **O2** | Private Mode Implementation | Architectural config toggle (`llm.private_mode: false`). Demo runs cloud BYOK. Local Ollama testing supported on Windows. |
| **O3** | Ingestion Injection Scanning | Two-tier hybrid: 100% heuristic regex prefilter + LLM check on flagged items and 2% random sample. Cached by chunk hash. |
| **O4** | Graph Entity Schema Depth | Minimal schema for v1: `Document`, `Requirement`, `Person`, `Project`, `Funder`. |
| **O5** | Cypher Querying Policy | Parameterized templates only. Zero free-form Cypher generated in v1. |
| **O6** | Document Identity & Deduplication | Content hash (SHA-256) is canonical. Same hash = instantaneous no-op. Same filename + new hash = atomic version increment. |
| **O7** | Non-English Query Policy | Strict polite refusal template in v1. Fast rejection at G1 guard without initiating RAG retrieval. |
| **O8** | Embedding Model | Cloud embeddings via OpenRouter/OpenAI API by default. Local model supported via optional configuration for Windows dev. |
| **O9** | Full-Answer Caching | Disabled in v1 to prevent stale responses during demo and iterative tuning. Extraction and embedding caches enabled by hash. |
| **O10** | Log Data Privacy | Full prompt and response text logged **off by default** (`logging.log_full_prompts: false`). IDs, metadata, and token tallies logged. |
| **O11** | Rewrite Retry Limits | Maximum 2 rewrite attempts. Graceful partial answer degradation with warning badges upon second failure. |
| **O12** | Refusal & Guard Templates | Stored as versioned Markdown files in `templates/refusals/*.md`. Loaded dynamically. |
| **O13** | Citations for Plain Text / Tables | PDF = Page; TXT = Line Range; CSV/XLSX = Sheet + Row Number + Column Header. |
| **O14** | Backup and Restore SLO | **Deferred as `[TODO]` post-demo.** SQLite + raw files constitute complete rebuildable state. |
| **O15** | Domain & Reverse Proxy TLS | Handled via host Nginx reverse proxy terminating TLS and proxying to GrantLoom port. |
| **O16** | Quarantined Document Retention | Retained in quarantine table for 14 days (`guardrails.quarantine_retention_days = 14`) for administrative review. |
| **O17** | Document Conflict Resolution | Writer prompt instructs the model to highlight both conflicting terms along with their respective dates/versions. |
| **O18** | Compliance Matrix Export Formats | Interactive JSON response in UI + downloadable Excel (`.xlsx`) generated via `openpyxl`. |

---

## 10. Development Milestones & Rollout Plan

### Phase 1: Step 1 — Native Windows Development (Zero Docker)
- **M0 (Setup & Foundations):** Python 3.12 virtual environment, project structure, typed configuration loading (`config.json`), `ports/` interfaces, and custom exception definitions.
- **M1 (Registry & Ingestion):** SQLite document registry + outbox, SHA-256 deduplication, async polling endpoint (`GET /documents/{id}`), extractors for PDF/TXT/XLSX, and embedded ChromaDB integration.
- **M2 (Query Pipeline & SSE):** BYOK header validation, G1 input guard, hybrid retrieval (ChromaDB + SQLite FTS5 with RRF), OpenRouter writer integration, G4 citation verification, and SSE streaming.
- **M3 (Structured Data & Graph Integration):** SQLite table loading for spreadsheets with AST SQL guard; KùzuDB embedded graph setup with parameterized Cypher templates.
- **M4 (Hardening, Guards & Rewrite Loop):** Two-tier ingestion injection checks, L1–L4 sanitization, bounded rewrite loop with feedback, and starter questions endpoint (`GET /examples`).
- **M5 (Local Verification & Synthetic Tooling):** CLI generator for synthetic proposals/solicitations; unit and integration test suites validating end-to-end flow on Windows.

### Phase 2: Step 2 — VPS Deployment & Demo Verification
- **M6 (VPS Host Integration):** Systemd service or lightweight single Docker container deployment onto the 8 GB VPS. Connect GrantLoom `/metrics` to the host's existing Prometheus and Grafana.
- **M7 (Reverse Proxy & Security):** Configure Nginx reverse proxy for TLS, static frontend hosting, rate limiting, and `X-API-Key` protection.
- **M8 (Demo Rehearsal & Verification):** Verify live demo with real NOFOs and synthetic lab proposals; measure latency and memory consumption (< 2.5 GB).
