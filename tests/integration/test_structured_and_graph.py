"""Structured spreadsheet tables + SQL guard, KùzuDB graph, and generic graph extraction."""

from __future__ import annotations

import gc
import sqlite3
from pathlib import Path

import pytest

from app.adapters.kuzu_adapter import KuzuGraphStoreAdapter
from app.core.errors import SqlGuardError, StorageError
from app.ports.models import DocumentChunk, GraphEdge, GraphNode
from app.services.graph_extraction import GraphEntityExtractor
from app.verification.sql_guard import SqlGuard

GUARD = SqlGuard(max_rows=100, timeout_seconds=5.0)


# ------------------------------------------------------------------ SQL guard

def test_sql_guard_blocks_multi_statement_writes_and_system_tables():
    with pytest.raises(SqlGuardError, match="Multiple SQL statements"):
        GUARD.validate_and_sanitize("SELECT * FROM sqlite_master; DROP TABLE documents;")
    for q in ["DROP TABLE users;", "DELETE FROM budget WHERE amount > 0;", "PRAGMA table_info('budget');",
              "INSERT INTO budget VALUES (1, 'hack', 9999);", "ALTER TABLE budget ADD COLUMN pw TEXT;"]:
        with pytest.raises(SqlGuardError):
            GUARD.validate_and_sanitize(q)
    for q in ["SELECT * FROM sqlite_master", "SELECT version_num FROM alembic_version",
              "SELECT * FROM audit_logs", "SELECT text_content FROM document_chunks", "SELECT * FROM documents"]:
        with pytest.raises(SqlGuardError):
            GUARD.validate_and_sanitize(q)


def test_sql_guard_allow_list_cannot_be_bypassed_with_quoted_identifiers():
    with pytest.raises(SqlGuardError, match="not in the allowed tables"):
        GUARD.validate_and_sanitize('SELECT * FROM "other_table"', allowed_tables={"doc_a_travel"})
    with pytest.raises(SqlGuardError, match="not in the allowed tables"):
        GUARD.validate_and_sanitize("SELECT * FROM [other_table]", allowed_tables={"doc_a_travel"})


def test_sql_guard_authorizer_denies_reads_outside_allow_list(tmp_path: Path):
    conn = sqlite3.connect(tmp_path / "x.db")
    conn.execute("CREATE TABLE allowed_t (v INTEGER)")
    conn.execute("CREATE TABLE secret_t (v INTEGER)")
    conn.commit()
    # A comma join hides the second table from the FROM/JOIN regex; the SQLite authorizer still refuses it.
    with pytest.raises(SqlGuardError, match="authorization"):
        GUARD.execute_safe(conn, "SELECT * FROM allowed_t, secret_t", allowed_tables={"allowed_t"})
    conn.close()


def test_sql_guard_enforces_limit_cap():
    assert "LIMIT 100" in GUARD.validate_and_sanitize("SELECT * FROM doc_123_travel")
    capped = GUARD.validate_and_sanitize("SELECT * FROM doc_123_travel LIMIT 5000")
    assert "LIMIT 100" in capped and "5000" not in capped
    assert "LIMIT 10" in GUARD.validate_and_sanitize("SELECT * FROM doc_123_travel LIMIT 10")


# ------------------------------------------------------------------ structured store

def test_structured_store_load_query_and_purge(stack):
    store = stack.structured_store
    table = store.load_sheet("doc_abc", "Travel", ["Trip_Description", "Travelers", "Cost"], [
        (2, ["DOE PI Meeting (Washington DC)", 2, 2800]),
        (3, ["IEEE PVSC Conference (Austin TX)", 3, 4200]),
        (4, ["Field Testing Site Visit (Nevada)", 1, 1500]),
    ])
    info = store.list_tables(["doc_abc"])[0]
    assert (info.table_name, info.doc_id, info.sheet_name, info.row_count) == (table, "doc_abc", "Travel", 3)
    assert {"row_number", "trip_description", "cost"} <= {c["name"] for c in info.columns}

    assert store.execute_query(f'SELECT SUM(cost) AS total FROM "{table}"')[0]["total"] == 8500
    rows = store.execute_query(f'SELECT row_number, cost FROM "{table}" ORDER BY row_number')
    assert [r["row_number"] for r in rows] == [2, 3, 4]

    # Only registered spreadsheet tables are queryable, never registry tables
    with pytest.raises(SqlGuardError):
        store.execute_query("SELECT * FROM structured_tables")

    store.delete_document_tables("doc_abc")
    assert store.list_tables(["doc_abc"]) == []
    with pytest.raises(SqlGuardError):
        store.execute_query(f'SELECT * FROM "{table}"')


# ------------------------------------------------------------------ KùzuDB

def test_kuzudb_templates_provenance_and_document_purge(tmp_path: Path):
    graph = KuzuGraphStoreAdapter(str(tmp_path / "kuzu"))
    try:
        graph.add_nodes([
            GraphNode("doc_a", "Document", {"filename": "a.pdf"}),
            GraphNode("doc_b", "Document", {"filename": "b.pdf"}),
            GraphNode("person_x", "Person", {"name": "Dr. Jane Roe"}),
            GraphNode("proj_p", "Project", {"title": "Coastal Sensor Network"}),
            GraphNode("funder_nsf", "Funder", {"name": "National Science Foundation (NSF)"}),
        ])
        graph.add_edges([
            GraphEdge("doc_a", "person_x", "MENTIONS", "doc_a:1:0"),
            GraphEdge("doc_a", "proj_p", "DESCRIBES", "doc_a:1:0"),
            GraphEdge("person_x", "proj_p", "AFFILIATED_WITH", "doc_a:1:1"),
            GraphEdge("funder_nsf", "proj_p", "FUNDS", "doc_a:2:0"),
            GraphEdge("doc_b", "person_x", "MENTIONS", "doc_b:1:0"),
        ])
        with pytest.raises(StorageError):
            graph.add_edges([GraphEdge("doc_a", "person_x", "MENTIONS", "")])

        assert graph.query_template("people_by_project", {"entity_id": "proj_p"})[0]["source_chunk_id"] == "doc_a:1:1"
        assert graph.query_template("projects_by_funder", {"entity_id": "funder_nsf"})[0]["project_title"] == "Coastal Sensor Network"
        with pytest.raises(StorageError):
            graph.query_template("free_form", {})

        # Deleting doc_a removes every edge extracted from it, including entity-to-entity edges,
        # while the person still mentioned by doc_b survives.
        graph.delete_document("doc_a")
        assert graph.query_template("people_by_project", {"entity_id": "proj_p"}) == []
        assert graph.query_template("projects_by_funder", {"entity_id": "funder_nsf"}) == []
        assert [r["entity_id"] for r in graph.query_template("list_people", {})] == ["person_x"]
        assert graph.query_template("list_projects", {}) == []
    finally:
        graph.close()
        gc.collect()


# ------------------------------------------------------------------ generic graph extraction

def _chunks(doc_id: str, texts: list[str]) -> list[DocumentChunk]:
    return [DocumentChunk(chunk_id=f"{doc_id}:1:{i}", doc_id=doc_id, chunk_index=i, text_content=t) for i, t in enumerate(texts)]


def test_graph_extraction_is_generic_and_provenance_tracked():
    """Entities never seen in the synthetic corpus are extracted from their textual cues."""
    chunks = _chunks("doc_p", [
        "Project Title: Arctic Permafrost Carbon Flux Observatory\nPrincipal Investigator: Dr. Ingrid Solberg, Senior Scientist",
        "Funded by the National Science Foundation (NSF) under a cooperative agreement award.",
        "Tomas Rivera, M.S. — Field Engineer (50% effort)\nThe PI must submit annual reports to the program officer.",
    ])
    nodes, edges = GraphEntityExtractor().extract("doc_p", "p.pdf", chunks, known_funders={})
    by_label: dict[str, set[str]] = {}
    for n in nodes:
        by_label.setdefault(n.label, set()).add(next(iter(n.properties.values())))
    assert by_label["Person"] == {"Dr. Ingrid Solberg", "Tomas Rivera"}
    assert by_label["Project"] == {"Arctic Permafrost Carbon Flux Observatory"}
    assert by_label["Funder"] == {"National Science Foundation (NSF)"}
    assert any("must submit annual reports" in t for t in by_label["Requirement"])

    affiliations = {(e.source_id, e.source_chunk_id) for e in edges if e.rel_type == "AFFILIATED_WITH"}
    assert ("person_ingrid_solberg", "doc_p:1:0") in affiliations
    assert any(e.rel_type == "FUNDS" and e.source_chunk_id == "doc_p:1:1" for e in edges)
    assert all(e.source_chunk_id.startswith("doc_p:") for e in edges)


def test_graph_extraction_ignores_capitalized_non_names():
    chunks = _chunks("doc_q", ["Principal Investigator Eligibility Information is described in Section 3."])
    nodes, _ = GraphEntityExtractor().extract("doc_q", "q.pdf", chunks, known_funders={})
    assert [n for n in nodes if n.label == "Person"] == []


def test_graph_extraction_name_capture_is_case_sensitive():
    """review-04 C4 regression: role/field keywords match case-insensitively, but the NAME capture
    still requires proper-noun capitalization. Under a global re.IGNORECASE these lowercase phrases
    became Person nodes ('solar grid', 'someone wrote') and the fix was patched symptom-by-symptom
    by extending non_name_words."""
    chunks = _chunks("doc_c", [
        "Name: solar grid monitoring equipment",
        "Author: someone wrote this paragraph about the budget.",
        "lead: firmware update for the inverter controller",
        "Principal Investigator: Dr. Ingrid Solberg",
    ])
    nodes, _ = GraphEntityExtractor().extract("doc_c", "c.pdf", chunks, known_funders={})
    names = {n.properties["name"] for n in nodes if n.label == "Person"}
    assert names == {"Dr. Ingrid Solberg"}


def test_graph_extraction_award_field_is_not_a_project_title():
    """review-04 C4 regression: a bare 'Award:' field introduces budget text, not a project title."""
    chunks = _chunks("doc_a", ["Award: $500,000 for period of performance 2026-2029."])
    nodes, _ = GraphEntityExtractor().extract("doc_a", "a.pdf", chunks, known_funders={})
    assert [n for n in nodes if n.label == "Project"] == []


@pytest.mark.parametrize(
    "kind, name, query, expected",
    [
        # review-04 C3 regression: short entity names must not substring-match unrelated queries.
        ("funder", "Office of Science (SC)", "what is the office equipment budget?", False),
        ("project", "Solar Grid Center", "where is the awards ceremony held?", False),
        ("project", "Award", "award amount for the project", False),
        ("funder", "National Science Foundation (NSF)", "which agency funds this via NSF?", True),
        ("person", "Dr. Ingrid Solberg", "who is Ingrid Solberg?", True),
    ],
)
def test_graph_query_tool_entity_matching_is_strict(kind, name, query, expected):
    from app.services.retriever_tools import GraphQueryTool

    assert GraphQueryTool._matches(kind, name, query) is expected


def test_graph_query_tool_skip_status_does_not_disclose_indexed_entities(stack):
    """review-04 C3 regression: the skip status reaches the client, so it must say why without
    enumerating the indexed entity names back to the caller."""
    from app.services.retriever_tools import GraphQueryTool

    tool = GraphQueryTool(stack.graph_store, stack.registry, stack.settings)
    outcome = tool.run("what is the airspeed of an unladen swallow?")
    assert outcome.status == "skipped: no graph entity matched the query"
    assert "known:" not in outcome.status
