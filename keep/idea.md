# 🏛️ Grant Writing & RFP Response Co-Pilot

**Status:** Idea → PRD phase
**Date:** 2026-10-12

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

Organizations constantly chase funding — a mid-size nonprofit submits **20–40 grant applications per year**. Each requires parsing a 50–200 page solicitation PDF, cross-referencing it against past proposals, financials, team resumes, and compliance documents. A single missed checkbox = automatic rejection. The work costs **40–80 hours per submission** and is soul-crushing, repetitive, and error-prone.

### The Unit Economics

| Metric | Value |
|--------|-------|
| Avg. grants submitted per org per year | 25 |
| Avg. hours per grant (manual) | 60 |
| Time saved by tool (conservative) | 30% |
| Hours saved per year | **450** |
| Dollar value ($75/hr loaded cost) | **$33,750/year** |
| Grants lost to compliance errors (industry est.) | 15–20% |

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

| # | Capstone Task | GrantForge Implementation |
|---|---------------|---------------------------|
| 1 | Project foundation | ✅ Git repo, `.env`, `config.json`, `docker-compose.yml`, Streamlit + FastAPI scaffold |
| 2 | User interaction layer | ✅ Streamlit dashboard: upload docs, define funder, ask questions, review compliance scorecard, generate sections |
| 3 | Document ingestion | ✅ PDF parser (PyMuPDF), TXT reader, CSV/Excel handler with schema validation |
| 4 | Semantic chunking | ✅ Section-aware chunker for legal docs (split on § markers, preserve clause atomicity); budget-aware CSV parser |
| 5 | Vector store | ✅ ChromaDB for solicitation + past proposal + boilerplate embeddings; SQLite for structured data |
| 6 | Intelligent retrieval | ✅ Multi-strategy: vector similarity for narrative docs, SQL query for compliance/numbers, graph traversal for dependency chains |
| 7 | RAG pipeline | ✅ Retrieve → Augment → Generate with citation footnotes; document context window management |
| 8 | Agent reasoning | ✅ 6 specialized agents (Analyst, Retriever, Gap Detector, Auditor, Assembler, Budget Validator) with tool access |
| 9 | Safety & guardrails | ✅ Compliance hallucination check (is this claim backed by a source doc?), budget cap enforcement, prompt injection guard, input validation |
| 10 | Deploy & document | ✅ Docker, nginx reverse proxy, README with architecture diagrams, agent role descriptions, limitations |

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
**Compliance Auditor** scores against DOE evaluation rubric:
```
Criterion                  Max    Scored   Status
─────────────────────────────────────────────────
Technical Approach         25     22       ✅
Management Capability      15      9       ⚠️
Past Performance           20     15       ⚠️
Budget Reasonableness      15     10       ⚠️
Diversity & Inclusion      10      0       ❌
Letters of Support         10      0       ❌
Format Compliance           5      5       ✅
─────────────────────────────────────────────────
TOTAL                     100     61       FAIL (min 70)
```

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
- **Genuine guardrails:** Compliance scoring isn't a stub — it's mathematically derived from rubric + evidence
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

## Open Questions

1. **Name:** GrantForge, ProposalCraft, DraftWright, or something else?
2. **Scope for prototype:** Full 6-agent pipeline, or reduce to 4 agents (merge Budget Validator into Compliance Auditor, merge Gap Detector into Analyst)?
3. **LLM provider:** OpenRouter (like previous projects) or direct Google Gemini (for ADK integration)?
4. **Data strategy:** Synthetic DOEsque solicitations (like rag6-supply-chain's synthetic data) or use real publicly available ones from grants.gov?

---

## Next Steps

1. ✅ Idea document written
2. ⬜ Choose name and create GitHub repo
3. ⬜ Write PRD with detailed agent prompts and architecture
4. ⬜ Generate synthetic dataset (solicitation PDFs, past proposals, budget Excel, org docs)
5. ⬜ Scaffold project (FastAPI + ChromaDB + SQLite + NetworkX + Streamlit)
6. ⬜ Implement document ingestion pipeline
7. ⬜ Build agent loop with tool definitions
8. ⬜ Build Streamlit UI with compliance dashboard
9. ⬜ Deploy to VPS (grantforge.taskmind-ai.com)
10. ⬜ Write capstone submission documentation