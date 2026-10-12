# GrantLoom 🧶

> **A backend-first, self-hosted RAG appliance for academic research labs.**  
> Cross-referencing funding solicitations against institutional documents with verifiable citations and automated compliance matrices.

[![Project Status](https://img.shields.io/badge/Status-Design%20Phase-blue.svg)](#current-status--phase)
[![Python Version](https://img.shields.io/badge/Python-3.12-green.svg)](#architecture-at-a-glance)
[![Architecture](https://img.shields.io/badge/Architecture-Hexagonal%20%2F%20Embedded-orange.svg)](#architecture-at-a-glance)

---

## 📌 Problem & Solution

* **The Problem:** Research labs spend hundreds of hours manually dissecting complex grant solicitations and cross-checking institutional proposal claims across scattered documents, risking immediate disqualification from unnoticed compliance gaps or fabricated citations.
* **The Solution:** GrantLoom is a self-hosted, lightweight RAG service combining hybrid vector and graph retrieval that cross-references funding solicitations against institutional documents to generate verifiable, coordinate-level cited answers and automated compliance matrices.

---

## 🚀 Key Capabilities

1. **Citation Resolvability by Construction:** The LLM never invents filenames or page numbers; it cites internal chunk IDs that the backend resolves and validates against the document registry (`filename`, `page`, `line`, `sheet`, `row`).
2. **Bring Your Own Key (BYOK):** Users provide their personal OpenRouter API key via request headers (`X-OpenRouter-Key`). The backend never persists or logs keys, eliminating server token liability.
3. **Two-Phase Delivery:**
   * **Phase 1 (Local Workstation):** Runs natively on Python 3.12 on Windows 11 with **zero Docker container dependencies** using in-process ChromaDB, KùzuDB, and SQLite.
   * **Phase 2 (VPS Deployment):** Dockerized deployment (`Dockerfile` + `docker-compose.yml`) targeting an 8 GB VPS host consuming **< 2.5 GB RAM**, hooking into pre-existing host Prometheus and Grafana instances.
4. **Thin Frontend (Vanilla JS):** Clean HTML5/CSS3/Vanilla JS single-page application with zero node build steps, served statically and communicating via REST and Server-Sent Events (SSE). Streamlit is explicitly excluded.
5. **Instant Ingestion Idempotency:** Streaming SHA-256 deduplication ensures re-uploading an existing file is an instantaneous no-op.
6. **Automated Compliance Matrix:** Cross-references NOFO requirements against lab past proposals and CVs, outputting interactive matrices with downloadable `.xlsx` reports.

---

## 🏗 Architecture at a Glance

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

---

## 📂 Project Documentation

GrantLoom maintains a strict separation between product requirements, design decisions, and rollout planning:

| Document | Purpose |
|----------|---------|
| [PRD.md](PRD.md) | **Product Requirements Document (v1.0):** Problem, solution, target personas, CUJs, functional/non-functional requirements, gate specifications, and architectural overview. |
| [decisions-log.md](decisions-log.md) | **Architectural Decisions Log:** Record of all technical choices (D1–D21), resolved memory/host conflicts (C1–C4), and design parameters (O1–O18). |
| [implementation-plan.md](implementation-plan.md) | **Implementation & Test Plan:** Two-phase execution plan (Windows dev → VPS demo), milestones M0–M9, test strategy, and Definition of Done. |
| [market-research.md](market-research.md) | **Market Research:** Detailed user pain points, workflow friction, and competitive analysis. |
| [data-strategy.md](data-strategy.md) | **Data Strategy:** Document typology, extraction strategies, and evaluation corpus specifications. |

---

## 📋 Current Status & TODOs (Design Phase)

The project is currently in the **Design & Architecture Phase**. The architectural foundation and technical specifications are approved. 

### Immediate TODOs (Design Phase Wrap-up & Scaffolding)
- [ ] **Finalize Thin Frontend UI Layout:** Define HTML/CSS wireframes for the single-page interface (upload dropzone, streaming chat with citation badges, citation preview modal, and compliance matrix view).
- [ ] **Lock In OpenAPI Contract:** Formulate the formal OpenAPI v3 specification matching [PRD.md Section 8](PRD.md).
- [ ] **Draft `config/config.json` Schema:** Produce the default typed configuration file covering all operational limits, model roles, and timeouts.
- [ ] **Draft `config/example_questions.json`:** Curate the initial starter domain prompts for demo users.

### Implementation Phase TODOs (Upcoming)
- [ ] **Milestone M0:** Initialize Python 3.12 project structure, `pyproject.toml`, and abstract `ports/` interfaces.
- [ ] **Milestone M1:** Build SQLite registry, outbox worker, and embedded ChromaDB ingestion adapter.
- [ ] **Milestone M2:** Implement BYOK live key validation, hybrid retrieval (Chroma + SQLite FTS5), and citation resolver.
- [ ] **Milestone M3:** Integrate KùzuDB graph templates and AST SQL guard for spreadsheet querying.
- [ ] **Milestone M4:** Add two-tier injection screening, bounded rewrite loop, and compliance matrix extraction.
- [ ] **Milestone M5:** Implement Thin Vanilla JS single-page web interface.
- [ ] **Milestone M6:** Implement agent reasoning loop and execute golden benchmark evaluation.
- [ ] **Milestones M7–M9:** VPS Dockerization, reverse proxy setup, live demo rehearsal, and capstone packaging.
