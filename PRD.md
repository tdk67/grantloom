# GrantLoom — Product Requirements Document (PRD)

**Document Version:** 1.0  
**Status:** Approved Architectural Baseline  
**Date:** 2026-10-12  
**Target Environment:** Python 3.12 (Native on Windows for Step 1; Dockerized on 8 GB VPS for Step 2)  
**Companion Artifacts:** [Decisions Log](file:///c:/Data/work/grantloom/decisions-log.md) | [Implementation Plan & Test Strategy](file:///c:/Data/work/grantloom/implementation-plan.md) | [Market Research](file:///c:/Data/work/grantloom/market-research.md)

---

## 1. Overview & Purpose

### 1.1 Executive Pitch
* **The Problem (1 Sentence):** Research labs spend hundreds of hours manually dissecting complex grant solicitations and cross-checking institutional proposal claims across scattered documents, risking immediate disqualification from unnoticed compliance gaps or fabricated citations.
* **The Solution (1 Sentence):** GrantLoom is a self-hosted, lightweight RAG service combining hybrid vector and graph retrieval that cross-references funding solicitations against institutional documents to generate verifiable, coordinate-level cited answers and automated compliance matrices.

### 1.2 Initiative Summary & Ownership
GrantLoom is built as a **backend-first, self-hosted RAG service** tailored for academic research groups and research administration offices. The system indexes Notices of Funding Opportunity (NOFOs / RFPs), prior proposals, researcher CVs, institutional policy manuals, and budget spreadsheets. 

Unlike generic document chat assistants, GrantLoom guarantees **citation validity by construction**: the generation model never invents file names or page numbers; it cites verified chunk IDs that the backend resolves and validates against the document registry.

* **Product Ownership:** Research Operations & Lab Systems.
* **Target Release:** v1 Single-User Self-Hosted Appliance (Local Windows execution first, followed by a lightweight 8 GB VPS deployment).

---

## 2. Problem Statement & Business Context

### 2.1 The Customer Pain Point
Securing scientific grants (e.g. NIH R01, NSF CAREER, Horizon Europe) is an existential challenge for research institutions. Submissions routinely fail before peer review due to administrative non-compliance (missing eligibility criteria, unaligned budget categories, or unverified claims). 

Researchers face three critical hurdles:
1. **Solicitation Volume & Density:** Federal solicitations are 40–120 pages of dense, multi-layered regulatory jargon with nested conditional requirements.
2. **Scattered Institutional Memory:** Evidence required to satisfy requirements (past project outcomes, biosketches, equipment lists, matching fund policies) is fragmented across PDFs, Word documents, and Excel spreadsheets.
3. **The LLM Hallucination Trap:** Commercial, generic chatbots frequently invent citations, miscalculate spreadsheet budget figures, or conflate requirements from different grant mechanisms.

### 2.2 Market Gap
Existing market solutions fall into two unviable extremes:
* **Enterprise Grant Management Suites:** Cost $30k–$80k/year, require months of IT procurement, and focus on billing/accounting rather than technical proposal compliance.
* **Consumer "Chat with your PDF" Tools:** Lack table/spreadsheet support, hallucinate citations, transmit confidential proprietary research data to third-party model providers, and cannot map relationship chains (e.g., *Which past grant funded which equipment operated by which co-investigator?*).

GrantLoom fills this gap with a self-hosted, lightweight appliance delivering deterministic citation resolution, structured spreadsheet querying, and relationship-aware graph retrieval.

---

## 3. Target Users & Personas

GrantLoom v1 is designed for a **single-user, single-workspace** deployment model operated by technical academic researchers.

```
+---------------------------------------------------------------------------------+
| PERSONA: Dr. Elena Vance — Principal Investigator & Lab Director               |
+---------------------------------------------------------------------------------+
| Profile:       Mid-career PI managing a 12-person computational biology lab.   |
| Technical Skill: Comfortable running scripts and local web services.          |
| Environment:   Windows workstation locally; shared 8 GB VPS for team access.    |
+---------------------------------------------------------------------------------+
| Goals:                                                                          |
| - Rapidly evaluate whether a new 60-page NIH solicitation fits her lab.         |
| - Verify whether lab biosketches and facilities meet exact solicitation clauses.|
| - Ask budget questions across past and draft proposal spreadsheets.             |
| Frustrations:                                                                   |
| - Fears AI hallucinations fabricating non-existent grant guidelines.            |
| - Dislikes monthly SaaS subscriptions and refuses to send unpublished lab data  |
|   to unauthorized third-party platforms.                                        |
| Context of Use: Runs GrantLoom locally during proposal drafting sprints;         |
|                 brings her own OpenRouter API key to pay only for tokens used.   |
+---------------------------------------------------------------------------------+
```

Other beneficiaries include **Postdoctoral Researchers** drafting proposal sections and **Academic Department Administrators** auditing budget spreadsheets.

---

## 4. Goals & Success Metrics

### 4.1 Primary Product Goals
1. **Zero Hallucinated Citations:** Every factual claim must link to a verified, clickable source coordinate (file name, page number, line range, or sheet/row).
2. **Lightweight System Footprint:** The entire backend (API, worker, dense vector store, graph store, and relational store) must run on an **8 GB VPS using < 2.5 GB RAM**.
3. **Transparent Cost Control (BYOK):** Users provide their own OpenRouter API key; zero subscription fees or server-side inference liability.
4. **Instant Ingestion Idempotency:** Duplicate document uploads must process in zero compute time via cryptographic deduplication.

### 4.2 Key Success Metrics & Targets

| Metric | Target | Measurement Method |
|--------|--------|--------------------|
| **Citation Validity Rate** | ≥ 0.98 | Proportion of claims whose citations point to verified text spans in the registry on the golden test set. |
| **Faithfulness Score** | ≥ 0.90 | Automated LLM judge evaluation verifying generated claims against retrieved context blocks. |
| **Single-Hop Query Latency** | p95 ≤ 15 s | End-to-end time from query submission to complete stream completion (first token ≤ 1.5 s). |
| **System Memory Footprint** | ≤ 2.5 GB RAM | Resident Set Size (RSS) across API, worker, and embedded stores under idle + active query load. |
| **Direct Injection Block Rate** | ≥ 0.95 | Percentage of adversarial prompt-injection queries rejected at the G1 input guard. |
| **Ingestion Idempotency** | 100% | Re-uploading identical documents incurs zero background worker execution. |

---

## 5. User Journeys & Critical Workflows (CUJs)

### CUJ-1: Document Ingestion & Idempotent Verification
1. **Upload:** User drags and drops an RFP PDF (`DOE_Solar_2026.pdf`) and past budget spreadsheet (`Lab_Budget_2025.xlsx`) into the UI.
2. **BYOK Transport:** UI includes the user's OpenRouter API key in the `X-OpenRouter-Key` request header.
3. **Idempotency Check:** Backend calculates SHA-256 hash. If already indexed, the system returns `200 OK` ("Document already active") immediately.
4. **Async Processing:** If new, backend accepts (`202 Accepted`), stores file in the registry, and enqueues tasks into the SQLite outbox.
5. **Status Polling:** Frontend polls `GET /api/v1/documents/{id}` displaying extraction progress, page counts, and current stage.
6. **Active State:** Document index completes across ChromaDB, KùzuDB, and SQLite tables.

### CUJ-2: Fact Retrieval with Verifiable Citations
1. **Query Entry:** User selects a starter prompt or enters: *"What are the mandatory qualifications for co-principal investigators in the DOE solicitation?"*
2. **Guard & Route:** G1 guard validates query safety. G2 router selects hybrid retrieval.
3. **Generation:** Writer LLM generates the answer, citing internal chunk IDs (`[chunk_4a71]`).
4. **Output Verification:** G4 guard resolves chunk IDs to physical coordinates (`DOE_Solar_2026.pdf, Page 14`).
5. **Interactive Exploration:** User reads the streamed answer; clicking a citation badge opens the preview excerpt highlighting the source text.

### CUJ-3: Automated NOFO Compliance Matrix Generation
1. **Matrix Request:** User requests compliance evaluation for a specific solicitation ID.
2. **Requirement Extraction:** System queries KùzuDB and ChromaDB to extract all mandatory requirements from the solicitation.
3. **Cross-Referencing:** System retrieves past lab proposals, equipment lists, and CVs to evaluate compliance status per requirement (`Satisfied`, `Partial`, `Gap`).
4. **Review & Export:** User reviews the matrix on-screen and clicks **Export to Excel** to download an audit-ready `.xlsx` spreadsheet.

### CUJ-4: Quarantined Document Resolution
1. **Taint Detection:** A collaborator uploads a proposal containing hidden prompt-injection text ("Ignore all previous instructions...").
2. **Quarantine State:** Ingestion Layer 4 detects the anomaly; document enters `quarantined` state and is excluded from search indexes.
3. **Audit & Review:** User inspects the administrative quarantine log (`GET /api/v1/documents`), views the flagged spans, and elects to redact or permanently delete the file.

---

## 6. Functional Requirements

### 6.1 Ingestion & Document Lifecycle (FR-I)
* **FR-I1 (Supported Formats):** System must parse PDF (text-layer), TXT, CSV, and XLSX formats. Password-protected files and scanned PDFs without a text layer must be rejected with descriptive error messages.
* **FR-I2 (SHA-256 Idempotency):** Every file upload must compute a streaming SHA-256 hash. Identical hashes must resolve as immediate no-ops; identical filenames with different hashes must increment document versioning atomically.
* **FR-I3 (Asynchronous Outbox & Polling):** File ingestion must execute asynchronously via a durable SQLite outbox table. Status must be queryable via `GET /api/v1/documents/{id}` (`stage`, `progress_pct`, `pages_processed`).
* **FR-I4 (Sanitization & Quarantine):** Extraction must strip zero-width characters and hidden microscopic text. Chunks triggering prompt-injection heuristics must be quarantined and isolated from retrieval.
* **FR-I5 (Cascading Deletion):** Deleting a document must atomically purge its raw files, registry record, ChromaDB vectors, KùzuDB nodes/edges, and SQLite spreadsheet tables.

### 6.2 Question Answering & Reasoning (FR-Q)
* **FR-Q1 (BYOK Header Validation):** API must accept `X-OpenRouter-Key` in request headers. Requests must fail fast (`401 Unauthorized`) via live upstream validation if the key is invalid or lacks credit balance. The key must never be logged or written to disk.
* **FR-Q2 (G1 Input Guard):** Queries must be screened for prompt injection, adversarial exploits, and out-of-domain topics. Non-English queries must be rejected with a polite refusal template.
* **FR-Q3 (G2 Router):** Router dynamically classifies queries into retrieval strategies: `semantic`, `keyword`, `hybrid`, `graph`, `structured`, or `composite`.
* **FR-Q4 (Hybrid Retrieval):** Semantic queries combine dense vectors (ChromaDB) and sparse BM25 keyword matching (SQLite FTS5) fused via Reciprocal Rank Fusion (RRF).
* **FR-Q5 (G4 Output Guard & Citations):** Generated claims must cite chunk IDs. Backend must resolve chunk IDs into physical coordinates (`file_name`, `page`, `line`, `sheet`, `row`) and verify that claims are supported by the source text.
* **FR-Q6 (Bounded Rewrite Loop):** If ungrounded claims are detected, the output guard must trigger a rewrite loop (max 2 attempts) with structured feedback. Persistent failures must degrade gracefully with explicit UI warning badges.
* **FR-Q7 (Server-Sent Events Streaming):** `POST /api/v1/ask` must stream stage progress events (`guarding`, `retrieving`, `generating`, `verifying`) followed by generated tokens and resolved citation metadata.

### 6.3 Structured & Relational Stores (FR-S)
* **FR-S1 (Spreadsheet SQL Queries):** CSV and XLSX sheets must load into typed SQLite tables. The LLM generates single `SELECT` statements validated by an AST SQL guard (read-only, table allow-list, row limit ≤ 100).
* **FR-S2 (KùzuDB Graph Traversal):** Graph retrieval must use parameterized Cypher templates over minimal entity types (`Document`, `Requirement`, `Person`, `Project`, `Funder`). Generation of free-form Cypher is strictly prohibited in v1.
* **FR-S3 (Source Tracing on Edges):** Every edge in KùzuDB must store a `source_chunk_id`. Graph facts without verified source chunks must never be cited in answers.

### 6.4 Operations, UI Starter Data & Tooling (FR-O)
* **FR-O1 (Starter Questions):** `GET /api/v1/examples` must serve curated demo questions from `config/example_questions.json`.
* **FR-O2 (Prometheus Metrics Scrape):** Backend must expose `/metrics` endpoint compatible with the host's pre-existing Prometheus scraper.
* **FR-O3 (Synthetic Data CLI):** A standalone CLI script (`python -m app.cli.generate_synthetic_docs`) must generate synthetic NOFOs and lab proposals on demand without checking generated documents into version control.

---

## 7. Non-Functional Requirements (NFR)

* **NFR-1 (Performance & Latency):** Single-hop queries must achieve p95 latency ≤ 15 seconds; first streaming token emitted within 1.5 seconds. Ingestion extraction throughput must exceed 20 pages/second.
* **NFR-2 (Memory Ceiling):** Total resident memory across all GrantLoom processes (API, worker, embedded ChromaDB, KùzuDB, and SQLite) must remain **below 2.5 GB RAM** under active load on the 8 GB host.
* **NFR-3 (Zero Hardcoding):** All capacities, timeouts, model designations, token budgets, and thresholds must load dynamically from `config/config.json`. Application code must contain zero hardcoded constants.
* **NFR-4 (Security & Privacy):** The API must enforce master key authorization (`X-API-Key`). BYOK OpenRouter keys must reside solely in client browser storage and ephemeral backend memory. Full prompt text logging must be disabled by default (`logging.log_full_prompts: false`).
* **NFR-5 (Reliability & Crash Recovery):** SQLite database must run in Write-Ahead Logging (WAL) mode. System restarts must leave the registry consistent and resume uncompleted outbox tasks.
* **NFR-6 (Auditability & Reproducibility):** Every query response must log a configuration hash and prompt template version to ensure deterministic evaluation.

---

## 8. High-Level System Architecture & Engineering Principles

### 8.1 Component Architecture Overview

```
                      +---------------------------------------+
                      |         Web Client (Vanilla JS)       |
                      |  Stores: X-API-Key & OpenRouter Key   |
                      +---------------------------------------+
                                          │
                                          │ HTTPS (X-API-Key + X-OpenRouter-Key)
                                          ▼
                      +---------------------------------------+
                      |        Host Nginx Reverse Proxy       |
                      |  TLS Termination, Rate Limiting (30/m)|
                      +---------------------------------------+
                                          │
                                          ▼
+-----------------------------------------------------------------------------------+
| GrantLoom Application Service (Python 3.12)                                        |
|                                                                                   |
|  +---------------------------+             +-----------------------------------+  |
|  |     FastAPI API Layer     |             |      Ingestion Worker Layer       |  |
|  |  Routers: ask, docs,      |             |  Extract, chunk, sanitize,        |  |
|  |  examples, metrics        |             |  embed, graph index               |  |
|  +─────────────┬─────────────+             +─────────────────┬─────────────────+  |
|                │                                             │                    |
|                ▼                                             ▼                    |
|  +─────────────────────────────────────────────────────────────────────────────+  |
|  | Service Domain & Orchestration (Guards G1-G4, Router, Citations, Rewrites)  |  |
|  +──────────────────────────────────────┬──────────────────────────────────────+  |
|                                         │                                         |
|                                         ▼                                         |
|  +─────────────────────────────────────────────────────────────────────────────+  |
|  | Hexagonal Ports: VectorStore | GraphStore | StructuredStore | LLMClient     |  |
|  +──────────────────────────────────────┬──────────────────────────────────────+  |
|                                         │                                         |
|        ┌────────────────────────────────┼────────────────────────────────┐        |
|        ▼                                ▼                                ▼        |
|  +───────────────+              +───────────────+              +───────────────+  |
|  | ChromaDB Port |              |  KùzuDB Port  |              |  SQLite Port  |  |
|  | (Dense Vecs)  |              | (Cypher Graph)|              | (Registry/FTS)|  |
|  +───────────────+              +───────────────+              +───────────────+  |
+-----------------------------------------------------------------------------------+
         │                                                                 │
         ▼                                                                 ▼
+─────────────────────────────────+             +───────────────────────────────────+
|   OpenRouter Cloud API (BYOK)   |             |  Pre-existing Host Monitoring     |
|   (Writer / Judge Models)       |             |  Prometheus & Grafana (Port 9090) |
+─────────────────────────────────+             +───────────────────────────────────+
```

### 8.2 Strict Architectural Principles
1. **Hexagonal Architecture (Ports & Adapters):** Domain services interact solely with abstract port interfaces. Storage and cloud adapters (Chroma, Kùzu, SQLite, OpenRouter) can be mocked or swapped without touching business logic.
2. **Layered Boundaries:** Request flow is strictly unidirectional (`API -> Service -> Repository/Adapter`). API routers never execute direct database queries or business algorithms.
3. **Fail-Fast Error Propagation:** No artificial fallbacks concealing bugs. Missing records fail immediately with RFC 7807 problem details.
4. **Intelligent Retries Only:** Exponential backoff is applied solely to transient upstream network failures (HTTP 429, 502/503/504). Client errors (400, 401, 404, 422) never retry.
5. **Clean Code Ceilings:** Focused modules targeting 200–500 lines (hard ceiling of 1,000 lines). Zero dead code or duplicate utilities.

---

## 9. Scope Boundaries

### 9.1 In-Scope (v1 Release)
* Native execution on Windows 11 (Python 3.12) with zero Docker dependencies (Step 1).
* Lightweight deployment on 8 GB Linux VPS hooking into existing Prometheus/Grafana (Step 2).
* Ingestion of PDF (text-layer), TXT, CSV, and XLSX files.
* Asynchronous outbox processing with live status polling.
* BYOK OpenRouter integration with live key validation.
* Hybrid retrieval (ChromaDB dense + SQLite FTS5 BM25 with RRF).
* In-process graph retrieval via KùzuDB parameterized templates.
* Structured spreadsheet queries via read-only SQLite and AST guard.
* Citation verification resolving chunk IDs to file/page/line/sheet/row coordinates.
* Bounded rewrite loop (max 2 attempts) for claim verification.
* Compliance matrix generation with Excel (`.xlsx`) export.
* Starter questions endpoint (`GET /api/v1/examples`).

### 9.2 Out-of-Scope (Deferred to Future Releases)
* Multi-user tenancy, organizational accounts, and user registration UI.
* Optical Character Recognition (OCR) for scanned PDFs and handwritten documents.
* Complex layout table extraction from visual PDF geometries.
* Automated funding opportunity web scraping / solicitation discovery crawlers.
* Collaborative live multi-author proposal drafting editors.
* Server-side automated backup daemons (deferred as `[TODO]`).

---

## 10. Assumptions, Risks & Mitigations

| Risk / Assumption | Impact | Mitigation Strategy |
|-------------------|--------|---------------------|
| **Assumption:** User provides a funded OpenRouter API key. | High | Middleware executes live `/api/v1/auth/key` validation before processing requests; fails fast with clear guidance if balance is zero. |
| **Risk: PDF Table Extraction Noise:** Irregular PDF tables produce corrupted text chunks. | Medium | Flag tables with confidence warnings; mandate that authoritative budgets be uploaded in native Excel/CSV format. |
| **Risk: Concurrency on Embedded Stores:** File-based databases locking under concurrent read/write. | Medium | SQLite runs in WAL mode; KùzuDB opens in read-only mode in API processes while worker holds write locks during ingestion. |
| **Risk: Indirect Prompt Injection:** Adversarial instructions hidden inside uploaded proposals hijack the LLM. | High | Two-tier defense: fast regex prefilter, sandboxed extraction, strict untrusted context delimiters, and writer tool restrictions. |
| **Risk: VPS Host OOM:** Running multiple services exhausts the 8 GB host RAM. | High | In-process embedded engines (ChromaDB + KùzuDB + SQLite) eliminate container overhead; reuse existing host Prometheus/Grafana; overall RAM capped at < 2.5 GB. |

---

## 11. Design & Technical Artifact References

To ensure modularity and maintainability, implementation details, decisions, and data strategies are maintained in dedicated reference documents:

* **[Architectural Decisions Log](file:///c:/Data/work/grantloom/decisions-log.md):** Complete record of decisions D1–D18, conflict resolutions C1–C4, and detailed options O1–O18.
* **[Implementation Plan & Test Strategy](file:///c:/Data/work/grantloom/implementation-plan.md):** Two-phase rollout plan, milestones M0–M8, test harness specifications, and Definition of Done (DoD).
* **[Market Research & Competitive Analysis](file:///c:/Data/work/grantloom/market-research.md):** Qualitative and quantitative research on academic research funding challenges.
* **[Data Strategy Document](file:///c:/Data/work/grantloom/data-strategy.md):** Golden evaluation corpora and benchmark taxonomy.
