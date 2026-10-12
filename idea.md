# 🏛️ Grant Writing & RFP Response Co-Pilot

**Status:** Idea → PRD phase (critically reviewed 2026-10-12; see `project-review.md`)
**Date:** 2026-10-12
**Chosen name:** **GrantLoom** (GrantForge is taken by 8+ products; see `market-research.md`)

---

## Naming Brainstorm

| # | Name | Pros | Cons |
|---|------|------|------|
| 1 | **GrantForge** | Forging proposals from raw evidence; strong, action-oriented; memorable; implies craftsmanship | Might sound aggressive for nonprofit/edu sector |
| 2 | **ProposalCraft** | Gentle, craft/artisan vibe; fits nonprofit + academic tone | A bit soft; doesn't hint at compliance/rigor |
| 3 | **ComplyWrite** | Highlights the unique compliance-checking angle | "Comply" sounds bureaucratic |
| 4 | **BidForge** | Direct, strong; "Bid" covers both grants and RFPs | "Bid" leans corporate — may alienate nonprofits |
| 5 | **DraftWright** | "Wright" = skilled craftsperson; unique, classy | Unusual word; pronunciation ambiguity |
| 6 | **GrantLoom** | Weaving together evidence threads; elegant metaphor | Too abstract; doesn't convey compliance |
| 7 | **FundForge** | Broad (grants, VC, crowdfunding); strong | Broadness means less focused identity |
| 8 | **ProposalX** | Clean, modern, tech-forward | Generic; X suffix is tired |

**Top picks:** GrantForge, ProposalCraft, DraftWright

---

## What It Solves

**Target user:** a university research lab, small research institute, or grant consultant preparing federal (NIH/NSF/DOE-style) applications. A development office or consultant may submit **20–40 applications per year**; a single lab submits far fewer. Each requires parsing a long solicitation, cross-referencing past proposals, CVs, budgets, and policies. A missed mandatory item can cause an administrative rejection before peer review. Federal proposals commonly take **40–80+ hours**; short foundation applications take far less.

**Core promise:** a *pre-submission compliance audit*: which requirements are met, with which evidence, and what is missing. This is a narrower and more defensible claim than "writes your grant", where a general LLM chat is a free substitute.

### Illustrative Unit Economics (assumptions, not facts)

| Metric | Assumed value | Status |
|--------|---------------|--------|
| Applications per year (consultant/dev office) | 25 | Assumption; varies widely |
| Hours per application (manual) | 60 | Assumption; federal-scale |
| Time saved by tool | 30% | **Unvalidated**; measure in a pilot |
| Hours saved per year | 450 | = 25 × 60 × 0.30 |
| Value at $75/hr | $33,750/year | = 450 × 75 |
| Applications rejected for compliance errors | unknown | Previous "15–20% industry est." had no source; removed |

To validate: time 3 real compliance reviews manually vs. with the tool, and count requirements missed.

---

## How It Works — Agent Architecture

```
User uploads: Solicitation PDF + Org Document Library
                        │
                        ▼
  ┌─────────────────────────────────────────────────────────┐
  │                   ORCHESTRATOR                           │
  │            (Intent Router: analyze / draft / audit)       │
  └──────────────────────────┬──────────────────────────────┘
                             │
       ┌─────────────────────┼───────────────────────┐
       ▼                     ▼                       ▼
┌──────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ SOLICITATION │   │    EVIDENCE       │   │   GAP DETECTOR   │
│   ANALYST    │   │    RETRIEVER      │   │                  │
│              │   │                   │   │ Compares every   │
│ Parses RFP   │   │ Vector-searches   │   │ MUST / SHOULD    │
│ Extracts all │   │ past proposals +  │   │ requirement      │
│ compliance   │   │ org capability    │   │ against found    │
│ requirements │   │ docs for each     │   │ evidence.        │
│ → structured │   │ requirement       │   │                  │
│   matrix     │   │                   │   │ Flags: "Missing  │
│ (Excel)      │   │ Returns relevant  │   │ 2 case studies   │
│              │   │ text + metadata   │   │ in healthcare"   │
│              │   │ with source       │   │                  │
│              │   │ citations         │   │ Scores readiness │
└──────┬───────┘   └────────┬─────────┘   └────────┬─────────┘
       │                    │                      │
       └────────────────────┼──────────────────────┘
                            ▼
                  ┌──────────────────┐
                  │ COMPLIANCE       │
                  │   AUDITOR        │
                  │                  │
                  │ Scores proposal  │
                  │ against published│
                  │ evaluation rubric│
                  │ (from Excel)     │
                  │                  │
                  │ Prevents "auto-  │
                  │ rejection" gaps  │
                  │                  │
                  │ Output: scorecard│
                  │ + prioritized    │
                  │ action items     │
                  └────────┬─────────┘
                           ▼
                  ┌──────────────────┐
                  │  DRAFT ASSEMBLER │
                  │                  │
                  │ Generates full   │
                  │ proposal section │
                  │ with in-text     │
                  │ citations:       │
                  │ "As demonstrated │
                  │  in our DARPA    │
                  │  project [§3.2]" │
                  │                  │
                  │ Respects funder's│
                  │ page limits +    │
                  │ formatting rules │
                  └────────┬─────────┘
                           ▼
                  ┌──────────────────┐
                  │ BUDGET VALIDATOR │
                  │                  │
                  │ Cross-checks     │
                  │ budget Excel     │
                  │ against funder   │
                  │ cost categories, │
                  │ indirect rate    │
                  │ caps, and        │
                  │ allowable        │
                  │ expenses         │
                  └──────────────────┘
```

---

## Data Landscape — Tri-Modal RAG

### 1. Unstructured Documents (PDF/TXT) → Vector DB
| Category | Examples | Typical Volume |
|----------|----------|----------------|
| Grant Solicitations | NSF, NIH, DoD, EU Horizon, Gates Foundation RFPs | 50–200 pages each |
| Past Proposals | Winning + losing submissions from previous cycles | 15–40 pages each |
| Organizational Boilerplate | Capability statement, diversity plan, past performance narratives | 5–20 pages |
| Team Resumes/CVs | PI bios, key personnel qualifications | 2–3 pages each |
| Funder Guidelines | General application guides, FAQ documents | 10–30 pages |

### 2. Structured/Tabular Data (CSV/Excel) → SQLite
| Table | Columns | Purpose |
|-------|---------|---------|
| `compliance_matrix` | requirement_id, section_ref, type (MUST/SHOULD/MAY), description, evidence_doc, status | Single source of truth for compliance |
| `evaluation_rubric` | criterion, max_points, section, description | Models the funder's scoring system |
| `budget_template` | category, item, quantity, unit_cost, total, funder_allowed, notes | Budget with compliance flags |
| `deadline_calendar` | funder, program, deadline, cycle, eligibility, notes | Funder CRM |
| `evidence_registry` | doc_id, title, project_name, date, keywords, relevance_score | Index of reusable evidence |

### 3. Graph DB (Knowledge Graph)
```
Node Types:
  Funder → Program → Requirement → Evidence Document → Key Personnel
  Funder → Evaluation Criterion → Evidence Mapping
  Past Grantee → Award Amount → Project Type → Similarity Score → Our Org

Edge Types:
  - REQUIRES (Program → Requirement)
  - EVIDENCED_BY (Requirement → Evidence Document)
  - AUTHORED_BY (Evidence Document → Key Personnel)
  - SIMILAR_TO (Past Grantee → Our Org, with similarity metric)
  - PRECEDENT (Past Grantee → Award → sets precedent for budget range)
```

---

## Capstone Rubric Mapping (10 Tasks)

| # | Capstone Task | GrantLoom Implementation |
|---|---------------|---------------------------|
| 1 | Project foundation | ✅ Git repo, `.env`, `config.json`, `docker-compose.yml`, Streamlit + FastAPI scaffold |
| 2 | User interaction layer | ✅ Streamlit dashboard + FastAPI endpoints: upload docs, **free-form natural-language Q&A chat over all uploaded documents** (the core requirement), compliance scorecard, section drafting |
| 3 | Document ingestion | ✅ PDF parser (PyMuPDF), TXT reader, CSV/Excel handler with schema validation |
| 4 | Semantic chunking | ✅ Section-aware chunker for legal docs (split on § markers, preserve clause atomicity); budget-aware CSV parser |
| 5 | Vector store | ✅ ChromaDB for solicitation + past proposal + boilerplate embeddings; SQLite for structured data |
| 6 | Intelligent retrieval | ✅ Multi-strategy: vector similarity for narrative docs, SQL query for compliance/numbers, graph traversal for dependency chains |
| 7 | RAG pipeline | ✅ Retrieve → Augment → Generate with citation footnotes; document context window management |
| 8 | Agent reasoning | ✅ 6 specialized agents (Analyst, Retriever, Gap Detector, Auditor, Assembler, Budget Validator) with tool access |
| 9 | Safety & guardrails | ✅ Compliance hallucination check (is this claim backed by a source doc?), budget cap enforcement, prompt injection guard, input validation |
| 10 | Deploy & document | ✅ Docker, nginx reverse proxy; **documentation file covering setup, architecture, agent roles, deployment steps, limitations and challenges faced**; final **Zip** with source + docs per submission guidelines |

---

## Demo Flow (Capstone Showcase)

**Scenario:** A university research lab applies for a Department of Energy "Advanced Manufacturing" grant.

### Step 1: Upload
- `doe_advanced_mfg_2026.pdf` — 85-page DOE solicitation
- 5 past proposals from the lab (PDFs)
- Lab capability statement + team CVs (PDFs)
- Budget template (Excel)

### Step 2: Analyze
**Solicitation Analyst** extracts 47 compliance requirements, builds matrix:
```
§C.1 - Project Narrative (MUST) — 15 pages max
§C.2 - Management Plan (MUST) — 5 pages max  
§C.3 - Past Performance (MUST) — 3 case studies
§C.4 - Diversity & Inclusion Plan (MUST)
§C.5 - Budget Justification (MUST) — indirect rate ≤ 15%
§D.1 - Letters of Commitment (SHOULD) — 2 partners
... (41 more)
```

### Step 3: Retrieve & Detect Gaps
- **Evidence Retriever** finds evidence for 41/47 requirements
- **Gap Detector** flags 6 gaps:
  1. ❌ Missing: 2 case studies in additive manufacturing (only 1 found)
  2. ❌ Missing: Letter of commitment from a manufacturing partner
  3. ❌ Missing: Diversity plan (no document found)
  4. ⚠️ Weak: Management plan evidence is 5 years old
  5. ❌ Missing: Budget justification for equipment over $25K
  6. ⚠️ Warning: Indirect rate proposed at 18%, funder caps at 15%

### Step 4: Compliance Audit
**Compliance Auditor** estimates readiness against the (synthetic) evaluation rubric:
```
Criterion                  Max    Est.     Status
─────────────────────────────────────────────────
Technical Approach         25     22       ✅
Management Capability      15      9       ⚠️
Past Performance           20     15       ⚠️
Budget Reasonableness      15     10       ⚠️
Broader Impacts / Plan     10      0       ❌ (no document found)
Letters of Support         10      0       ❌
Format Compliance           5      5       ✅
─────────────────────────────────────────────────
TOTAL                     100     61       Below synthetic threshold (70)
```
> **Caveat:** real funders rarely publish point scores or pass thresholds (peer review decides). The score is an LLM-assisted *readiness estimate* against the mock rubric, shown with per-criterion rationale and citations. It is **not** "mathematically derived" and must be labeled as an estimate in the UI. The demo solicitation is synthetic; do not present the demo as a real DOE call. Funders also change mandatory sections (e.g., DEI-type plans were removed from many 2025 federal solicitations), so the requirement list must come from the uploaded solicitation, never from fixed assumptions.

### Step 5: Fill Gaps (Human in the Loop)
User addresses gaps: writes diversity plan, secures partner letters, adds case study — re-uploads.

### Step 6: Generate Draft
**Draft Assembler** generates a 22-page proposal with:
- Section-by-section narrative with `[1]`, `[2]` footnote citations
- Each claim traceable to a source document
- Page count warnings when sections exceed limits
- Budget table with compliance-verified line items

### Step 7: Budget Validation
**Budget Validator** cross-checks the Excel budget:
```
✅ Personnel costs within allowable range
⚠️ Equipment line "3D Printer - $45,000" exceeds $25K threshold → needs prior approval per §C.5.3
❌ Indirect cost rate 18% exceeds funder cap of 15% per §C.5.1 → must reduce to 15%
✅ Travel costs within GSA per diem rates
```

---

## Why This Is a Strong Capstone

### Technical Depth
- **True tri-modal RAG:** Vector search (narrative docs) + SQL queries (compliance, budget) + Graph traversal (requirement → evidence chains, precedent mapping)
- **Multi-agent with distinct tools:** Each agent has different retrieval strategies and prompts
- **Guardrails with measurement:** requirement coverage, citation verification, and budget rule checks are evaluated on a labeled test set (see `data-strategy.md`)
- **Citation system:** Every generated claim must reference a source chunk, mirroring real legal/contract RAG

### Differentiation from Your Previous Projects
| Previous Project | This Project |
|-----------------|--------------|
| `rag6-supply-chain` — Supply chain disruption intelligence, BOM graphs, hardware focus | **Grant writing** — completely different domain, document types, and user workflow |
| `flyrank-capstone-idea-lint` — Venture validation, adversarial debate, PRD generation | **Compliance-driven** — not adversarial brainstorming but evidence-to-requirement mapping |
| `cv-matcher` — Resume-to-job matching, structured data heavy | **Document generation** — this project *writes* documents, not just matches them |

### Showmanship
- "Before vs. After" scorecard is visually dramatic
- Real-time gap detection is satisfying to watch
- Generated proposal with citations demonstrates RAG grounding
- Budget violation flags are concrete and relatable

---

## Decisions & Open Questions (post-review)

1. **Name:** ✅ GrantLoom (minor conflict with a deadline-tracker app; acceptable for a capstone).
2. **Scope:** ✅ Phase the build (see below). Jev is kept as a core experiment (not dropped); the other stretch items stay optional.
3. **LLM models:** ✅ Implementation detail, fully configuration-driven. Provider: OpenRouter; model IDs per role (`writer`, `analyst`, `qa_judge`, embedding) in `config.json`, keys in `.env`. The **QA/judge model must be a different family than the writer**. See `data-strategy.md` 3.7.
3b. **Privacy vs performance:** ✅ Jev stays (best performance). Decision gates run behind a swappable backend: **Jev** (cloud) or **Julia-1** (local, open-weight, laptop CPU). Compare both in the evaluation.
3c. **Target segment:** ✅ Narrowed to research labs and research institutes (consultants secondary). Small no-IT nonprofits are out of scope.
4. **Data:** ✅ Real NOFO/solicitation PDFs from agency sites (NIH Guide, NSF) plus NIH sample applications; synthetic only for the fictional org's internal documents. See `data-strategy.md` for the correction about Grants.gov synopses.

### Phased Scope

| Phase | Deliverable | Covers capstone tasks |
|-------|-------------|-----------------------|
| 1 (must) | Ingestion → chunking → ChromaDB → RAG Q&A with citations; one planner/retriever/validator agent loop | 1–7 |
| 2 (must) | Solicitation Analyst (requirement matrix) + Evidence mapping + Gap report | 8 |
| 3 (must) | Budget Validator (4–6 deterministic rules) + guardrails + evaluation report | 9 |
| 4 (should) | Docker/nginx deploy, docs, Zip | 10 |
| 5 (should) | Jev gates behind `DecisionBackend` + Julia-1 local backend + Jev vs Julia-1 vs fallback comparison | 9 (strengthens) |
| 6 (stretch) | Draft Assembler, NetworkX traceability graph, RFP parser | — |

---

## Next Steps

1. ✅ Idea document written
2. ✅ Name chosen (GrantLoom); ⬜ create GitHub repo
3. ⬜ Write PRD with detailed agent prompts and architecture
4. ⬜ Generate synthetic dataset (solicitation PDFs, past proposals, budget Excel, org docs)
5. ⬜ Scaffold project (FastAPI + ChromaDB + SQLite + NetworkX + Streamlit)
6. ⬜ Implement document ingestion pipeline
7. ⬜ Build agent loop with tool definitions
8. ⬜ Build Streamlit UI with compliance dashboard
9. ⬜ Deploy to VPS (domain configured in `config.local.json`, not hardcoded)
10. ⬜ Write capstone submission documentation