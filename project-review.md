# GrantLoom — Critical Review (Business Owner + AI Engineer)

**Date:** 2026-10-12  
**Scope:** `idea.md`, `market-research.md`, `data-strategy.md` against `project-requirements.txt`

---

## 1. Verdicts

| Question | Verdict |
|----------|---------|
| Is the **idea** novel? | **Low–moderate.** Compliance matrices, NOFO parsing, and AI grant drafting already exist (Grant Assistant, ResearchGrants.ai, RFEase, Granted AI, DeepRFP, Continua). |
| Is the **solution approach** novel? | **Moderate, if measured.** Evidence-to-requirement audit, budget check against caps read from the solicitation, and claim-level citation verification are a sound combination. "6 agents" and "tri-modal RAG" are architecture, not customer value. |
| Does the **niche** justify building a product? | **Only if narrowed.** Viable as a *pre-submission compliance auditor for research labs / consultants*. Not viable as a general "AI grant writer": the free substitute (a general LLM with the NOFO uploaded) is strong. |
| Does it fit the **capstone**? | **Yes**, and it is a strong demo. Main risk is scope, not fit. |

---

## 2. Assumptions Challenged

| Original claim | Problem | Fix applied |
|----------------|---------|-------------|
| "Nobody" has multi-agent, budget checks, tri-modal RAG | Not verifiable; vendors market "agents"; ERA tools (Cayuse, Streamlyne, InfoEd) do budget math | Rewritten as "not found in public material"; gaps re-scoped |
| Competitor list complete | Missed Loopio, Responsive, Grant Assistant (FreeWill), Granted AI, ResearchGrants.ai, ERA tools, general LLMs | Added table |
| GrantLoom has "no conflicts" | App "Grantloom: Deadline Desk" exists | Noted; acceptable for capstone |
| Primary persona: small nonprofit with no grant writer | Contradicts Docker self-hosting; also small nonprofits submit few grants | Persona changed to research labs / consultants |
| TAM from $500B grant spend and $700B RFP spend | Funder spend ≠ software market | Replaced with price-point × paying teams; interviews recommended |
| 15–20% of grants lost to compliance errors | Unsourced | Removed |
| 25 grants/org, 30% time saved | Skewed, unvalidated | Labeled as assumptions |
| Compliance score "mathematically derived", FAIL at 70 | Funders use peer review; no published pass marks; scores are LLM-assisted | Relabeled as readiness estimate |
| Mandatory Diversity & Inclusion plan in demo | Removed from many 2025 federal calls | Demo uses a generic plan criterion; requirements must come from the uploaded document |
| Grants.gov gives 50 solicitation PDFs of ~15 pages | API returns synopses; full NOFO is a separate attachment | Source changed to full-text NOFOs; 25 docs, totals recomputed (80 docs, ~1,216 pages) |
| LLM-generated data + LLM judge | Circular; inflated scores | Planted errors, real-NOFO hand labels, different judge model, split reporting |
| Compliance accuracy ≥ 0.95, grid of 192 configs | Unrealistic / costly | Recall/precision targets; staged tuning |
| Jev private-verdict gates | Sends text to a third party (conflicts with privacy wedge), 27 calls/query latency, vendor claims unverified | **Decision: keep Jev.** Mitigated by a swappable `DecisionBackend` with local open-weight **Julia-1**, batched calls, and a measured Jev vs Julia-1 comparison |
| Gap 7 "capstone niche" | A course rubric is not a market gap | Removed |

---

## 3. Requirements Traceability

| # | Requirement (`project-requirements.txt`) | Covered by | Status |
|---|------------------------------------------|-----------|--------|
| 1 | Project foundation | Repo, `.env`, `config.json`, scaffold | ✅ |
| 2 | Interaction layer (upload + NL questions) | Streamlit + FastAPI; **free-form Q&A made explicit** | ✅ (was implicit) |
| 3 | Ingestion PDF/TXT/CSV/Excel | PyMuPDF, text, pandas/openpyxl | ✅ |
| 4 | Chunking | Section-aware chunker | ✅ |
| 5 | Vector store | ChromaDB (+ SQLite) | ✅ |
| 6 | Retrieval | Vector + SQL (graph optional) | ✅ |
| 7 | RAG pipeline | Retrieve → augment → generate with citations | ✅ |
| 8 | Agent reasoning: plan, retrieve, reason, validate | Analyst / Retriever / Auditor / Budget Validator; **plan→retrieve→reason→validate loop required for general Q&A too** | ✅ (clarified) |
| 9 | Reliability and safety | Input validation, injection guard, citation check, error handling | ✅ |
| 10 | Deploy and document | Docker/nginx; **docs with setup, architecture, agent roles, deployment, limitations, challenges; Zip** | ✅ (was incomplete) |

---

## 4. Remaining Risks

1. **Scope:** 6 agents + 3 stores + Jev + RAGAS + Docker is too much for one capstone. Follow the phased plan in `idea.md`; graph, drafting, and RFP parsing are stretch. Jev gates are Phase 5 (kept, after the core works). Models are config-driven; the QA model differs from the writer model.
2. **Demo realism:** the headline demo uses synthetic data. Keep at least 3–5 real NOFOs in the evaluation so claims are not circular.
3. **LLM extraction accuracy on real NOFOs** is the real technical risk (requirement extraction recall). Measure it early.
4. **Legal/ethical:** a readiness estimate must not be presented as a prediction of funding.
5. **Unvalidated demand:** run 5–10 short interviews with research administrators before any commercial claim.

## 5. Recommended Next Steps

1. Pick the LLM and embedding provider; put names in `config.json`, keys in `.env`.
2. Collect 5 real NOFOs and hand-label their MUST requirements (build the test set first).
3. Build Phase 1 (RAG Q&A with citations) end to end, then measure.
4. Decide at the Phase 2 checkpoint whether to attempt stretch items.
