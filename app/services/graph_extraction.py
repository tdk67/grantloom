"""Generic, document-agnostic entity extraction for the KùzuDB graph (FR-S2).

Pattern-based (no model call, so ingestion works without credentials) and deliberately conservative:
- Person: names introduced by a title (Dr./Prof.), a project role (Principal Investigator, Co-PI, ...),
  a spreadsheet "Name:" field, or a trailing degree ("Jane Roe, Ph.D.").
- Funder: "<Organization Name> (ACRONYM)" where the name contains a funding-body keyword
  (Department, Foundation, Agency, Office, ...), plus bare acronyms of funders already in the graph.
- Project: explicit title fields ("Project Title: ...").
- Requirement: sentences containing requirement markers (must, shall, required, ...).
Relationships need provenance: every edge records the chunk it was extracted from. People and funders
are linked to a project only when the document describes exactly one project.
Known limitation: names without any of these cues are not extracted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from app.core.constants import ENGLISH_STOP_WORDS
from app.core.templates import load_json_template
from app.core.text import lexicon_hits, split_sentences
from app.ports.models import DocumentChunk, GraphEdge, GraphNode

NAME = r"[A-Z][a-z]+(?:[ ][A-Z]\.)?[ ][A-Z][a-zA-Z'\-]+"
TITLE = r"(?:Dr\.|Prof\.|Professor)"
ROLE = (
    r"(?:Co-Principal Investigator|Principal Investigator|Lead Investigator|Co-Investigator|Co-PI|"
    r"Project Director|Program Director)"
)
TITLED_PERSON_RE = re.compile(rf"\b{TITLE}[ ]({NAME})")
ROLE_PERSON_RE = re.compile(rf"\b{ROLE}(?:[ ]\(PI\))?[ ]*[:\-–—]?[ ]*(?:is[ ]+)?(?:{TITLE}[ ]+)?({NAME})")
FIELD_PERSON_RE = re.compile(rf"\bName:[ ]*(?:{TITLE}[ ]+)?({NAME})")
CREDENTIAL_PERSON_RE = re.compile(rf"\b({NAME}),[ ]+(?:Ph\.D\.|M\.D\.|M\.S\.|M\.Sc\.|M\.A\.|MBA|B\.S\.)")
ORG_ACRONYM_RE = re.compile(r"((?:[A-Z][a-zA-Z.]*[ ](?:of[ ](?:the[ ])?|for[ ]|and[ ])?){1,7}[A-Z][a-zA-Z.]*)[ ]\(([A-Z]{2,8})\)")
PROJECT_TITLE_RE = re.compile(r"(?:Project Title|Title of Project|Project Name)[ ]*:[ ]*([^\n]{8,160})")
REQUIREMENT_MAX_CHARS = 400


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


@dataclass
class _Extraction:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: dict[tuple[str, str, str], GraphEdge] = field(default_factory=dict)

    def node(self, node_id: str, label: str, **props: str) -> None:
        self.nodes.setdefault(node_id, GraphNode(node_id=node_id, label=label, properties=props))

    def edge(self, source: str, target: str, rel: str, chunk_id: str) -> None:
        self.edges.setdefault((source, target, rel), GraphEdge(source, target, rel, chunk_id))


class GraphEntityExtractor:
    def __init__(self) -> None:
        lexicon = load_json_template("decision/heuristic_lexicon.json")
        self.non_name_words = set(lexicon["non_name_words"])
        self.funder_keywords = set(lexicon["funder_keywords"])
        self.funding_cues = lexicon["funding_cues"]
        self.requirement_markers = lexicon["requirement_markers"]

    def _valid_name(self, name: str) -> bool:
        words = [w.strip(".").lower() for w in name.split()]
        return all(w not in ENGLISH_STOP_WORDS and w not in self.non_name_words for w in words)

    def people_in(self, text: str) -> dict[str, tuple[str, bool]]:
        """normalized name -> (display name, appears in a role/credential context)."""
        found: dict[str, tuple[str, bool]] = {}
        for regex, role_context in (
            (ROLE_PERSON_RE, True), (CREDENTIAL_PERSON_RE, True), (FIELD_PERSON_RE, True), (TITLED_PERSON_RE, False),
        ):
            for match in regex.finditer(text):
                name = match.group(1).strip()
                if not self._valid_name(name):
                    continue
                titled = re.search(rf"{TITLE}[ ]+{re.escape(name)}", text)
                display = f"{titled.group(0).split()[0]} {name}" if titled else name
                previous = found.get(name)
                found[name] = (display, role_context or (previous[1] if previous else False))
        return found

    def funders_in(self, text: str, known_funders: dict[str, str]) -> dict[str, str]:
        """acronym -> full name, from 'Name (ACR)' patterns and acronyms of already-known funders."""
        found: dict[str, str] = {}
        for match in ORG_ACRONYM_RE.finditer(text):
            name, acronym = re.sub(r"^The[ ]+", "", match.group(1).strip()), match.group(2)
            if set(name.split()) & self.funder_keywords:
                found[acronym] = name
        for acronym, name in known_funders.items():
            if acronym not in found and re.search(rf"\b{re.escape(acronym)}\b", text):
                found[acronym] = name
        return found

    def extract(
        self,
        doc_id: str,
        filename: str,
        chunks: Sequence[DocumentChunk],
        known_funders: dict[str, str],
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        out = _Extraction()
        out.node(doc_id, "Document", filename=filename)

        projects: dict[str, str] = {}
        role_people: dict[str, str] = {}
        funding_chunks: dict[str, str] = {}

        for c in chunks:
            text = c.text_content

            for match in PROJECT_TITLE_RE.finditer(text):
                title = match.group(1).strip().rstrip(".")
                pid = f"proj_{_slug(title)[:80]}"
                projects.setdefault(pid, c.chunk_id)
                out.node(pid, "Project", title=title)
                out.edge(doc_id, pid, "DESCRIBES", c.chunk_id)

            for name, (display, role_context) in self.people_in(text).items():
                person_id = f"person_{_slug(name)}"
                out.node(person_id, "Person", name=display)
                out.edge(doc_id, person_id, "MENTIONS", c.chunk_id)
                if role_context:
                    role_people.setdefault(person_id, c.chunk_id)

            funders = self.funders_in(text, known_funders)
            for acronym, name in funders.items():
                funder_id = f"funder_{_slug(acronym)}"
                out.node(funder_id, "Funder", name=f"{name} ({acronym})")
                out.edge(doc_id, funder_id, "MENTIONS_FUNDER", c.chunk_id)
                if lexicon_hits(text, self.funding_cues):
                    funding_chunks.setdefault(funder_id, c.chunk_id)

            for idx, sentence in enumerate(split_sentences(text)):
                if len(sentence) <= REQUIREMENT_MAX_CHARS and lexicon_hits(sentence, self.requirement_markers):
                    rid = f"req_{c.chunk_id}_{idx}"
                    out.node(rid, "Requirement", text=sentence)
                    out.edge(doc_id, rid, "REQUIRES", c.chunk_id)

        # A proposal describes one project: link its role-bearing people and its funders to it.
        if len(projects) == 1:
            (project_id,) = projects
            for person_id, chunk_id in role_people.items():
                out.edge(person_id, project_id, "AFFILIATED_WITH", chunk_id)
            for funder_id, chunk_id in funding_chunks.items():
                out.edge(funder_id, project_id, "FUNDS", chunk_id)

        return list(out.nodes.values()), list(out.edges.values())
