"""SQL Guard AST & Lexical Parser.

Enforces:
- Single statement only (no chained commands or semicolons)
- Read-only SELECT statements only
- Allow-listed user tables, blocking system tables (e.g. sqlite_master, sqlite_schema, alembic_version)
- Row limit <= 100 (enforced or capped)
- Execution timeout <= 5.0 seconds
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import Optional, Sequence, Set

from app.core.errors import SqlGuardError

logger = logging.getLogger("grantloom.verification.sql_guard")

# Forbidden write/admin keywords
FORBIDDEN_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
    "ATTACH", "DETACH", "PRAGMA", "REINDEX", "VACUUM",
    "REPLACE", "UPSERT", "EXEC", "EXECUTE", "TRUNCATE",
    "GRANT", "REVOKE", "COMMIT", "ROLLBACK", "SAVEPOINT",
    "RELEASE", "BEGIN", "TRANSACTION", "LOAD_EXTENSION"
}

# Forbidden system / catalog tables
FORBIDDEN_TABLES = {
    "SQLITE_MASTER", "SQLITE_SCHEMA", "SQLITE_TEMP_MASTER",
    "SQLITE_TEMP_SCHEMA", "SQLITE_SEQUENCE", "SQLITE_STAT1",
    "ALEMBIC_VERSION", "AUDIT_LOG", "OUTBOX_JOBS"
}

# Token matching for basic SQL AST extraction
WORD_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
SEMICOLON_RE = re.compile(r";")
LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)


class SqlGuard:
    """Validates and enforces strict read-only constraints on generated or user SQL."""

    def __init__(
        self,
        max_rows: int = 100,
        timeout_seconds: float = 5.0,
        allowed_tables: Optional[Set[str]] = None,
    ) -> None:
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds
        self.allowed_tables = {t.upper() for t in allowed_tables} if allowed_tables else None

    def clean_comments(self, sql: str) -> str:
        """Strip single-line and multi-line SQL comments."""
        # Multi-line comments /* ... */
        cleaned = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        # Single-line comments -- ...
        cleaned = re.sub(r"--[^\n]*", " ", cleaned)
        return cleaned.strip()

    def validate_and_sanitize(self, sql: str) -> str:
        """Validate SQL against AST security rules and return bounded query with LIMIT <= max_rows."""
        cleaned = self.clean_comments(sql)
        if not cleaned:
            raise SqlGuardError("Empty SQL query is not allowed.")

        # 1. Enforce single statement: No multiple statements separated by semicolons
        statements = [s.strip() for s in cleaned.split(";") if s.strip()]
        if len(statements) > 1:
            raise SqlGuardError("Multiple SQL statements are strictly prohibited.")

        single_query = statements[0]

        # 2. Extract leading keyword: Must be SELECT
        words = single_query.split()
        if not words:
            raise SqlGuardError("Empty SQL query is not allowed.")

        first_word = words[0].upper()
        if first_word != "SELECT" and first_word != "WITH":
            raise SqlGuardError(
                f"Forbidden statement type '{first_word}'. Only read-only SELECT queries are allowed."
            )

        # 3. Check for any forbidden keywords anywhere in the statement
        upper_tokens = [w.upper() for w in WORD_RE.findall(single_query)]
        for token in upper_tokens:
            if token in FORBIDDEN_KEYWORDS:
                raise SqlGuardError(f"Forbidden SQL keyword detected: '{token}'.")

        # 4. Check for system tables
        for token in upper_tokens:
            if token in FORBIDDEN_TABLES:
                raise SqlGuardError(f"Access to system table '{token}' is prohibited.")

        # 5. Check allowed tables if configured
        if self.allowed_tables is not None:
            table_tokens = self._extract_table_names(single_query)
            for tbl in table_tokens:
                if tbl.upper() not in self.allowed_tables:
                    raise SqlGuardError(
                        f"Table '{tbl}' is not in the allowed tables list: {sorted(self.allowed_tables)}"
                    )

        # 6. Enforce LIMIT <= self.max_rows
        limit_match = LIMIT_RE.search(single_query)
        if limit_match:
            existing_limit = int(limit_match.group(1))
            if existing_limit > self.max_rows:
                sanitized_query = LIMIT_RE.sub(f"LIMIT {self.max_rows}", single_query)
            else:
                sanitized_query = single_query
        else:
            sanitized_query = f"{single_query} LIMIT {self.max_rows}"

        return sanitized_query

    def _extract_table_names(self, sql: str) -> list[str]:
        """Extract table names referenced in FROM and JOIN clauses."""
        pattern = re.compile(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
        return pattern.findall(sql)

    def execute_safe(self, conn: sqlite3.Connection, sql: str) -> list[dict]:
        """Execute validated query with runtime authorizer and timeout."""
        sanitized_sql = self.validate_and_sanitize(sql)

        # Set sqlite authorizer for defense in depth
        def authorizer(action: int, arg1: Optional[str], arg2: Optional[str], db_name: Optional[str], trigger_name: Optional[str]) -> int:
            if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, 31):
                if arg1 and arg1.upper() in FORBIDDEN_TABLES:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        conn.set_authorizer(authorizer)

        start_time = time.monotonic()
        max_duration = self.timeout_seconds

        def progress_handler() -> int:
            if time.monotonic() - start_time > max_duration:
                return 1
            return 0

        conn.set_progress_handler(progress_handler, 1000)

        cursor = conn.cursor()
        try:
            cursor.execute(sanitized_sql)
            columns = [col[0] for col in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                raise SqlGuardError(f"Query execution timed out after {self.timeout_seconds}s.") from exc
            if "not authorized" in str(exc).lower():
                raise SqlGuardError(f"Query violated authorization policy: {exc}") from exc
            raise SqlGuardError(f"SQL execution failed: {exc}") from exc
        finally:
            conn.set_authorizer(None)
            conn.set_progress_handler(None, 0)
