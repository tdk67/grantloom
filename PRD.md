# GrantLoom — Product Requirements Document (PRD)

**Document Version:** 1.0  
**Status:** Approved Architectural Specification  
**Date:** 2026-10-13  
**Owner:** Research Operations & Lab Systems  
**Implementation Strategy:** Two-Phase Delivery (Phase 1: Native Windows 11 / Python 3.12; Phase 2: Linux VPS Dockerization)  
**Companion Artifacts:** [Decisions Log](decisions-log.md) | [Implementation Plan](implementation-plan.md) | [Market Research](market-research.md) | [Data Strategy](data-strategy.md)

---

## 0. Document Control & Governance

| Version | Date | Author / Owner | Change Description |
|---|---|---|---|
| 0.1 | 2026-10-12 | Research Ops | Initial draft incorporating exploratory Q&A and architecture spikes. |
| 0.2 | 2026-10-12 | Lead Architect | Integrated BYOK OpenRouter, KùzuDB embedded graph, ChromaDB vector store, and 8 GB host alignment. |
| 1.0 | 2026-10-13 | Lead Architect & BA | Fully resolved architectural review: decoupled decisions/plan, specified thin frontend (Vanilla JS), established two-phase delivery, added compliance matrix FRs, defined agent reasoning loop, clarified citation resolvability vs. faithfulness, and closed operational gaps. |

---

## 1. Overview & Purpose

### 1.1 Executive Pitch
* **The Problem (1 Sentence):** Research labs spend hundreds of hours manually dissecting complex grant solicitations and cross-checking institutional proposal claims across scattered documents, risking immediate disqualification from unnoticed compliance gaps or fabricated citations.
* **The Solution (1 Sentence):** GrantLoom is a self-hosted, lightweight RAG appliance combining hybrid vector and graph retrieval that cross-references funding solicitations against institutional documents to generate verifiable, coordinate-level cited answers and automated compliance matrices.

### 1.2 Initiative Summary & Core Principles
GrantLoom is built as a **backend-first, self-hosted RAG service accompanied by a focused, thin web frontend** tailored for academic research groups. The system indexes Notices of Funding Opportunity (NOFOs / RFPs), prior proposals, researcher CVs, institutional policy manuals, and budget spreadsheets.

#### Core Architectural Commitments:
1. **Citation Resolvability by Construction:** The generation model never outputs free-form file names or page numbers. The writer emits internal chunk IDs, which the backend resolves and validates against the document registry (`filename`, `page`, `line`, `sheet`, `row`). Only fully resolvable citations are rendered to the UI.
2. **Defensible Privacy & Cost Control (BYOK):** Single-tenant, self-hosted architecture where the user brings their personal OpenRouter API key (`X-OpenRouter-Key`). Documents are never used to train external models; all outbound network egress is transparently surfaced in the UI.
3. **Two-Phase Execution Strategy:**
   * **Phase 1 (Local Workstation):** Native Python 3.12 execution on Windows 11 with **zero Docker container dependencies**, using in-process embedded stores (ChromaDB for dense vectors, KùzuDB for graph relations, SQLite for registry, structured data, and BM25 keywords).
   * **Phase 2 (VPS Deployment):** Dockerized deployment (`Dockerfile` + `docker-compose.yml`) targeting an 8 GB VPS host with a memory footprint of **< 2.5 GB RAM**, hooking directly into pre-existing host Prometheus and Grafana instances.
4. **Thin Frontend (Vanilla JS):** A fast, modern HTML/CSS/Vanilla JavaScript single-page application (SPA) with zero node build steps, served statically and communicating via REST and Server-Sent Events (SSE). Streamlit is explicitly out of scope.

---

## 2. Problem Statement & Business Context

### 2.1 The Customer Pain Point
Securing external scientific grants (NIH R01, NSF CAREER, Horizon Europe, DOE solicitations) is a resource-intensive endeavor for academic research institutions. Proposals frequently fail administrative triage before reaching scientific peer review due to non-compliance with complex eligibility clauses, budget formatting rules, or unsupported institutional claims.

Researchers face three acute hurdles:
1. **Solicitation Complexity & Density:** Solicitations average 40–100 pages of legalistic and conditional instructions, with critical submission constraints buried in disparate footnotes.
2. **Fragmented Institutional Evidence:** Crucial verification data (past project metrics, biosketch achievements, facilities access, matching fund policies) is scattered across unindexed PDFs, Word docs, and Excel sheets.
3. **The LLM Hallucination & Privacy Dilemma:** Generic commercial chatbots invent non-existent grant guidelines and calculate erroneous budget sums. Furthermore, labs cannot upload confidential, unpublished research proposals to multi-tenant commercial platforms with opaque data retention terms.

### 2.2 Market Gap & Defensible Positioning
* **Enterprise Research Administration Suites:** Cost thousands of dollars per month, require institutional procurement, and focus on post-award accounting rather than pre-award technical compliance.
* **Consumer "Chat with your PDF" Tools:** Route documents through vendor-managed multi-tenant accounts with no provenance guarantees, poor spreadsheet handling, and no ability to traverse multi-document relational chains.
* **GrantLoom's Positioning:** A single-tenant, self-hosted appliance where **the user controls the host and holds the key**. Provenance is guaranteed at the chunk level, budget spreadsheets are queried with deterministic SQL, and compliance matrices are verified against source records.

---

## 3. Target Users & Personas

GrantLoom v1 is optimized for a **single-tenant deployment model** operated by a principal investigator and their direct lab team.

```
+---------------------------------------------------------------------------------+
| PRIMARY PERSONA: Dr. Elena Vance — Principal Investigator & Lab Director        |
+---------------------------------------------------------------------------------+
| Profile:       Mid-career PI managing an academic research group.               |
| Technical:     Comfortable executing command-line scripts and running apps.     |
| Environment:   Windows 11 workstation locally (Phase 1); 8 GB Linux VPS (Phase 2).|
+---------------------------------------------------------------------------------+
| Goals:                                                                          |
| - Swiftly determine whether a 60-page solicitation matches her lab's portfolio. |
| - Verify proposal statements against past grants and biosketches with citations.|
| - Query complex budget spreadsheets across past fiscal years.                   |
| Frustrations:                                                                   |
| - AI hallucinations that cite fictional grant solicitation sections.           |
| - Proprietary cloud tools that monetize or retain unpublished research ideas.   |
| Context of Use: Runs GrantLoom locally during grant drafting sprints; brings    |
|                 her OpenRouter key to pay pennies per query directly.           |
+---------------------------------------------------------------------------------+
```

*Note on User Scope:* While postdoctoral researchers and lab managers may access the deployed instance over the lab's local network or VPN, v1 is single-tenant with a single shared master access key. Multi-tenant organizational user management is an explicit non-goal for v1.

---

## 4. Goals & Success Metrics

### 4.1 Primary Goals
1. **Enforce Absolute Citation Resolvability:** 100% of rendered citations resolve to verified, physical coordinates in the local document registry.
2. **Measurable Verification:** Claim support and faithfulness evaluated by an independent judge model family (`qa_judge_model != writer_model`).
3. **Strict Memory & Hardware Fit:** GrantLoom services run in **< 2.5 GB RAM** on the target 8 GB host, preserving host headroom.
4. **Instant Ingestion Idempotency:** Duplicate uploads incur zero redundant background worker processing.
5. **Auditable Compliance Evaluation:** Generate interactive requirement matrices and downloadable Excel reports.

### 4.2 Key Success Metrics & Targets

| Metric | Target | Measurement Method |
|---|---|---|
| **Citation Resolvability** | **100%** | Architectural invariant: unresolvable chunk IDs are intercepted and rejected by G4 output verifier. |
| **Claim Faithfulness** | **≥ 0.95** | Independent LLM judge verifying claim alignment against cited chunk spans on golden benchmark set. |
| **Direct Injection Block Rate** | **≥ 0.95** | Evaluated on held-out adversarial prompt injection set (100% on canonical OWASP test cases). |
| **Requirement Extraction Recall** | **≥ 0.90** | Proportion of mandatory solicitation constraints extracted against hand-labeled NOFO benchmarks. |
| **Requirement Extraction Precision** | **≥ 0.85** | Proportion of extracted requirements that are valid solicitation constraints. |
| **Budget Error Catch Rate** | **1.00** | Catch rate on deterministic injected budget discrepancy test suite (false-positive rate reported). |
| **Abstention Accuracy** | **≥ 0.90** | Correct abstention ("not found in indexed documents") on questions with answers absent from corpus. |
| **Single-Hop Query Latency** | **p95 ≤ 15.0 s** | Total end-to-end duration from request submission to final SSE completion (single retrieval round). |
| **Time to First Streamed Token** | **p95 ≤ 4.0 s** | Time to first streamed content token (accounting for G1 input guard + G2 router evaluation). |
| **System Memory Footprint** | **≤ 2.5 GB RAM** | Total combined Resident Set Size (RSS) under idle and active query load. |
| **Ingestion Idempotency** | **100%** | Re-uploading identical file (matching SHA-256) yields zero background worker processing. |

### 4.3 Memory Budget Decomposition (< 2.5 GB Target)

| Component | Nature / Technology | Allocated RAM | Engineering Constraint |
|---|---|---|---|
| **API Process** | Python 3.12 / FastAPI (async) | ~300 MB | 2 Uvicorn workers, async I/O. |
| **Worker Process** | Python 3.12 / Background Ingestion | ~600 MB | Peak allocation during multi-page PDF text extraction. |
| **ChromaDB** | In-process embedded persistent client | ~250 MB | **Explicitly disable default ONNX MiniLM embedder** (`embedding_function=None`); use cloud embeddings. |
| **KùzuDB** | In-process embedded columnar graph | ~150 MB | Columnar storage, in-process Cypher execution. |
| **SQLite (WAL)** | Embedded file database | ~100 MB | Shared cache, FTS5 keyword index, outbox table. |
| **Headroom & OS Buffers** | Host OS / Buffer Cache | ~1,000 MB | Safe buffer preventing any host OOM thrashing. |
| **Total GrantLoom Footprint** | | **~2,400 MB** | **Fits comfortably within 8 GB VPS alongside existing Prometheus/Grafana.** |

---

## 5. User Journeys & Critical Workflows (CUJs)

### CUJ-1: Document Ingestion & Idempotent Verification
1. **Upload:** User drops an RFP PDF (`DOE_Solar_2026.pdf`) or budget spreadsheet (`Lab_Budget_2025.xlsx`) onto the web upload area.
2. **BYOK Transport:** The thin frontend includes the stored OpenRouter key in the `X-OpenRouter-Key` request header.
3. **Idempotency Check:** API calculates streaming SHA-256 hash. If matching an `active` document, returns `200 OK` ("Document already indexed") immediately. If matching an in-flight job, returns `202 Accepted` with the active job link.
4. **Async Processing:** If new, API stores file, writes an outbox job, and returns `202 Accepted` with a `doc_id`.
5. **Status Polling:** Frontend polls `GET /api/v1/documents/{id}` every 2 seconds with exponential backoff, displaying extraction progress, page counts, and pipeline stage.
6. **Active State:** Ingestion worker parses, chunks, embeds, extracts graph entities, indexes spreadsheet tables, and marks status `active`.

### CUJ-2: Fact Retrieval with Verifiable Citations
1. **Query Entry:** User selects a starter prompt or enters: *"What are the mandatory qualifications for co-principal investigators?"*
2. **Guard & Routing:** G1 input guard screens safety and domain relevance; G2 router selects hybrid retrieval.
3. **Multi-Store Retrieval:** Chunks retrieved via dense ChromaDB vectors + SQLite FTS5 keywords (RRF fusion); G3 context filter drops noise.
4. **Generation & Verification:** Writer LLM generates the answer citing internal chunk IDs (`[chunk_4a71]`). G4 output verifier resolves IDs to coordinates (`DOE_Solar_2026.pdf, Page 14`).
5. **Interactive Exploration:** Answer streams to frontend via SSE. User clicks a citation badge; an excerpt modal opens with the source text. If a claim cannot be verified, it displays an unverified warning badge.

### CUJ-3: Automated NOFO Compliance Matrix & Scorecard
1. **Matrix Request:** User navigates to Compliance view and selects an indexed solicitation.
2. **Requirement Extraction:** System extracts mandatory requirements (`MUST`, `REQUIRED`, eligibility clauses) into structured records.
3. **Cross-Referencing & Classification:** Agent compares requirements against lab documents, classifying status (`Satisfied`, `Partial`, `Gap`) with cited justifications.
4. **Readiness Scorecard:** System calculates an estimated readiness score (e.g. `74/100`), explicitly displaying a disclaimer: *"Readiness Estimate — Not an official funding guarantee"*.
5. **Export:** User reviews the interactive matrix and clicks **Export to Excel**, downloading an audit-ready `.xlsx` spreadsheet.

### CUJ-4: Quarantined Document Resolution
1. **Taint Detection:** A proposal containing embedded adversarial prompt-injection text ("Ignore all previous instructions...") is uploaded.
2. **Quarantine State:** Ingestion Layer 4 detects the anomaly. The document is flagged `quarantined` and quarantined from all search indexes.
3. **Audit & Review:** User inspects the quarantine list, reviews flagged snippet coordinates, and decides to redact flagged spans or delete the file permanently.

---

## 6. Functional Requirements

### 6.1 Ingestion & Document Lifecycle (FR-I)

| ID | Requirement | Acceptance Criteria | MoSCoW |
|---|---|---|---|
| **FR-I1** | **Multi-Format Extraction** | Ingest PDF (text-layer), TXT, CSV, and XLSX. Scanned PDFs (no text layer) and password-protected files must be rejected with RFC 7807 error codes. PDF parsing uses `pypdf` (permissive BSD license); table extraction uses `openpyxl`/`pdfplumber`. | Must |
| **FR-I2** | **Cryptographic Idempotency** | Streaming SHA-256 calculation on upload. Matching existing hash returns `200 OK` (no background work). Matching in-flight job returns `202 Accepted`. Same filename with new hash creates atomic incremented version (`version: 2`). | Must |
| **FR-I3** | **Asynchronous Outbox & Polling** | Ingestion executed by background worker via SQLite `outbox` table. Granular progress exposed via `GET /api/v1/documents/{id}` (`stage`, `progress_pct`, `pages_processed`). Polling endpoint is exempt from aggressive rate limits. | Must |
| **FR-I4** | **Page- & Section-Aware Chunking** | PDF chunks must never span across page boundaries (ensuring exact page citations). Chunks receive deterministic IDs (`doc_id:page:index`). Metadata includes `doc_id`, `page`, `char_span`, `hash`. TXT chunks capture line ranges; spreadsheets capture sheet/row/column. | Must |
| **FR-I5** | **Two-Tier Injection & Sanitization** | Tier 1 fast heuristic regex screens 100% of chunks. Tier 2 targeted LLM check evaluates flagged chunks and a 2% random sample. Documents with > 2 flagged chunks enter `quarantined` state and are excluded from retrieval. | Must |
| **FR-I6** | **Idempotent Deletion Saga** | Document deletion executes as an idempotent saga: status set to `deleting`, worker purges vectors (ChromaDB), graph nodes (KùzuDB), and tables (SQLite), followed by registry row removal. | Must |
| **FR-I7** | **Index Rebuild Capability** | Provide CLI command (`python -m app.cli.rebuild_indexes`) to re-extract and re-index all stores from original registry files without loss of metadata. | Should |

### 6.2 Question Answering & Reasoning Pipeline (FR-Q)

| ID | Requirement | Acceptance Criteria | MoSCoW |
|---|---|---|---|
| **FR-Q1** | **BYOK Header Authentication** | API expects `X-OpenRouter-Key` header. Validated live against upstream `/api/v1/auth/key` probe. Fails fast with `401 Unauthorized` (invalid key), `402 Payment Required` (zero balance), or `502 Bad Gateway`. Key is never persisted or logged. | Must |
| **FR-Q2** | **G1 Input Guard** | Evaluates prompt injection, safety, and domain relevance (academic/grant/lab scope). Rejects non-English queries using fast character heuristic. Refused queries return dynamic template without consuming retrieval compute. | Must |
| **FR-Q3** | **G2 Strategy Router** | Router evaluates query intent to select: `hybrid`, `semantic`, `keyword`, `graph`, `structured`, or `composite`. Confidence score evaluated against `router_min_confidence`; low confidence falls back to `hybrid`. | Must |
| **FR-Q4** | **Multi-Store Hybrid Retrieval** | Combines ChromaDB dense semantic vectors with SQLite FTS5 BM25 keyword matching, fused via Reciprocal Rank Fusion (RRF). RRF constant `k` loaded from configuration. | Must |
| **FR-Q5** | **G3 Context Filter / Reranker** | Evaluates candidate chunks for relevance and drops injection-suspect text before building writer context. Truncates context to `rerank_top_k`. | Should |
| **FR-Q6** | **G4 Output Guard & Resolvability** | Writer LLM must cite internal chunk IDs. G4 resolves IDs to verified physical coordinates. If a cited chunk ID does not exist in the registry, the citation is stripped and logged. | Must |
| **FR-Q7** | **Bounded Rewrite Loop** | Claims failing support checks trigger structured feedback to writer (max 2 attempts). If verification fails after 2 rewrites, answer is rendered with visible unverified warning badges. | Must |
| **FR-Q8** | **Explicit Abstention Contract** | If retrieval yields zero chunks above `min_relevance_threshold`, system must output explicit abstention statement: *"This information was not found in the indexed documents"* rather than answering from parametric memory. | Must |
| **FR-Q9** | **Server-Sent Events (SSE) Stream** | `POST /api/v1/ask` streams status events using canonical enum (`guard`, `route`, `retrieve`, `filter`, `generate`, `verify`, `rewrite`, `done`, `error`) followed by token stream and citation metadata. | Must |
| **FR-Q10** | **UI Data Egress Indicator** | System emits event indicating data egress destination (`openrouter.ai`) and model role so frontend can render an active cloud egress badge. | Should |

### 6.3 Agent-Based Reasoning Loop (FR-A) — Capstone Task 8 Alignment

To fulfill the capstone requirements for **agent-based planning, tool retrieval, reasoning, and validation**:

| ID | Requirement | Acceptance Criteria | MoSCoW |
|---|---|---|---|
| **FR-A1** | **Orchestrated Agent Workflow** | GrantLoom operates an explicit agent reasoning loop: Planner Agent → Retriever Agent → Writer Agent → Validator Agent. | Must |
| **FR-A2** | **Discrete Tool Execution** | The Retriever Agent selects and executes discrete retrieval tools: `dense_vector_search(query, top_k)`, `bm25_keyword_search(query, top_k)`, `graph_traverse_query(template_id, params)`, `sql_tabular_query(sql_statement)`. | Must |
| **FR-A3** | **Plan & Validate Feedback** | The Validator Agent inspects generated claims against tool outputs; on verification failure, it formulates a diagnostic payload and invokes the Writer Agent with corrective instructions. | Must |

### 6.4 Compliance Matrix & Scorecard (FR-C)

| ID | Requirement | Acceptance Criteria | MoSCoW |
|---|---|---|---|
| **FR-C1** | **Requirement Extraction** | `POST /api/v1/compliance/{solicitation_id}` extracts mandatory requirements (`MUST`, `REQUIRED`, eligibility, budget ceilings) into structured database records linked to source chunk IDs. | Must |
| **FR-C2** | **Evidence Mapping & Gap Audit** | System searches lab corpus for matching evidence for each requirement, classifying status as `Satisfied`, `Partial`, or `Gap` with clickable citation coordinates. | Must |
| **FR-C3** | **Readiness Scorecard** | Computes an aggregated readiness percentage (0–100) based on weighted requirement satisfaction, displaying an explicit estimate disclaimer. | Should |
| **FR-C4** | **Audit Spreadsheet Export** | `GET /api/v1/compliance/{solicitation_id}/export` downloads a formatted Excel (`.xlsx`) compliance matrix containing requirement text, status, cited evidence, and gaps. | Must |

### 6.5 Structured Tabular & Graph Stores (FR-S)

| ID | Requirement | Acceptance Criteria | MoSCoW |
|---|---|---|---|
| **FR-S1** | **Spreadsheet Ingestion & SQL Guard** | Ingest CSV/XLSX into typed SQLite tables. LLM generates read-only `SELECT` statements validated by an AST parser (single statement, allow-listed tables/columns, timeout ≤ 5s, row limit ≤ 100). | Must |
| **FR-S2** | **KùzuDB Parameterized Traversal** | Graph relationships traversed using parameterized Cypher templates over minimal entity types (`Document`, `Requirement`, `Person`, `Project`, `Funder`). Free-form Cypher generation is prohibited. | Must |
| **FR-S3** | **Source Chunk Tracing on Graph Edges** | Every edge in KùzuDB must store a `source_chunk_id`. Graph facts without a verified source chunk are never cited in answers. | Must |

### 6.6 Thin Frontend Interface (FR-U)

| ID | Requirement | Acceptance Criteria | MoSCoW |
|---|---|---|---|
| **FR-U1** | **Zero-Build Vanilla JS SPA** | Frontend built as clean HTML5, CSS3, and modern Vanilla JS (ES Modules) with zero node build steps, served statically by FastAPI. Streamlit is prohibited. | Must |
| **FR-U2** | **Document Management UI** | Accessible drag-and-drop file upload zone, active document table with status badges (`uploading`, `extracting`, `active`, `quarantined`), and document deletion controls. | Must |
| **FR-U3** | **Streaming Chat & Confidence Score** | Conversational chat interface displaying real-time SSE progress indicators, streaming markdown responses, inline clickable citation badges, and a query confidence level badge (`High`, `Moderate`, `Low`). | Must |
| **FR-U4** | **Citation Excerpt Drawer with Keyword Highlighting** | Clicking any citation badge slides open an excerpt drawer displaying the exact source text, page/row coordinate, and query search keywords wrapped in `<mark class="kw-highlight">`. | Must |
| **FR-U5** | **Compliance Matrix Dashboard** | Dedicated table view rendering solicitation requirements, evidence status badges (`Satisfied`, `Partial`, `Gap`), and a one-click **Export to Excel** button. | Must |
| **FR-U6** | **IndexedDB Credential Security & Lockout** | API keys stored in browser IndexedDB. Live validation executes upon entry; ingestion dropzone and query input remain locked until a valid OpenRouter key or Ollama connection is confirmed. | Must |
| **FR-U7** | **Keyboard-Only Navigation & WCAG AA** | Complete keyboard navigation support with logical Tab sequence, skip-to-content link, high-contrast focus rings, and focus traps inside modals and slide-over drawers. | Must |
| **FR-U8** | **Ollama Local Model Probe & Context Scaling** | UI supports switching to local Ollama; enumerates local models via `/api/tags`, executes a diagnostic tool-calling probe, and dynamically adjusts the context retrieval window. | Should |

### 6.7 Operations, Demo Tooling & SEO (FR-O)

| ID | Requirement | Acceptance Criteria | MoSCoW |
|---|---|---|---|
| **FR-O1** | **Starter Questions Endpoint** | `GET /api/v1/examples` serves curated domain questions from `config/example_questions.json`. | Must |
| **FR-O2** | **Host Prometheus Metrics** | `/metrics` endpoint exposes Prometheus counters and histograms for request latency, router decisions, guardrail verdicts, and index sizes. Compatible with host scraper. | Must |
| **FR-O3** | **Synthetic Demo Data Generator (CLI & API)** | CLI script (`python -m app.cli.generate_synthetic`) and API endpoint (`POST /api/v1/admin/generate-synthetic-data`) generate and index realistic demo NOFOs, past proposals, and budget sheets on demand. | Must |
| **FR-O4** | **SEO & AI Crawler Artifacts** | Static root endpoints serve `/robots.txt`, `/sitemap.xml`, and `/llms.txt` for search indexers and AI assistants. | Must |

---

## 7. Non-Functional Requirements (NFR)

* **NFR-1 (Performance & Latency):** Single-hop queries must achieve p95 latency ≤ 15.0 s; time to first streamed token p95 ≤ 4.0 s. Ingestion extraction throughput must exceed 15 pages/second.
* **NFR-2 (Memory Ceiling):** Combined resident memory across all GrantLoom processes must remain **< 2.5 GB RAM** under active load on the 8 GB host. Default ONNX embedders in ChromaDB must be disabled.
* **NFR-3 (Externalized Configuration):** All operational limits (file sizes, page limits, token budgets, model designations, timeouts, rate limits) must load from `config/config.json` via strict Pydantic models with **no in-code defaults**. Pure-protocol constants (HTTP codes, enum literals) are exempt.
* **NFR-4 (Security & Credential Isolation):** Master key enforced via `X-API-Key`. BYOK OpenRouter keys reside solely in browser storage and ephemeral memory. Sensitive headers redacted from reverse-proxy logs.
* **NFR-5 (Reliability & Stage-Idempotent Outbox):** SQLite operates in WAL mode. Outbox tasks utilize stage-idempotency keys (delete-before-insert on retry) ensuring crash-safe resumes without duplicate vectors or graph nodes.
* **NFR-6 (Evaluator Independence):** Automated faithfulness and evaluation benchmarks must enforce model independence (`qa_judge_model != writer_model`). Startup checks warn if identical model families are configured.

---

## 8. System Architecture Overview & Engineering Rules

### 8.1 System Topology

```
                      +---------------------------------------+
                      |       Thin Frontend (Vanilla JS)      |
                      |  Session Storage: Keys & Settings     |
                      +---------------------------------------+
                                          │
                                          │ HTTPS (X-API-Key + X-OpenRouter-Key)
                                          ▼
                      +---------------------------------------+
                      |        Host Nginx Reverse Proxy       |
                      |  TLS, Rate Limit, SSE Buffering Off   |
                      +---------------------------------------+
                                          │
                                          ▼
+-----------------------------------------------------------------------------------+
| GrantLoom Application Service (Python 3.12)                                        |
|                                                                                   |
|  +---------------------------+             +-----------------------------------+  |
|  |     FastAPI API Layer     |             |      Ingestion Worker Layer       |  |
|  |  Routers: ask, docs,      |             |  Extract, chunk, sanitize,        |  |
|  |  compliance, examples     |             |  embed, graph index (Outbox)      |  |
|  +─────────────┬─────────────+             +─────────────────┬─────────────────+  |
|                │                                             │                    |
|                ▼                                             ▼                    |
|  +─────────────────────────────────────────────────────────────────────────────+  |
|  | Service Domain & Orchestration (Guards G1-G4, Agent Loop, Citations, Sage)   |  |
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

### 8.2 Two-Phase Delivery Strategy
* **Phase 1 (Workstation Execution):** 100% native execution on Windows 11 in a Python 3.12 virtualenv. Uses in-process ChromaDB, KùzuDB, and SQLite. Zero Docker requirements.
* **Phase 2 (VPS Deployment):** Dockerized deployment via a multi-stage `Dockerfile` and `docker-compose.yml`. Configures Nginx reverse proxy with `proxy_buffering off` for SSE streaming and maps to the host's existing Prometheus scraper.

### 8.3 Gate Architecture Table (G1 – G4)

| Gate | Name | Input | Primary Evaluation | Action on Pass | Action on Fail | Default Backend (v1) |
|---|---|---|---|---|---|---|
| **G1** | **Input Guard** | User query | Prompt injection patterns; domain relevance (grant/academic context); language check. | Forward to G2. | Reject with polite refusal template (no retrieval compute). | Regex prefilter + `PromptJudgeAdapter` |
| **G2** | **Strategy Router** | Sanitized query | Evaluates query requirements against strategies (`hybrid`, `semantic`, `keyword`, `graph`, `structured`). | Invokes selected retrieval tools. | Falls back to default `hybrid` retrieval. | `PromptJudgeAdapter` (fallback: hybrid) |
| **G3** | **Context Filter** | Candidate chunks | Scores chunk relevance; screens for injected text strings; truncates context. | Ranks top K chunks for writer prompt. | Drops irrelevant or flagged chunk blocks. | Cosine similarity + heuristic filter |
| **G4** | **Output Verifier** | Writer draft & chunk IDs | Resolves chunk IDs against registry; checks claim faithfulness and grounding. | Emits verified answer with citation badges. | Initiates bounded rewrite loop (max 2 attempts). | Registry resolver + `PromptJudgeAdapter` |

> **Implementation status (2026-10-13, decisions-log D22):** `PromptJudgeAdapter` is implemented as `LLMJudgeDecisionBackend` and is the default for G1 (after the regex prefilter), G2, G3 and G4, with a labelled heuristic fallback (`HeuristicDecisionBackend`; cosine/lexical rules as listed for G3). Every gate verdict reports the backend that produced it. Jev and Julia-1 backends from `data-strategy.md` Part 3 are **deferred** and not implemented.

### 8.4 API Surface Specification

| Method | Endpoint | Auth Headers | Request Payload | Success Response | Error Codes | FR Ref |
|---|---|---|---|---|---|---|
| `POST` | `/api/v1/documents` | `X-API-Key`, `X-OpenRouter-Key` | `multipart/form-data` (file) | `200 OK` (hash match) or `202 Accepted` (`doc_id`) | 400, 401, 402, 413, 415 | FR-I1, FR-I2 |
| `GET` | `/api/v1/documents` | `X-API-Key` | None | `200 OK` (list of documents, versions, states) | 401 | FR-I3 |
| `GET` | `/api/v1/documents/{id}` | `X-API-Key` | None | `200 OK` (progress pct, stage, pages, errors) | 401, 404 | FR-I3 |
| `DELETE` | `/api/v1/documents/{id}` | `X-API-Key` | None | `202 Accepted` (saga started) | 401, 404 | FR-I6 |
| `POST` | `/api/v1/documents/{id}/release` | `X-API-Key` | None | `200 OK` (quarantine released) | 401, 404 | FR-I5 |
| `GET` | `/api/v1/documents/{id}/preview` | `X-API-Key` | Query: `?page=&char_span=` | `200 OK` (raw text excerpt for citation modal) | 401, 404 | FR-U4 |
| `POST` | `/api/v1/ask` | `X-API-Key`, `X-OpenRouter-Key` | JSON: `{ "query": "..." }` | `200 OK` (SSE event stream) | 400, 401, 402, 429 | FR-Q1, FR-Q9 |
| `POST` | `/api/v1/compliance/{id}` | `X-API-Key`, `X-OpenRouter-Key` | None | `200 OK` (JSON compliance matrix & scorecard) | 401, 402, 404 | FR-C1, FR-C2 |
| `GET` | `/api/v1/compliance/{id}/export` | `X-API-Key` | None | `200 OK` (`application/vnd.openxmlformats...`) | 401, 404 | FR-C4 |
| `GET` | `/api/v1/examples` | `X-API-Key` | None | `200 OK` (list of curated starter questions) | 401 | FR-O1 |
| `POST` | `/api/v1/admin/generate-synthetic-data` | `X-API-Key` | None | `202 Accepted` (generates & indexes demo files) | 401 | FR-O3 |
| `POST` | `/api/v1/admin/test-model-capabilities` | `X-API-Key` | JSON: `{ "endpoint": "...", "model": "..." }` | `200 OK` (`{ "tool_calling": true/false }`) | 400, 401 | FR-U8 |
| `GET` | `/robots.txt`, `/sitemap.xml`, `/llms.txt` | None | None | `200 OK` (text/xml search and AI crawler metadata) | None | FR-O4 |
| `GET` | `/metrics` | None (or basic auth) | None | `200 OK` (Prometheus text format) | None | FR-O2 |
| `GET` | `/healthz` | None | None | `200 OK` (`{ "status": "ok" }`) | None | FR-O2 |
| `GET` | `/readyz` | None | None | `200 OK` (store connectivity confirmed) | 503 | FR-O2 |

---

## 9. Scope Boundaries & Cut-Line

### 9.1 In-Scope (v1 Release)
* Native Python 3.12 execution on Windows 11 (Phase 1).
* Dockerized deployment on 8 GB Linux VPS hooking into existing Prometheus/Grafana (Phase 2).
* Thin Vanilla JS frontend (single-page app) with citation preview modals.
* Ingestion of text-layer PDF, TXT, CSV, and XLSX files.
* Asynchronous SQLite outbox processing with polling.
* BYOK OpenRouter live authentication and streaming.
* Multi-store hybrid retrieval (ChromaDB + SQLite FTS5) with RRF fusion.
* In-process KùzuDB graph traversal via parameterized templates.
* Structured spreadsheet queries via read-only SQLite AST guard.
* Citation resolution enforcing 100% resolvability.
* Bounded rewrite loop (max 2 attempts) with fallback warning badges.
* Compliance matrix generation with downloadable `.xlsx` reports.

### 9.2 Out-of-Scope (Deferred)
* Multi-user tenancy, individual user accounts, or role-based access control.
* Optical Character Recognition (OCR) for scanned PDFs or handwriting.
* Complex visual table geometry parsing from scanned figures.
* Automated web crawlers for solicitation discovery (Grants.gov API).
* Full proposal prose drafting or section generation (v1 is an audit & retrieval appliance).
* Streamlit-based user interfaces.

### 9.3 Scope Cut-Line (Contingency Plan)
If development encounters timeline pressure prior to Phase 2:
1. **First Cut-Line:** Graph retrieval via KùzuDB (`FR-S2`) is disabled; system falls back entirely to vector + BM25 hybrid search.
2. **Second Cut-Line:** Compliance matrix `.xlsx` export (`FR-C4`) is deferred; matrix remains interactive JSON/table on screen only.
3. **Core Invariant:** Document ingestion, citation resolvability, BYOK authentication, and the thin frontend will never be cut.

---

## 10. Assumptions, Risks & Mitigations

| Risk / Assumption | Impact | Mitigation Strategy |
|---|---|---|
| **Egress Privacy Risk:** Prompts and retrieved chunks leave the local host in cloud BYOK mode. | High | Surfaced explicitly in the UI via an egress indicator badge; user controls the model endpoint via OpenRouter account; zero server-side logging of full prompt text. |
| **Shared Master Key Exposure:** Single-tenant key compromised on public VPS. | Medium | Enforce HTTPS via Let's Encrypt; configure Nginx rate limiting (30 req/min); redact `X-API-Key` headers in proxy access logs; provide key rotation script. |
| **PDF Table Parsing Imperfections:** Irregular PDF table layouts yield poor chunk boundaries. | Medium | Detect table regions during extraction and append layout confidence warnings; recommend native CSV/XLSX uploads for authoritative budget sheets. |
| **Embedded Store Concurrency:** File locks under concurrent reads/writes. | Medium | SQLite runs in WAL mode; KùzuDB opened in read-only mode in API processes while worker holds write locks during ingestion. |
| **Indirect Prompt Injection:** Adversarial documents attempt to hijack writer prompts. | High | Two-tier injection scan during ingestion; untrusted data blocks delimited in system prompt; writer model has no side-effect tools; G4 verifies output groundedness. |
| **Host OOM During Demo:** Memory consumption exceeds 8 GB VPS limits. | High | In-process embedded engines eliminate container overhead; Chroma ONNX embedder explicitly disabled; total memory budget capped at < 2.5 GB RAM. |

---

## 11. Design & Technical Artifact References

* **[Architectural Decisions Log](decisions-log.md):** Complete record of decisions D1–D18, conflict resolutions C1–C4, and design options O1–O18.
* **[Implementation Plan & Test Strategy](implementation-plan.md):** Two-phase execution plan, milestones M0–M8, test harness specifications, and Definition of Done (DoD).
* **[Market Research & Competitive Analysis](market-research.md):** Qualitative analysis of academic research funding workflows and customer interviews.
* **[Data Strategy](data-strategy.md):** Corpus taxonomy, chunking benchmarks, and golden evaluation criteria.
