"""Integration tests for Milestone M3: Structured Spreadsheet & KùzuDB Graph Integration.

Verifies:
1. Query test against sample budget spreadsheet ("What is the travel total?") asserting
   correct SQL execution, AST guard pass, and row coordinate citation.
2. Malicious SQL test (SELECT * FROM sqlite_master; DROP TABLE ...) asserting immediate block by sql_guard.
3. Kùzu query test verifying relationship traversal and source chunk traceability on edges.
"""

from __future__ import annotations

import gc
import shutil
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from app.adapters.kuzu_adapter import KuzuGraphStoreAdapter
from app.adapters.sqlite_structured_store import SqliteStructuredStoreAdapter
from app.core.errors import SqlGuardError, StorageError
from app.ports.models import GraphEdge, GraphNode
from app.verification.sql_guard import SqlGuard


def create_sample_budget_xlsx(file_path: Path) -> Path:
    """Generate sample budget Excel workbook with Personnel, Travel, Equipment sheets."""
    wb = openpyxl.Workbook()

    # Sheet 1: Personnel
    ws_personnel = wb.active
    ws_personnel.title = "Personnel"
    ws_personnel.append(["Role", "Name", "Effort_Months", "Salary"])
    ws_personnel.append(["PI", "Dr. Elena Vance", 3, 45000])
    ws_personnel.append(["Postdoc", "Alex Mercer", 12, 65000])
    ws_personnel.append(["Grad Student", "Samira Khan", 12, 32000])

    # Sheet 2: Travel
    ws_travel = wb.create_sheet(title="Travel")
    ws_travel.append(["Trip_Description", "Travelers", "Cost"])
    ws_travel.append(["DOE PI Meeting (Washington DC)", 2, 2800])
    ws_travel.append(["IEEE PVSC Conference (Austin TX)", 3, 4200])
    ws_travel.append(["Field Testing Site Visit (Nevada)", 1, 1500])

    # Sheet 3: Equipment
    ws_equipment = wb.create_sheet(title="Equipment")
    ws_equipment.append(["Item_Description", "Quantity", "Unit_Price", "Total_Cost"])
    ws_equipment.append(["Solar Simulator Spectrometer", 1, 55000, 55000])
    ws_equipment.append(["IV Characterization Station", 1, 28000, 28000])

    wb.save(str(file_path))
    return file_path


# ==============================================================================
# 1. AST SQL Guard Security Tests
# ==============================================================================

def test_sql_guard_blocks_malicious_multi_statement():
    """Assert that multiple chained statements (e.g. injection) are immediately blocked."""
    guard = SqlGuard()
    malicious_sql = "SELECT * FROM sqlite_master; DROP TABLE document_registry;"
    with pytest.raises(SqlGuardError) as exc_info:
        guard.validate_and_sanitize(malicious_sql)
    assert "Multiple SQL statements" in str(exc_info.value)


def test_sql_guard_blocks_write_keywords():
    """Assert that INSERT, UPDATE, DELETE, DROP, PRAGMA are blocked."""
    guard = SqlGuard()
    write_queries = [
        "DROP TABLE users;",
        "DELETE FROM budget WHERE amount > 0;",
        "INSERT INTO budget VALUES (1, 'hack', 9999);",
        "PRAGMA table_info('budget');",
        "ALTER TABLE budget ADD COLUMN pw TEXT;",
    ]
    for q in write_queries:
        with pytest.raises(SqlGuardError):
            guard.validate_and_sanitize(q)


def test_sql_guard_blocks_system_tables():
    """Assert access to sqlite_master, sqlite_schema, and alembic_version is blocked."""
    guard = SqlGuard()
    system_queries = [
        "SELECT * FROM sqlite_master",
        "SELECT name FROM sqlite_schema",
        "SELECT version_num FROM alembic_version",
    ]
    for q in system_queries:
        with pytest.raises(SqlGuardError) as exc_info:
            guard.validate_and_sanitize(q)
        assert "prohibited" in str(exc_info.value).lower() or "system table" in str(exc_info.value).lower()


def test_sql_guard_enforces_limit_cap():
    """Assert that queries without LIMIT get LIMIT 100 and LIMIT > 100 is capped."""
    guard = SqlGuard(max_rows=100)
    # Query with no limit
    sanitized1 = guard.validate_and_sanitize("SELECT * FROM doc_123_travel")
    assert "LIMIT 100" in sanitized1

    # Query with excessive limit
    sanitized2 = guard.validate_and_sanitize("SELECT * FROM doc_123_travel LIMIT 5000")
    assert "LIMIT 100" in sanitized2
    assert "5000" not in sanitized2

    # Query with valid lower limit is preserved
    sanitized3 = guard.validate_and_sanitize("SELECT * FROM doc_123_travel LIMIT 10")
    assert "LIMIT 10" in sanitized3


# ==============================================================================
# 2. Structured Tabular Spreadsheet Ingestion and Query Tests
# ==============================================================================

def test_structured_store_ingestion_and_travel_total_query(tmp_path: Path):
    """Query test against sample budget spreadsheet ('What is the travel total?').

    Asserts:
    - Tables created in SQLite preserving row_number and data types
    - Correct SQL execution via SqlGuard
    - Travel total calculation matches ($8,500)
    - Row coordinate citations are preserved
    """
    db_path = tmp_path / "structured_test.db"
    store = SqliteStructuredStoreAdapter(str(db_path))

    # Sample Travel rows (row_number, [Trip_Description, Travelers, Cost])
    travel_columns = ["Trip_Description", "Travelers", "Cost"]
    travel_rows = [
        (2, ["DOE PI Meeting (Washington DC)", 2, 2800]),
        (3, ["IEEE PVSC Conference (Austin TX)", 3, 4200]),
        (4, ["Field Testing Site Visit (Nevada)", 1, 1500]),
    ]
    store.load_sheet("doc_doe2026_travel", travel_columns, travel_rows)

    # Verify table schema
    schema = store.get_table_schema("doc_doe2026_travel")
    col_names = [col["name"] for col in schema]
    assert "row_number" in col_names
    assert "trip_description" in col_names
    assert "cost" in col_names

    # Run guarded SQL query computing travel total
    query = "SELECT SUM(cost) AS travel_total FROM doc_doe2026_travel;"
    results = store.execute_query(query)
    assert len(results) == 1
    assert results[0]["travel_total"] == 8500  # 2800 + 4200 + 1500

    # Query individual rows and check row coordinate preservation
    detail_query = "SELECT row_number, trip_description, cost FROM doc_doe2026_travel ORDER BY row_number;"
    details = store.execute_query(detail_query)
    assert len(details) == 3
    assert details[0]["row_number"] == 2
    assert details[0]["trip_description"] == "DOE PI Meeting (Washington DC)"
    assert details[0]["cost"] == 2800

    assert details[1]["row_number"] == 3
    assert details[2]["row_number"] == 4

    # Test delete_document_tables
    store.delete_document_tables("doe2026")
    with pytest.raises(SqlGuardError):
        store.execute_query("SELECT * FROM doc_doe2026_travel;")


# ==============================================================================
# 3. KùzuDB Columnar Graph Store & Provenance Traceability Tests
# ==============================================================================

def test_kuzudb_graph_integration_and_provenance_traceability(tmp_path: Path):
    """Kùzu query test verifying relationship traversal and source chunk traceability on edges."""
    kuzu_dir = tmp_path / "kuzudb_test"
    graph_store = KuzuGraphStoreAdapter(str(kuzu_dir))

    try:
        # 1. Add nodes: Document, Requirement, Person, Project, Funder
        nodes = [
            GraphNode(node_id="doc_solar_2026", label="Document", properties={"filename": "DOE_Solar_2026.pdf"}),
            GraphNode(node_id="req_cost_match", label="Requirement", properties={"text": "Mandatory 20% non-federal cost match"}),
            GraphNode(node_id="person_elena", label="Person", properties={"name": "Dr. Elena Vance"}),
            GraphNode(node_id="proj_pv_tech", label="Project", properties={"title": "Perovskite Photovoltaic Research"}),
            GraphNode(node_id="funder_doe", label="Funder", properties={"name": "Department of Energy"}),
        ]
        graph_store.add_nodes(nodes)

        # 2. Add edges with mandatory source_chunk_id
        edges = [
            GraphEdge(
                source_id="doc_solar_2026",
                target_id="req_cost_match",
                rel_type="REQUIRES",
                source_chunk_id="doc_solar_2026:1:3",
            ),
            GraphEdge(
                source_id="doc_solar_2026",
                target_id="person_elena",
                rel_type="MENTIONS",
                source_chunk_id="doc_solar_2026:2:1",
            ),
            GraphEdge(
                source_id="person_elena",
                target_id="proj_pv_tech",
                rel_type="AFFILIATED_WITH",
                source_chunk_id="past_prop_2024:3:2",
            ),
            GraphEdge(
                source_id="funder_doe",
                target_id="proj_pv_tech",
                rel_type="FUNDS",
                source_chunk_id="past_prop_2024:1:1",
            ),
        ]
        graph_store.add_edges(edges)

        # 3. Verify Edge without source_chunk_id is rejected
        with pytest.raises(StorageError):
            graph_store.add_edges([
                GraphEdge(
                    source_id="doc_solar_2026",
                    target_id="person_elena",
                    rel_type="MENTIONS",
                    source_chunk_id="",  # Missing provenance!
                )
            ])

        # 4. Query template: requirements_by_doc
        req_res = graph_store.query_template("requirements_by_doc", {"doc_id": "doc_solar_2026"})
        assert len(req_res) == 1
        assert req_res[0]["doc_id"] == "doc_solar_2026"
        assert req_res[0]["req_id"] == "req_cost_match"
        assert req_res[0]["source_chunk_id"] == "doc_solar_2026:1:3"

        # 5. Query template: affiliations_by_person
        affil_res = graph_store.query_template("affiliations_by_person", {
            "person_name": "Dr. Elena Vance",
            "person_id": "person_elena",
        })
        assert len(affil_res) == 1
        assert affil_res[0]["person_name"] == "Dr. Elena Vance"
        assert affil_res[0]["project_title"] == "Perovskite Photovoltaic Research"
        assert affil_res[0]["source_chunk_id"] == "past_prop_2024:3:2"

        # 6. Query template: funder_projects
        funder_res = graph_store.query_template("funder_projects", {
            "funder_name": "Department of Energy",
            "funder_id": "funder_doe",
        })
        assert len(funder_res) == 1
        assert funder_res[0]["funder_name"] == "Department of Energy"
        assert funder_res[0]["project_title"] == "Perovskite Photovoltaic Research"
        assert funder_res[0]["source_chunk_id"] == "past_prop_2024:1:1"

        # 7. Query template: traverse_all
        all_affils = graph_store.query_template("traverse_all", {})
        assert len(all_affils) >= 1
        assert all_affils[0]["source_chunk_id"] == "past_prop_2024:3:2"

        # 8. Query template: edge_traceability
        trace_res = graph_store.query_template("edge_traceability", {"source_chunk_id": "doc_solar_2026:1:3"})
        assert len(trace_res) == 1
        assert trace_res[0]["source_chunk_id"] == "doc_solar_2026:1:3"

    finally:
        # Ensure Windows handles are freed before directory cleanup
        graph_store.close()
        gc.collect()
