"""SQLite implementation of StructuredStorePort for tabular spreadsheets."""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional, Sequence

from app.core.errors import SqlGuardError, StorageError
from app.ports.structured_store import StructuredStorePort
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
    """Stores structured spreadsheet tables in SQLite with row numbers and AST-guarded queries."""

    def __init__(
        self,
        sqlite_path: str,
        sql_guard: Optional[SqlGuard] = None,
    ) -> None:
        self.sqlite_path = sqlite_path
        self.sql_guard = sql_guard or SqlGuard()
        Path(self.sqlite_path).parent.mkdir(parents=True, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def load_sheet(
        self,
        table_name: str,
        columns: Sequence[str],
        rows: Sequence[Sequence[Any]],
    ) -> None:
        """Create a typed tabular table and insert rows preserving original spreadsheet row numbers."""
        clean_table = sanitize_column_name(table_name)
        sanitized_cols = [sanitize_column_name(c) for c in columns]

        # Ensure uniqueness of column names
        seen_cols: set[str] = set()
        unique_cols: list[str] = []
        for idx, col in enumerate(sanitized_cols):
            candidate = col
            counter = 1
            while candidate in seen_cols:
                candidate = f"{col}_{counter}"
                counter += 1
            seen_cols.add(candidate)
            unique_cols.append(candidate)

        # Infer types column by column (rows is list of tuples: (row_number, [val1, val2, ...]))
        col_values: list[list[Any]] = [[] for _ in unique_cols]
        for row in rows:
            if len(row) >= 2 and isinstance(row[0], int) and isinstance(row[1], (list, tuple)):
                # Format: (row_number, values)
                r_vals = row[1]
            else:
                # Format: values only, 1-indexed
                r_vals = row

            for c_idx, val in enumerate(r_vals):
                if c_idx < len(col_values):
                    col_values[c_idx].append(val)

        col_types = [infer_column_type(vals) for vals in col_values]

        col_defs = ["row_number INTEGER"]
        for c_name, c_type in zip(unique_cols, col_types):
            col_defs.append(f'"{c_name}" {c_type}')

        create_sql = f'CREATE TABLE IF NOT EXISTS "{clean_table}" ({", ".join(col_defs)});'

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(f'DROP TABLE IF EXISTS "{clean_table}";')
            cursor.execute(create_sql)

            # Insert rows
            placeholders = ", ".join(["?"] * (len(unique_cols) + 1))
            insert_sql = f'INSERT INTO "{clean_table}" VALUES ({placeholders});'

            to_insert = []
            for r_idx, row in enumerate(rows, start=1):
                if len(row) >= 2 and isinstance(row[0], int) and isinstance(row[1], (list, tuple)):
                    row_num = row[0]
                    vals = list(row[1])
                else:
                    row_num = r_idx
                    vals = list(row)

                # Pad or truncate values to match columns
                if len(vals) < len(unique_cols):
                    vals.extend([None] * (len(unique_cols) - len(vals)))
                else:
                    vals = vals[: len(unique_cols)]

                # Convert numeric strings if column is numeric
                clean_row_vals = []
                for val, c_type in zip(vals, col_types):
                    if val is None:
                        clean_row_vals.append(None)
                    elif c_type == "INTEGER":
                        try:
                            clean_row_vals.append(int(str(val).replace(",", "").replace("$", "").strip()))
                        except (ValueError, TypeError):
                            clean_row_vals.append(None)
                    elif c_type == "REAL":
                        try:
                            clean_row_vals.append(float(str(val).replace(",", "").replace("$", "").strip()))
                        except (ValueError, TypeError):
                            clean_row_vals.append(None)
                    else:
                        clean_row_vals.append(str(val).strip())

                to_insert.append([row_num] + clean_row_vals)

            cursor.executemany(insert_sql, to_insert)
            conn.commit()
            logger.info("Loaded sheet into table '%s' with %d rows.", clean_table, len(to_insert))
        except Exception as exc:
            conn.rollback()
            raise StorageError(f"Failed to load sheet into table '{clean_table}': {exc}") from exc
        finally:
            conn.close()

    def execute_query(self, sql_query: str) -> list[dict[str, Any]]:
        """Execute a validated read-only SELECT statement against the structured tables."""
        conn = self._get_connection()
        try:
            return self.sql_guard.execute_safe(conn, sql_query)
        finally:
            conn.close()

    def delete_document_tables(self, doc_id: str) -> None:
        """Purge tabular tables associated with a given document ID."""
        clean_prefix = sanitize_column_name(f"doc_{doc_id}")
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ?", (f"{clean_prefix}%",))
            tables = [row[0] for row in cursor.fetchall()]
            for tbl in tables:
                cursor.execute(f'DROP TABLE IF EXISTS "{tbl}";')
            conn.commit()
            logger.info("Purged %d tables for document '%s'.", len(tables), doc_id)
        finally:
            conn.close()

    def get_table_schema(self, table_name: str) -> list[dict[str, str]]:
        """Return list of column names and types for a table."""
        clean_tbl = sanitize_column_name(table_name)
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(f'PRAGMA table_info("{clean_tbl}");')
            return [{"name": row["name"], "type": row["type"]} for row in cursor.fetchall()]
        finally:
            conn.close()
