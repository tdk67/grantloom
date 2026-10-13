"""SQLite implementation of StructuredStorePort for tabular spreadsheets."""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional, Sequence

from app.core.errors import StorageError
from app.ports.structured_store import StructuredStorePort, TableInfo
from app.verification.sql_guard import SqlGuard

logger = logging.getLogger("grantloom.adapters.sqlite_structured_store")


def sanitize_column_name(name: str) -> str:
    """Sanitize column name to alphanumeric snake_case."""
    cleaned = re.sub(r"[^\w]+", "_", name.strip()).strip("_").lower()
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"col_{cleaned}"
    return cleaned


def infer_column_type(values: Sequence[Any]) -> str:
    """Infer SQLite column type (INTEGER, REAL, TEXT) from sample values."""
    non_empty = [v for v in values if v is not None and str(v).strip() != ""]
    if not non_empty:
        return "TEXT"

    is_int = True
    is_real = True

    for v in non_empty:
        if isinstance(v, int) and not isinstance(v, bool):
            continue
        if isinstance(v, float):
            is_int = False
            continue
        # String representation checks
        str_v = str(v).replace(",", "").replace("$", "").strip()
        try:
            int(str_v)
        except ValueError:
            is_int = False
            try:
                float(str_v)
            except ValueError:
                is_real = False
                break

    if is_int:
        return "INTEGER"
    if is_real:
        return "REAL"
    return "TEXT"


class SqliteStructuredStoreAdapter(StructuredStorePort):
    """Stores structured spreadsheet tables in SQLite with row numbers and AST-guarded queries.

    Every loaded table is recorded in `structured_tables` (doc_id, sheet_name) so query results stay
    traceable to the source document and so queries are confined to spreadsheet tables only.
    """

    def __init__(self, sqlite_path: str, sql_guard: SqlGuard) -> None:
        self.sqlite_path = sqlite_path
        self.sql_guard = sql_guard
        Path(self.sqlite_path).parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path, timeout=self.sql_guard.timeout_seconds)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def table_name_for(doc_id: str, sheet_name: str) -> str:
        return sanitize_column_name(f"{doc_id}_{sheet_name}")

    def load_sheet(
        self,
        doc_id: str,
        sheet_name: str,
        columns: Sequence[str],
        rows: Sequence[Sequence[Any]],
    ) -> str:
        """Create a typed tabular table and insert rows preserving original spreadsheet row numbers."""
        clean_table = self.table_name_for(doc_id, sheet_name)
        sanitized_cols = [sanitize_column_name(c) for c in columns]

        # Ensure uniqueness of column names (row_number is reserved for provenance)
        seen_cols: set[str] = {"row_number"}
        unique_cols: list[str] = []
        for col in sanitized_cols:
            candidate = col
            counter = 1
            while candidate in seen_cols:
                candidate = f"{col}_{counter}"
                counter += 1
            seen_cols.add(candidate)
            unique_cols.append(candidate)

        # rows are (row_number, [values...]) tuples, or bare value lists (1-indexed)
        normalized: list[tuple[int, list[Any]]] = []
        for r_idx, row in enumerate(rows, start=1):
            if len(row) >= 2 and isinstance(row[0], int) and isinstance(row[1], (list, tuple)):
                normalized.append((row[0], list(row[1])))
            else:
                normalized.append((r_idx, list(row)))

        col_values: list[list[Any]] = [[] for _ in unique_cols]
        for _, vals in normalized:
            for c_idx, val in enumerate(vals[: len(unique_cols)]):
                col_values[c_idx].append(val)
        col_types = [infer_column_type(vals) for vals in col_values]

        col_defs = ["row_number INTEGER"] + [f'"{n}" {t}' for n, t in zip(unique_cols, col_types)]

        to_insert = []
        for row_num, vals in normalized:
            vals = (vals + [None] * len(unique_cols))[: len(unique_cols)]
            clean_row_vals: list[Any] = []
            for val, c_type in zip(vals, col_types):
                if val is None:
                    clean_row_vals.append(None)
                elif c_type in ("INTEGER", "REAL"):
                    caster = int if c_type == "INTEGER" else float
                    try:
                        clean_row_vals.append(caster(str(val).replace(",", "").replace("$", "").strip()))
                    except (ValueError, TypeError):
                        clean_row_vals.append(None)
                else:
                    clean_row_vals.append(str(val).strip())
            to_insert.append([row_num] + clean_row_vals)

        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(f'DROP TABLE IF EXISTS "{clean_table}";')
            cursor.execute(f'CREATE TABLE "{clean_table}" ({", ".join(col_defs)});')
            placeholders = ", ".join(["?"] * (len(unique_cols) + 1))
            cursor.executemany(f'INSERT INTO "{clean_table}" VALUES ({placeholders});', to_insert)
            cursor.execute(
                "INSERT OR REPLACE INTO structured_tables (table_name, doc_id, sheet_name, row_count) VALUES (?, ?, ?, ?)",
                (clean_table, doc_id, sheet_name, len(to_insert)),
            )
            conn.commit()
            logger.info("Loaded sheet '%s' of doc '%s' into table '%s' (%d rows).", sheet_name, doc_id, clean_table, len(to_insert))
            return clean_table
        except Exception as exc:
            conn.rollback()
            raise StorageError(f"Failed to load sheet into table '{clean_table}': {exc}") from exc
        finally:
            conn.close()

    def list_tables(self, doc_ids: Optional[Sequence[str]] = None) -> list[TableInfo]:
        """List loaded tables (with column schemas), optionally restricted to the given documents."""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            if doc_ids is not None:
                if not doc_ids:
                    return []
                marks = ", ".join("?" for _ in doc_ids)
                cursor.execute(
                    f"SELECT table_name, doc_id, sheet_name, row_count FROM structured_tables WHERE doc_id IN ({marks}) ORDER BY table_name",
                    list(doc_ids),
                )
            else:
                cursor.execute("SELECT table_name, doc_id, sheet_name, row_count FROM structured_tables ORDER BY table_name")
            infos = [
                TableInfo(table_name=r["table_name"], doc_id=r["doc_id"], sheet_name=r["sheet_name"], row_count=r["row_count"])
                for r in cursor.fetchall()
            ]
            for info in infos:
                cursor.execute(f'PRAGMA table_info("{info.table_name}");')
                info.columns = [{"name": r["name"], "type": r["type"]} for r in cursor.fetchall()]
            return infos
        except sqlite3.Error as exc:
            raise StorageError(f"Failed to list structured tables: {exc}") from exc
        finally:
            conn.close()

    def execute_query(
        self,
        sql_query: str,
        allowed_tables: Optional[Sequence[str]] = None,
    ) -> list[dict[str, Any]]:
        """Execute a validated read-only SELECT confined to registered spreadsheet tables."""
        known = {t.table_name for t in self.list_tables()}
        allowed = known if allowed_tables is None else known & set(allowed_tables)
        conn = self._connect()
        try:
            return self.sql_guard.execute_safe(conn, sql_query, allowed_tables=allowed)
        finally:
            conn.close()

    def delete_document_tables(self, doc_id: str) -> None:
        """Purge tabular tables associated with a given document ID."""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT table_name FROM structured_tables WHERE doc_id = ?", (doc_id,))
            tables = [row[0] for row in cursor.fetchall()]
            for tbl in tables:
                cursor.execute(f'DROP TABLE IF EXISTS "{tbl}";')
            cursor.execute("DELETE FROM structured_tables WHERE doc_id = ?", (doc_id,))
            conn.commit()
            logger.info("Purged %d tables for document '%s'.", len(tables), doc_id)
        except sqlite3.Error as exc:
            conn.rollback()
            raise StorageError(f"Failed to purge structured tables for '{doc_id}': {exc}") from exc
        finally:
            conn.close()
