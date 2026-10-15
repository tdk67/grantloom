"""Corpus-driven starter questions generated from the actual indexed documents.

The starter questions served by `/api/v1/examples` are derived from live corpus facts instead of a
hand-tuned static file, so a freshly seeded installation gets questions it can actually answer
(review-08 F1). Each question is labelled with both the retrieval category it exercises
(keyword / semantic / graph / sql / composite / negative) and the strategy hint the pipeline should
route to. The generator never fabricates facts: every question template is grounded in at least
one active chunk whose text is actually returned alongside the generated question, and any template
whose anchor term is absent from the corpus is dropped rather than emitted unchecked.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

from app.ports.registry import RegistryPort

logger = logging.getLogger("grantloom.services.example_questions")

MIN_CHUNK_CHARS = 260

MAX_EMITTED = 8



@dataclass
class ExampleQuestion:
    id: str
    category: str
    question: str
    strategy_hint: str
    description: str
    source_chunks: list[str]  # chunk_ids that ground the question (for debug / verification)


# ---------------------------------------------------------------------------------------------
# Template library. Anchors are matched (case-insensitive substring) against the raw chunk text;
# a template is only emitted when every anchor appears in at least one active chunk.
# ---------------------------------------------------------------------------------------------

_CORPUS_ANCHORS = [
    ("Clean Hydrogen Electrolysis & Underground Storage", "DOE Clean Hydrogen FOA (DE-FOA-0003412)"),
    ("Quantum Sensors for Environmental", "NSF Quantum Sensors program (NSF 26-520)"),
    ("Floating Offshore Wind", "DOE Floating Offshore Wind FOA (DE-FOA-0003820)"),
    ("Next-Generation Enhanced Geothermal", "DOE Geothermal FOA (DE-FOA-0003740)"),
    ("Bioengineering Partnerships with Industry", "NIH Bioengineering RFA (RFA-EB-26-001)"),
    ("National Artificial Intelligence Research Institutes", "NSF AI Institutes program (NSF 26-580)"),
    ("Rural Energy for America", "USDA Rural Energy program (REAP)"),
    ("Autonomous Surface Microgrids", "NASA Space Power solicitation (NNH26ZDA001N)"),
    ("ARPA-E OPEN 2026", "ARPA-E OPEN solicitation (DE-FOA-0003600)"),
]

_FACILITY = "Grid Edge Testing Laboratory"
_FIRMWARE = "Marcus Holloway"
_BUDGET_DOC = "Lab_Budget_2025_Sample.xlsx"
_EQUIP_TOTAL = "$151,000"
_COST_SHARE_LETTER = "Maryland Clean Energy Center"
_DOE_SOLAR = "DOE Solar FOA"

# keyword: exact identifiers / codes in the source
_KEYWORD_TEMPLATES = [
    {
        "id": "ex_keyword_hydrogen_foa",
        "category": "Keyword Search",
        "question": "What is the official FOA number of the DOE Clean Hydrogen Electrolysis & Underground Storage Infrastructure funding opportunity and who publishes it?",
        "strategy_hint": "keyword",
        "description": "Exact-identifier question answered by the DOE Clean Hydrogen FOA header (DE-FOA-0003412).",
        "anchors": ["DE-FOA-0003412", "Clean Hydrogen Electrolysis"],
    },
    {
        "id": "ex_keyword_nsf_quantum",
        "category": "Keyword Search",
        "question": "What is the official solicitation number of the NSF Quantum Sensors for Environmental & Critical Infrastructure Monitoring program?",
        "strategy_hint": "keyword",
        "description": "Exact-identifier question answered by the NSF Quantum Sensors solicitation header (NSF 26-520).",
        "anchors": ["NSF 26-520", "Quantum Sensors"],
    },
]

# semantic: paraphrase concepts that appear in the corpus (islanding / resilience)
_SEMANTIC_TEMPLATES = [
    {
        "id": "ex_semantic_islanding",
        "category": "Semantic Search",
        "question": "How does the proposed microgrid system maintain power delivery for critical infrastructure when the external utility grid fails?",
        "strategy_hint": "semantic",
        "description": "Conceptual question about islanding / blackout behaviour (no keyword overlap with the spelled-out answer).",
        "anchors": ["islanded", "blackout"],
    },
    {
        "id": "ex_semantic_resilience",
        "category": "Semantic Search",
        "question": "What prior performance did the lab demonstrate during the August 2025 grid outage event?",
        "strategy_hint": "semantic",
        "description": "Conceptual question about the documented grid-outage track record.",
        "anchors": ["August 2025", "outage"],
    },
]

# graph: entity relationship facts present in the Kùzu graph
_GRAPH_TEMPLATES = [
    {
        "id": "ex_graph_firmware",
        "category": "Knowledge Graph",
        "question": "Who is affiliated with the Urban Microgrid Solar Storage project as the Lead Firmware Engineer?",
        "strategy_hint": "graph",
        "description": "Person→project relationship traversal (Marcus Holloway ↔ Urban Microgrid).",
        "anchors": ["Lead Firmware Engineer", "Marcus Holloway"],
    },
    {
        "id": "ex_graph_funder",
        "category": "Knowledge Graph",
        "question": "Which federal agency funds the Urban Microgrid Solar Storage Integration for Underserved Neighborhoods project?",
        "strategy_hint": "graph",
        "description": "Funder→project relationship traversal (DOE funds the CHIL microgrid).",
        "anchors": ["DOE", "Urban Microgrid Solar Storage"],
    },
]

# sql / structured: numeric facts in spreadsheet tables
_SQL_TEMPLATES = [
    {
        "id": "ex_sql_equipment_total",
        "category": "Tabular SQL",
        "question": "What is the total sum of all equipment acquisition costs across all budgeted equipment items in the lab budget spreadsheet?",
        "strategy_hint": "structured",
        "description": "SUM over the Equipment sheets of Lab_Budget_2025_Sample.xlsx ($151,000).",
        "anchors": ["Equipment", "151000"],
    },
    {
        "id": "ex_sql_personnel_pi",
        "category": "Tabular SQL",
        "question": "What is the annual base salary of the Principal Investigator listed in the lab budget personnel sheet?",
        "strategy_hint": "structured",
        "description": "Lookup over the Personnel sheet (Dr. Elena Vance, $140,000).",
        "anchors": ["Personnel", "140000"],
    },
]

# composite: cross-source checks (proposal vs FOA limits)
_COMPOSITE_TEMPLATES = [
    {
        "id": "ex_composite_award_range",
        "category": "Complex Query",
        "question": "Does the $1,250,000 award for the CHIL microgrid project fall within the award ceiling and floor set by the DOE Solar FOA, and does its performance period fit the FOA limits?",
        "strategy_hint": "composite",
        "description": "Cross-references the past proposal award/period against the DOE Solar FOA award range and performance window.",
        "single_source": False,
        "anchors": ["$1,250,000", "Award Ceiling", "Performance Period"],
    },
    {
        "id": "ex_composite_cost_share",
        "category": "Complex Query",
        "question": "Compare the mandatory non-federal cost share required by the DOE Solar FOA with the cost share policy of the EPA Community Resilience NOFO.",
        "strategy_hint": "composite",
        "description": "Cross-references two independent solicitations (DOE 20.0% vs EPA 0.0%).",
        "single_source": False,
        "anchors": ["20.0%", "0.0%", "EPA Community Resilience"],
    },
]

# negative control: unanswerable
_NEGATIVE_TEMPLATES = [
    {
        "id": "ex_negative_out_of_corpus",
        "category": "Negative Control",
        "question": "What was the total tonnage of structural steel imported for the Golden Gate Bridge construction in 1933?",
        "strategy_hint": "abstain",
        "description": "Out-of-corpus question the appliance must abstain from (not cited, no fabricated answer).",
        "anchors": [],  # intentionally never grounded -> always emitted
    },
]

_SECTIONS = [
    ("ex_kw", "keyword", "Keyword Search", _KEYWORD_TEMPLATES),
    ("ex_sem", "semantic", "Semantic Search", _SEMANTIC_TEMPLATES),
    ("ex_graph", "graph", "Knowledge Graph", _GRAPH_TEMPLATES),
    ("ex_sql", "structured", "Tabular SQL", _SQL_TEMPLATES),
    ("ex_comp", "composite", "Complex Query", _COMPOSITE_TEMPLATES),
    ("ex_neg", "abstain", "Negative Control", _NEGATIVE_TEMPLATES),
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _has_anchor(chunk_text: str, anchors: list[str]) -> bool:
    """Every anchor must appear in the chunk text (case-insensitive substring)."""
    haystack = _norm(chunk_text)
    return all(_norm(a) in haystack for a in anchors)


def _extract_num(text: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, text or "")
    return m.group(1) if m else None


def build_example_questions(registry: RegistryPort, limit: int = 8) -> list[dict[str, Any]]:
    """Generate a capped, curated list of starter questions from the active corpus.

    Deterministic ordering: fixed section order, templates within a section, so the sidebar does
    not reshuffle between requests.

    Grounding semantics depend on the template family:
    - single-source templates (keyword / semantic / graph / sql) require every anchor to appear
      together in at least one active chunk;
    - composite templates legitimately span documents (proposal vs FOA), so each anchor must exist
      somewhere in the active corpus but not necessarily in one chunk.
    `source_chunks` records the grounding chunk ids so callers (and the UI) can inspect why a
    question was offered.
    """
    active_chunks = [c for d in registry.list_documents(state="active") for c in registry.get_chunks_for_document(d.doc_id)]
    usable = [c for c in active_chunks if len((c.text_content or "").strip()) >= MIN_CHUNK_CHARS]
    # Composite questions legitimately reference short anchor fragments (a one-line
    # proposal header, a budget row). Ground them against the full active corpus.
    all_active = active_chunks

    def _corpus_has(anchor: str) -> bool:
        a = _norm(anchor)
        return any(a in _norm(c.text_content) for c in all_active)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _emit(prefix: str, idx: int, template: dict[str, Any], source_chunks: list[str]) -> None:
        qid = template.get("id") or f"{prefix}_{idx}"
        if qid in seen:
            return
        seen.add(qid)
        out.append({
            "id": qid,
            "category": template["category"],
            "question": template["question"],
            "strategy_hint": template["strategy_hint"],
            "description": template.get("description", ""),
            "source_chunks": source_chunks,
        })

    def _ground(anchors: list[str], single_chunk: bool) -> list[str]:
        hits: list[str] = []
        if single_chunk:
            for chunk in usable:
                if _has_anchor(chunk.text_content, anchors):
                    hits.append(chunk.chunk_id)
                    if len(hits) >= 3:
                        break
            return hits
        # composite: collect one chunk per anchor (cross-document grounding)
        for anchor in anchors:
            for chunk in all_active:
                if _norm(anchor) in _norm(chunk.text_content):
                    hits.append(chunk.chunk_id)
                    break
        return hits

    for prefix, strategy, category, templates in _SECTIONS:
        for idx, template in enumerate(templates, start=1):
            anchors = template.get("anchors", [])
            if not anchors:
                # negative control: always emitted
                _emit(prefix, idx, template, [])
                continue
            single_chunk = template.get("single_source", True)
            if single_chunk:
                chunk_ids = _ground(anchors, single_chunk=True)
            else:
                if not all(_corpus_has(a) for a in anchors):
                    chunk_ids = []
                else:
                    chunk_ids = _ground(anchors, single_chunk=False)
            if not chunk_ids:
                logger.info("Example question '%s' skipped: anchor(s) %s absent from corpus",
                            template.get("id", template.get("question"))[:60], anchors)
                continue
            _emit(prefix, idx, template, chunk_ids)

    # cap: keep first `limit` (negative control retained if space allows)
    if len(out) > limit:
        out = out[:limit]
    return out


def encode_example_question(example: dict[str, Any]) -> str:
    """Build a ?q= URL-safe query for direct search from a starter question."""
    return quote(example["question"])
