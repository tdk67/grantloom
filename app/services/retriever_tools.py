"""Discrete retriever tools beyond hybrid search (FR-A2, FR-S1, FR-S2).

Each tool returns evidence that reaches the writer and the G4 verifier:
- evidence_chunks: registry chunks the writer can cite (spreadsheet rows behind a SQL result,
  or the chunk an extracted graph relationship came from),
- evidence_text: the computed result itself (SQL rows, graph facts) for the writer prompt and for
  claim verification of computed values such as totals.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import GrantLoomError, SqlGuardError
from app.core.metrics import TOOL_INVOCATIONS
from app.core.templates import load_template, render_template
from app.core.text import TOKEN_RE, content_tokens
from app.ports.graph_store import GraphStorePort
from app.ports.llm_client import LLMClientPort
from app.ports.models import DocumentChunk
from app.ports.registry import RegistryPort
from app.ports.structured_store import StructuredStorePort, TableInfo
from app.verification.sql_guard import TABLE_REF_RE

logger = logging.getLogger("grantloom.services.retriever_tools")

SAMPLE_ROWS = 3


@dataclass
class ToolOutcome:
    tool_name: str
    arguments: dict[str, Any]
    status: str  # "ok", "skipped: <why>", "error: <why>"
    result_count: int = 0
    evidence_chunks: list[DocumentChunk] = field(default_factory=list)
    evidence_text: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    def finish(self, started: float) -> "ToolOutcome":
        self.duration_ms = round((time.perf_counter() - started) * 1000, 1)
        TOOL_INVOCATIONS.labels(tool=self.tool_name, outcome=self.status.split(":", 1)[0]).inc()
        return self


class _SqlPlan(BaseModel):
    sql: Optional[str] = None
    table: Optional[str] = None


def _active_doc_ids(registry: RegistryPort) -> dict[str, str]:
    return {d.doc_id: d.filename for d in registry.list_documents(state="active")}


class StructuredQueryTool:
    """sql_tabular_query: the analyst model writes a SELECT over spreadsheet schemas; SqlGuard executes it."""

    name = "sql_tabular_query"

    def __init__(
        self,
        structured_store: StructuredStorePort,
        registry: RegistryPort,
        llm_client: Optional[LLMClientPort],
        model: str,
        settings: Settings,
    ) -> None:
        self.structured_store = structured_store
        self.registry = registry
        self.llm_client = llm_client
        self.model = model
        self.max_tables = settings.retrieval.max_structured_tables
        self.max_chunks = settings.retrieval.max_tool_evidence_chunks

    def available(self) -> bool:
        return bool(self.structured_store.list_tables(list(_active_doc_ids(self.registry))))

    async def run(self, query: str, api_key: Optional[str] = None) -> ToolOutcome:
        started = time.perf_counter()
        outcome = ToolOutcome(self.name, {"query": query}, "ok")
        active = _active_doc_ids(self.registry)
        tables = self.structured_store.list_tables(list(active))[: self.max_tables]
        if not tables:
            outcome.status = "skipped: no spreadsheet tables in active documents"
            return outcome.finish(started)
        if self.llm_client is None:
            outcome.status = "skipped: no SQL generator model configured"
            return outcome.finish(started)

        schemas = "\n".join(self._describe(t, active[t.doc_id]) for t in tables)
        try:
            raw = await self.llm_client.generate_response(
                prompt=render_template("prompts/sql_generator.txt", schemas=schemas, query=query),
                model=self.model,
                system_prompt=load_template("prompts/judge_system.txt"),
                temperature=0.0,
                api_key=api_key,
                json_output=True,
            )
            start, end = raw.find("{"), raw.rfind("}")
            plan = _SqlPlan.model_validate(json.loads(raw[start : end + 1])) if start >= 0 else _SqlPlan()
        except GrantLoomError as exc:
            outcome.status = f"error: SQL generation failed ({exc.detail})"
            return outcome.finish(started)
        except (ValidationError, json.JSONDecodeError) as exc:
            # Model output fragments stay server-side; the tool status reaches the client (review-04 C1).
            logger.error("SQL generation returned an unparseable plan: %s", exc)
            outcome.status = "error: SQL generation returned an unparseable plan"
            return outcome.finish(started)

        if not plan.sql:
            outcome.status = "skipped: model judged the tables unable to answer"
            return outcome.finish(started)
        outcome.arguments["sql"] = plan.sql

        allowed = [t.table_name for t in tables]
        try:
            rows = self.structured_store.execute_query(plan.sql, allowed_tables=allowed)
        except SqlGuardError as exc:
            outcome.status = f"error: rejected by SQL guard ({exc.detail})"
            return outcome.finish(started)

        outcome.result_count = len(rows)
        if not rows or all(v is None for row in rows for k, v in row.items() if k != "row_number"):
            # An empty / all-NULL result is "no answer", not evidence; passing it on misleads the writer.
            outcome.status = "ok: empty result"
            return outcome.finish(started)
        referenced = {name.lower() for name in TABLE_REF_RE.findall(plan.sql)}
        used = [t for t in tables if t.table_name.lower() in referenced]
        outcome.evidence_chunks = self._provenance(used, rows)
        sheets = ", ".join(f"sheet '{t.sheet_name}' of {active[t.doc_id]}" for t in used) or "spreadsheet tables"
        outcome.evidence_text.append(
            f"Structured query over {sheets}. SQL: {plan.sql}\nResult rows: {json.dumps(rows[:20], default=str)}"
        )
        return outcome.finish(started)

    def _describe(self, table: TableInfo, filename: str) -> str:
        """Schema line plus a few sample rows so the model can match how text values are written."""
        columns = ", ".join(f'"{c["name"]}" {c["type"]}' for c in table.columns)
        samples = self.structured_store.execute_query(
            f'SELECT * FROM "{table.table_name}" LIMIT {SAMPLE_ROWS}', allowed_tables=[table.table_name]
        )
        return (
            f'- "{table.table_name}" (sheet "{table.sheet_name}" of {filename}, {table.row_count} rows): {columns}\n'
            f"  sample rows: {json.dumps(samples, default=str)}"
        )

    def _provenance(self, used: list[TableInfo], rows: list[dict[str, Any]]) -> list[DocumentChunk]:
        """Chunks behind a SQL result: row-number matches first; for computed/aggregate results
        (no row numbers) the chunks whose text actually contains the result values, so a citation
        for e.g. a SUM shows the amount it came from."""
        row_numbers = {int(r["row_number"]) for r in rows if isinstance(r.get("row_number"), int)}
        values = self._result_values(rows)
        row_matches: list[DocumentChunk] = []
        value_matches: list[DocumentChunk] = []
        for table in used:
            for chunk in self.registry.get_chunks_for_document(table.doc_id):
                if chunk.sheet_name != table.sheet_name or chunk.row_start is None:
                    continue
                if row_numbers and any(chunk.row_start <= n <= (chunk.row_end or chunk.row_start) for n in row_numbers):
                    row_matches.append(chunk)
                elif values and any(v in chunk.text_content for v in values):
                    value_matches.append(chunk)
        return (row_matches or value_matches)[: self.max_chunks]

    @staticmethod
    def _result_values(rows: list[dict[str, Any]]) -> set[str]:
        """Plain and thousands-separated renderings of numeric results (values < 100 are skipped:
        they are counts, not identifying amounts, and would match almost any chunk)."""
        values: set[str] = set()
        for row in rows:
            for key, v in row.items():
                if key == "row_number" or isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                if float(v) == int(v):
                    n = abs(int(v))
                    if n >= 100:
                        values.add(str(int(v)))
                        values.add(f"{int(v):,}")
                elif abs(v) >= 100:
                    values.add(f"{v:g}")
                    values.add(f"{v:,.2f}".rstrip("0").rstrip("."))
        return values


class GraphQueryTool:
    """graph_traverse_query: links entities named in the question to parameterized Cypher templates."""

    name = "graph_traverse_query"
    ENTITY_LISTS = {"person": "list_people", "funder": "list_funders", "project": "list_projects"}
    TRAVERSALS = {
        "person": ["projects_by_person"],
        "project": ["people_by_project", "funders_by_project"],
        "funder": ["projects_by_funder"],
    }

    def __init__(self, graph_store: GraphStorePort, registry: RegistryPort, settings: Settings) -> None:
        self.graph_store = graph_store
        self.registry = registry
        self.max_chunks = settings.retrieval.max_tool_evidence_chunks

    def _entities(self) -> list[tuple[str, str, str, str]]:
        """(kind, entity_id, name, mention_chunk_id) for entities mentioned by active documents."""
        active = _active_doc_ids(self.registry)
        seen: dict[str, tuple[str, str, str, str]] = {}
        for kind, template in self.ENTITY_LISTS.items():
            for row in self.graph_store.query_template(template, {}):
                if row["source_chunk_id"].split(":", 1)[0] in active:
                    seen.setdefault(row["entity_id"], (kind, row["entity_id"], row["name"], row["source_chunk_id"]))
        return list(seen.values())

    @staticmethod
    def _matches(kind: str, name: str, query: str) -> bool:
        """Strict entity-to-query linking (review-04 C3): token matches and high overlap only.
        Loose substring matching produced false graph evidence for short names ("Office", "Award")."""
        q_tokens = set(content_tokens(query))
        q_words = set(TOKEN_RE.findall(query.lower()))  # keeps stop words; strips punctuation
        if kind == "person":
            parts = [p for p in name.replace("Dr.", "").replace("Prof.", "").split() if len(p) >= 3]
            return bool(parts) and (parts[-1].lower() in q_tokens or " ".join(parts).lower() in query.lower())
        if kind == "funder":
            acronym = name.rsplit("(", 1)[-1].rstrip(")") if "(" in name else ""
            return bool(acronym) and acronym.lower() in q_words
        title_tokens = set(content_tokens(name))
        if not title_tokens:
            return False
        overlap = len(title_tokens & q_tokens)
        # >= 3 shared tokens, or a high shared ratio with at least 2 tokens (a one-word title like
        # "Award" must not match every query containing that word).
        return overlap >= 3 or (overlap >= 2 and overlap / len(title_tokens) >= 0.6)

    def run(self, query: str) -> ToolOutcome:
        started = time.perf_counter()
        outcome = ToolOutcome(self.name, {"query": query, "templates": []}, "ok")
        try:
            matched = [e for e in self._entities() if self._matches(e[0], e[2], query)]
            if not matched:
                # Never enumerate indexed entity names here: tool status reaches the client (review-04 C3).
                outcome.status = "skipped: no graph entity matched the query"
                return outcome.finish(started)
            chunk_ids: list[str] = []
            for kind, entity_id, name, mention_chunk in matched:
                chunk_ids.append(mention_chunk)
                for template in self.TRAVERSALS[kind]:
                    outcome.arguments["templates"].append({"template": template, "entity_id": entity_id})
                    for row in self.graph_store.query_template(template, {"entity_id": entity_id}):
                        outcome.result_count += 1
                        chunk_ids.append(row["source_chunk_id"])
                        fact = ", ".join(f"{k}={v}" for k, v in row.items() if k != "source_chunk_id")
                        outcome.evidence_text.append(f"Graph fact ({template}): {fact} [source {row['source_chunk_id']}]")
        except GrantLoomError as exc:
            outcome.status = f"error: {exc.detail}"
            return outcome.finish(started)

        active = _active_doc_ids(self.registry)
        for cid in dict.fromkeys(chunk_ids):
            chunk = self.registry.get_chunk(cid)
            if chunk is not None and chunk.doc_id in active:
                outcome.evidence_chunks.append(chunk)
        outcome.evidence_chunks = outcome.evidence_chunks[: self.max_chunks]
        return outcome.finish(started)
