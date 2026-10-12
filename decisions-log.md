# GrantLoom — Architectural Decisions Log

**Document Version:** 1.0  
**Date:** 2026-10-13  
**Status:** Approved Baseline  
**Companion Document:** [PRD.md](PRD.md)

---

## 1. Core Architectural Decisions (D1 – D21)

| ID | Decision | Choice | Rationale |
|----|----------|--------|-----------|
| **D1** | **Backend Stack** | Python 3.12 + FastAPI (async) | Modern async runtime, native Windows development support without Docker, fast SSE streaming. |
| **D2** | **Vector Store** | **ChromaDB** (embedded persistent mode) + **SQLite FTS5** | In-process execution in Python 3.12 without external Docker daemons; dense embeddings stored in ChromaDB, sparse keywords in SQLite FTS5, combined via Reciprocal Rank Fusion (RRF). Pluggable via `VectorStorePort`. **Default ONNX embedder explicitly disabled** to save ~250 MB RAM. |
| **D3** | **Graph Store** | **KùzuDB** (`kuzu` embedded in-process database) | Replaced heavy Neo4j JVM container. Runs embedded in Python, native Cypher support, < 100 MB RAM footprint, zero Docker container requirements. Pluggable via `GraphStorePort`. |
| **D4** | **Structured Tabular Store** | **SQLite** (read-only mode) + AST SQL Guard | Excel and CSV sheets loaded as typed tables; LLM generates read-only `SELECT` statements validated against an allow-list AST parser. |
| **D5** | **Source of Truth** | **Document Registry** (SQLite) + Outbox | SQLite with WAL mode stores document metadata, SHA-256 hashes, version trees, and outbox queue. Vector, graph, and SQL indexes are derived and rebuildable from original files. |
| **D6** | **Server Access Control** | Static Master Key (`X-API-Key`) | Single-tenant deployment without user registration/login screens. Server protected via static secret header passed by the frontend. |
| **D7** | **LLM Inference & Billing** | **Bring Your Own Key (BYOK)** via OpenRouter | User provides their personal OpenRouter key via `X-OpenRouter-Key` request header. Zero LLM cost/token liability for the server host. Key is validated live, used ephemerally in-memory, and never persisted to database or disk. Stored only in browser client storage. |
| **D8** | **Private Mode** | Config-Driven Toggle (`llm.private_mode: false`) | Architectural switch allowing local Ollama endpoints for testing correctness on local workstations. Disabled for demo on 2-vCPU VPS. |
| **D9** | **Decision Backend** | Prompt-Based Judge (Cloud LLM) | Julia-1 container dropped to conserve RAM. Abstract `DecisionBackendPort` used for G1–G4 guardrails. |
| **D10** | **Observability Integration** | Reuse Host Prometheus & Grafana | FastAPI exposes `/metrics`. Hooks into pre-existing Prometheus/Grafana already running on the demo machine for another project. Zero duplicate monitoring containers. |
| **D11** | **Ingestion Flow** | Asynchronous with Status Polling | Ingestion runs asynchronously via SQLite outbox. Frontend polls `GET /api/v1/documents/{id}` for stage, percentage, and page counts. |
| **D12** | **Ingestion Idempotency** | Canonical Content-Hash (SHA-256) | Duplicate file upload with identical SHA-256 is an immediate `200 OK` no-op. Re-uploading with identical name but modified content creates an atomic version increment. |
| **D13** | **Output Verification & Rewrites** | Bounded Rewrite Loop (Max 2 attempts) | Model returns chunk IDs. Server resolves to verified physical page/line/row coordinates. Claims failing verification trigger a structured rewrite feedback loop; unresolvable claims degrade gracefully with warning badges. |
| **D14** | **Zero Hardcoding** | Strict Configuration Separation | All capacities (max documents, file sizes, page limits), model names, temperatures, and thresholds are loaded from `config/config.json`. Secrets reside strictly in `.env`. |
| **D15** | **Starter Questions** | Dedicated Metadata Endpoint | `GET /api/v1/examples` serves curated domain questions from `config/example_questions.json` for immediate demo exploration. |
| **D16** | **Synthetic Data Strategy** | On-Demand Generator Script | No synthetic test documents checked into Git. Generated on demand via `python -m app.cli.generate_synthetic_docs`. |
| **D17** | **Engineering Principles** | Hexagonal Ports & Adapters + Layered Design | `api -> service -> repository/adapter` layered architecture. Clear port interfaces, fail-fast mechanics, RFC 7807 problem details, and intelligent retries only. |
| **D18** | **Document Conflict Policy** | Surface Both with Dates/Versions | When solicitations or proposals state conflicting guidelines, the LLM writer highlights both contradictory claims with their respective document dates and versions. |
| **D19** | **Frontend Technology** | **Thin HTML5/CSS3/Vanilla JS SPA** | Modern zero-build single-page application served statically by FastAPI. Streamlit is explicitly rejected (inflexible SSE streaming, high memory, and difficult citation modal integration). |
| **D20** | **Two-Phase Delivery** | Windows Native First → VPS Dockerization | Phase 1 runs natively on Windows 11 with zero Docker dependencies for fast development. Phase 2 packages into a multi-stage Docker container (`docker-compose.yml`) for VPS demo and rubric compliance. |
| **D21** | **PDF Extraction Library** | **`pypdf`** + `openpyxl`/`pdfplumber` | Permissive BSD license for text extraction avoiding AGPL viral licensing issues (such as PyMuPDF). Fast, lightweight, and page-exact. |

---

## 2. Architectural Conflict Resolutions (C1 – C4)

| Conflict ID | Initial Conflict | Resolution & Engineering Rationale |
|-------------|------------------|------------------------------------|
| **C1** | Full stack memory (> 6 GB) vs 8 GB host with existing services | **Resolved:** Dropped Neo4j JVM (~1.2 GB) for embedded KùzuDB (~100 MB). Replaced external Qdrant container with in-process ChromaDB. Dropped Julia-1 daemon. Reused host's pre-existing Prometheus/Grafana instances. Total GrantLoom memory reduced to **< 2.5 GB RAM**. |
| **C2** | Private mode on 2-vCPU VPS | **Resolved:** Private mode is an optional configuration toggle (`llm.private_mode: false`). Demo runs in cloud BYOK mode. Local Ollama testing supported on developer laptop. |
| **C3** | Public demo VPS vs "No Login" | **Resolved:** Protected by `X-API-Key` server secret header + reverse proxy TLS/rate limiting. No login UI required. |
| **C4** | Neo4j JVM memory footprint | **Resolved:** Dropped Neo4j completely. Adopted **KùzuDB** behind `GraphStorePort`. Kùzu runs embedded in Python 3.12, parses Cypher natively, and requires no background daemon. |

---

## 3. Detailed Design Decisions (O1 – O18)

| # | Topic | Decision | Details |
|---|-------|----------|---------|
| **O1** | Target Host | 8 GB VPS (< 2.5 GB allocated) | Runs comfortably on the 8 GB host alongside pre-existing Prometheus and Grafana. |
| **O2** | Private Mode | Config-Driven Toggle | `llm.private_mode: false` in `config.json`. Points to local Ollama if enabled for testing. |
| **O3** | Ingestion Injection Scan | Two-Tier Hybrid Pipeline | 100% regex heuristic scan + targeted LLM verification for flagged chunks and 2% random sample. Cached by chunk hash. |
| **O4** | Graph Entity Schema | Minimal Schema (v1) | Node types: `Document`, `Requirement`, `Person`, `Project`, `Funder`. Evaluated via retrieval ablation. |
| **O5** | Cypher Querying | Parameterized Templates Only | Zero free-form Cypher generated in v1 to eliminate Cypher injection and syntax hallucinations. |
| **O6** | Document Identity | SHA-256 Content Hash | Hash is canonical ID. Duplicate hash = no-op. Same name + new hash = version increment. |
| **O7** | Non-English Queries | Strict Polite Refusal | G1 input guard rejects non-English queries immediately without initiating RAG retrieval. |
| **O8** | Embedding Model | Cloud OpenRouter / OpenAI Embeddings | Cloud embeddings by default to avoid local CPU/RAM overhead on 2-vCPU host; local embedding option supported via config. |
| **O9** | Answer Caching | Disabled in v1 | Full-answer caching disabled to avoid stale responses during demo; extraction and embeddings cached by content hash. |
| **O10** | Log Data Privacy | Anonymized Logs Only | `logging.log_full_prompts: false` by default. Only metadata, latencies, and token counts logged. |
| **O11** | Rewrite Retry Limits | Maximum 2 Iterations | Bounded rewrite loop. Degrades gracefully to partial answer with warning badges if claims cannot be resolved. |
| **O12** | Refusal Templates | Versioned Markdown Files | Refusal copy stored in `templates/refusals/*.md`. Loaded dynamically. |
| **O13** | Coordinate Citations | Media-Specific Coordinates | PDF = Page + text snippet; TXT = Line Range; CSV/XLSX = Sheet + Row Number + Column Header. |
| **O14** | Backup and Restore | Deferred as `[TODO]` | Deferred for post-demo. SQLite registry + raw files form 100% rebuildable state. |
| **O15** | Domain & Reverse Proxy | Host Nginx Reverse Proxy | Nginx terminates TLS and enforces IP/rate limiting with SSE buffering disabled. |
| **O16** | Quarantine Retention | 14 Days Retention | Quarantined files retained in quarantine table for 14 days (`guardrails.quarantine_retention_days = 14`). |
| **O17** | Document Conflict Resolution | Highlight Both with Dates | Prompts instruct model to highlight both conflicting terms along with their respective dates/versions. |
| **O18** | Compliance Export Format | JSON + Downloadable Excel | Screen JSON view + downloadable `.xlsx` generated via `openpyxl`. |
