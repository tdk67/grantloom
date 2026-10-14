"""Lexical decision-backend test double (NOT production code).

GrantLoom's production decision backends are model-based only (llm_judge / jev); the old
HeuristicDecisionBackend was removed from app/ (review-04 A1, decisions-log D31) because a
keyword rule engine cannot judge meaning or catch paraphrased injections. Tests still need a
deterministic, offline decision backend, so its former implementation lives here as an explicit
test double with its own vocabulary. It must never be imported from app/.
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

from app.core.errors import DecisionBackendError
from app.core.text import content_tokens, lexicon_hits, numbers_in, overlap_ratio
from app.ports.decision_backend import DecisionBackendPort
from app.ports.models import (
    ChunkJudgement,
    ClaimJudgement,
    DocumentChunk,
    GateVerdict,
    RequirementAssessment,
)
from app.verification.injection_patterns import DOCUMENT_INJECTION_PATTERNS, INJECTION_PATTERNS

DOMAIN_TERMS = [
    "grant*",
    "fund*",
    "solicitation*",
    "nofo",
    "foa",
    "rfp",
    "rfa",
    "proposal*",
    "award*",
    "budget*",
    "cost share",
    "cost match*",
    "match*",
    "non-federal",
    "indirect",
    "nicra",
    "f&a",
    "salar*",
    "fringe benefit*",
    "personnel",
    "effort",
    "stipend*",
    "travel*",
    "equipment",
    "capital equipment",
    "expenditure*",
    "allowable",
    "eligib*",
    "applicant*",
    "apply",
    "prime",
    "recipient*",
    "subaward*",
    "subrecipient*",
    "subcontract*",
    "principal investigator",
    "investigator*",
    "co-pi",
    "pi",
    "postdoc*",
    "graduate student*",
    "researcher*",
    "lab",
    "labs",
    "laboratory",
    "laborator*",
    "facilit*",
    "institution*",
    "universit*",
    "department*",
    "organization*",
    "agency",
    "agencies",
    "federal",
    "doe",
    "nsf",
    "nih",
    "nasa",
    "usda",
    "dod",
    "darpa",
    "foundation",
    "complian*",
    "requirement*",
    "mandatory",
    "required",
    "regulation*",
    "polic*",
    "terms and conditions",
    "report*",
    "milestone*",
    "deliverable*",
    "go/no-go",
    "decision point*",
    "period of performance",
    "deadline*",
    "submission*",
    "submit*",
    "page limit*",
    "data management",
    "dmp",
    "public access",
    "research",
    "project*",
    "program*",
    "topic area*",
    "objective*",
    "aims",
    "specific aims",
    "scope of work",
    "cv",
    "curriculum vitae",
    "biosketch*",
    "publication*",
    "journal*",
    "paper*",
    "degree*",
    "ph.d.",
    "phd",
    "affiliat*",
    "collaborat*",
    "partner*",
    "team*",
    "consortium",
    "letter of support",
    "commitment letter*",
    "environmental questionnaire",
    "nepa",
    "categorical exclusion",
    "foreign",
    "export control*",
    "contracting officer",
    "prior approval",
    "rebudget*",
    "no-cost extension",
    "audit*",
    "spreadsheet*",
    "worksheet*"
]

ROUTE_TERMS = {
    "structured": [
        "budget*",
        "salar*",
        "total",
        "sum",
        "how much",
        "how many",
        "price*",
        "cost of",
        "costs",
        "expenditure*",
        "allocated",
        "budgeted",
        "spreadsheet*",
        "worksheet*",
        "travelers",
        "effort",
        "months of effort",
        "unit price",
        "amount*",
        "dollar*"
    ],
    "graph": [
        "who",
        "affiliat*",
        "co-investigator*",
        "co-pi",
        "collaborat*",
        "leads",
        "leading",
        "led by",
        "funded",
        "funder*",
        "which agency",
        "relationship*",
        "team",
        "works with",
        "partner*"
    ],
    "composite": [
        "compare",
        "reconcile",
        "cross-reference",
        "meets requirement",
        "matching funds",
        "cost share requirement",
        "verify budget against",
        "aligns with"
    ]
}


class StubDecisionBackend(DecisionBackendPort):
    """Deterministic regex + lexical-overlap verdicts for tests. Scores are overlap ratios, not
    calibrated probabilities; paraphrased injections are invisible to it (asserted in tests)."""

    name = "stub"

    GROUNDED_OVERLAP = 0.5
    SATISFIED_OVERLAP = 0.5
    PARTIAL_OVERLAP = 0.25
    CORPUS_IN_DOMAIN_OVERLAP = 0.5

    def __init__(self, corpus_overlap: Optional[Callable[[str], float]] = None) -> None:
        """corpus_overlap(query) -> best share of query terms found in one indexed chunk; lets
        questions about document content pass G1 without lexicon vocabulary."""
        self.corpus_overlap = corpus_overlap

    @property
    def supports_semantic_scan(self) -> bool:
        return False

    async def screen_query(self, query: str, api_key: Optional[str] = None) -> GateVerdict:
        injection = any(p.search(query) for p in INJECTION_PATTERNS)
        hits = lexicon_hits(query, DOMAIN_TERMS)
        corpus = self.corpus_overlap(query) if (self.corpus_overlap and not hits) else 0.0
        if hits:
            in_domain, confidence, reason = True, min(1.0, 0.5 + 0.1 * len(hits)), f"domain_terms={hits[:5]}"
        elif corpus >= self.CORPUS_IN_DOMAIN_OVERLAP:
            in_domain, confidence, reason = True, round(corpus, 2), f"corpus_overlap={corpus:.2f}"
        else:
            in_domain, confidence, reason = False, 0.7, f"no_domain_vocabulary (corpus_overlap={corpus:.2f})"
        return GateVerdict(
            gate_id="G1",
            passed=in_domain and not injection,
            confidence=confidence,
            reason=reason,
            metadata={"injection": injection, "in_domain": in_domain, "backend": self.name},
        )

    async def route_query(self, query: str, available_strategies: Sequence[str], api_key: Optional[str] = None) -> GateVerdict:
        scores = {
            strategy: len(lexicon_hits(query, terms))
            for strategy, terms in ROUTE_TERMS.items()
            if strategy in available_strategies
        }
        best = max(scores, key=lambda s: scores[s], default=None)
        if best is None or scores[best] == 0:
            return GateVerdict("G2", True, 0.0, "no_routing_cues", {"strategy": "hybrid", "backend": self.name})
        hits = scores[best]
        return GateVerdict(
            "G2", True, round(hits / (hits + 1), 2), f"{hits} {best} cue(s)",
            {"strategy": best, "backend": self.name},
        )

    async def judge_chunks(self, query: str, chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> list[ChunkJudgement]:
        judgements = []
        for c in chunks:
            ratio = overlap_ratio(query, c.text_content)
            relevance = "answers" if ratio >= 0.5 else "partial" if ratio > 0 else "irrelevant"
            injected = any(p.search(c.text_content) for p in DOCUMENT_INJECTION_PATTERNS)
            judgements.append(ChunkJudgement(c.chunk_id, relevance, 1.0 if injected else 0.0, round(ratio * 100, 1), self.name))
        return judgements

    async def verify_claims(self, claims: Sequence[tuple[str, Sequence[str], str]], api_key: Optional[str] = None) -> list[ClaimJudgement]:
        judgements = []
        for claim, cited, evidence in claims:
            if not evidence:
                judgements.append(ClaimJudgement(claim, list(cited), "unsupported", "no evidence cited", self.name))
                continue
            ratio = overlap_ratio(claim, evidence)
            missing_numbers = numbers_in(claim) - numbers_in(evidence)
            if missing_numbers:
                verdict, reason = "unsupported", f"numbers not in evidence: {sorted(missing_numbers)}"
            elif ratio >= self.GROUNDED_OVERLAP:
                verdict, reason = "grounded", f"lexical overlap {ratio:.2f}"
            else:
                verdict, reason = "unsupported", f"lexical overlap {ratio:.2f}"
            judgements.append(ClaimJudgement(claim, list(cited), verdict, reason, self.name))
        return judgements

    async def scan_for_injection(self, chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> dict[str, bool]:
        raise DecisionBackendError("The stub backend cannot perform a Tier-2 semantic scan.")

    async def assess_requirement(self, requirement_text: str, evidence_chunks: Sequence[DocumentChunk], api_key: Optional[str] = None) -> RequirementAssessment:
        if not content_tokens(requirement_text) or not evidence_chunks:
            return RequirementAssessment("Gap", None, "No lab evidence retrieved for this requirement.", self.name)
        best = max(evidence_chunks, key=lambda c: overlap_ratio(requirement_text, c.text_content))
        ratio = overlap_ratio(requirement_text, best.text_content)
        if ratio >= self.SATISFIED_OVERLAP:
            status = "Satisfied"
        elif ratio >= self.PARTIAL_OVERLAP:
            status = "Partial"
        else:
            return RequirementAssessment("Gap", None, f"Best lexical overlap {ratio:.2f} is below the partial threshold.", self.name)
        return RequirementAssessment(status, best.chunk_id, f"Lexical overlap {ratio:.2f} with lab evidence.", self.name)
