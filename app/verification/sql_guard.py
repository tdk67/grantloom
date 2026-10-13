"""SQL Guard AST & Lexical Parser.

Enforces:
- Single statement only (no chained commands or semicolons)
- Read-only SELECT statements only
- Allow-listed user tables, blocking system tables and GrantLoom's internal registry tables
- Row limit (config `sql_guard.max_rows`, enforced or capped)
- Execution timeout (config `sql_guard.timeout_seconds`)
- A SQLite authorizer that re-checks every table read at execution time (defense in depth)
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import Optional, Set

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

# Forbidden system / catalog tables and GrantLoom's internal registry tables
FORBIDDEN_TABLES = {
    "SQLITE_MASTER", "SQLITE_SCHEMA", "SQLITE_TEMP_MASTER",
    "SQLITE_TEMP_SCHEMA", "SQLITE_SEQUENCE", "SQLITE_STAT1",
    "ALEMBIC_VERSION", "AUDIT_LOGS", "OUTBOX_JOBS", "DOCUMENTS",
    "DOCUMENT_CHUNKS", "CHUNKS_FTS", "STRUCTURED_TABLES", "COMPLIANCE_REQUIREMENTS",
}

# Token matching for basic SQL AST extraction
WORD_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)
# FROM / JOIN targets, bare or "quoted" / `backticked` / [bracketed]
TABLE_REF_RE = re.compile(r"""\b(?:FROM|JOIN)\s+["`\[]?([a-zA-Z_][a-zA-Z0-9_]*)["`\]]?""", re.IGNORECASE)


class SqlGuard:
    """Validates and enforces strict read-only constraints on generated or user SQL."""

    def __init__(
        self,
        max_rows: int,
        timeout_seconds: float,
        allowed_tables: Optional[Set[str]] = None,
    ) -> None:
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds
        self.allowed_tables = {t.upper() for t in allowed_tables} if allowed_tables else None

    def clean_comments(self, sql: str) -> str:
        """Strip single-line and multi-line SQL comments."""
        cleaned = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        cleaned = re.sub(r"--[^\n]*", " ", cleaned)
        return cleaned.strip()

    def _effective_allowed(self, allowed_tables: Optional[Set[str]]) -> Optional[set[str]]:
        if allowed_tables is None:
            return self.allowed_tables
        requested = {t.upper() for t in allowed_tables}
        return requested if self.allowed_tables is None else requested & self.allowed_tables

    def validate_and_sanitize(self, sql: str, allowed_tables: Optional[Set[str]] = None) -> str:
        """Validate SQL against AST security rules and return bounded query with LIMIT <= max_rows."""
        allowed = self._effective_allowed(allowed_tables)
        cleaned = self.clean_comments(sql)
        if not cleaned:
            raise SqlGuardError("Empty SQL query is not allowed.")

        # 1. Enforce single statement: No multiple statements separated by semicolons
        statements = [s.strip() for s in cleaned.split(";") if s.strip()]
        if len(statements) > 1:
            raise SqlGuardError("Multiple SQL statements are strictly prohibited.")
        single_query = statements[0]

        # 2. Extract leading keyword: Must be SELECT (or a WITH ... SELECT)
        first_word = single_query.split()[0].upper()
        if first_word not in ("SELECT", "WITH"):
            raise SqlGuardError(
                f"Forbidden statement type '{first_word}'. Only read-only SELECT queries are allowed."
            )

        # 3. Check for any forbidden keywords anywhere in the statement
        upper_tokens = [w.upper() for w in WORD_RE.findall(single_query)]
        for token in upper_tokens:
            if token in FORBIDDEN_KEYWORDS:
                raise SqlGuardError(f"Forbidden SQL keyword detected: '{token}'.")

        # 4. Check for system / internal tables
        for token in upper_tokens:
            if token in FORBIDDEN_TABLES:
                raise SqlGuardError(f"Access to system table '{token}' is prohibited.")

        # 5. Check allowed tables if configured
        if allowed is not None:
            for tbl in TABLE_REF_RE.findall(single_query):
                if tbl.upper() not in allowed:
                    raise SqlGuardError(f"Table '{tbl}' is not in the allowed tables list: {sorted(allowed)}")

        # 6. Enforce LIMIT <= self.max_rows
        limit_match = LIMIT_RE.search(single_query)
        if limit_match:
            if int(limit_match.group(1)) > self.max_rows:
                return LIMIT_RE.sub(f"LIMIT {self.max_rows}", single_query)
            return single_query
        return f"{single_query} LIMIT {self.max_rows}"

    def execute_safe(
        self,
        conn: sqlite3.Connection,
        sql: str,
        allowed_tables: Optional[Set[str]] = None,
    ) -> list[dict]:
        """Execute validated query with runtime authorizer and timeout."""
        sanitized_sql = self.validate_and_sanitize(sql, allowed_tables)
        allowed = self._effective_allowed(allowed_tables)

        def authorizer(action: int, arg1: Optional[str], arg2: Optional[str], db_name: Optional[str], trigger_name: Optional[str]) -> int:
            if action == sqlite3.SQLITE_READ:
                table = (arg1 or "").upper()
                if table in FORBIDDEN_TABLES or (allowed is not None and table not in allowed):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        conn.set_authorizer(authorizer)

        start_time = time.monotonic()
        max_duration = self.timeout_seconds

        def progress_handler() -> int:
            return 1 if time.monotonic() - start_time > max_duration else 0

        conn.set_progress_handler(progress_handler, 1000)

        cursor = conn.cursor()
        try:
            cursor.execute(sanitized_sql)
            columns = [col[0] for col in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        except sqlite3.DatabaseError as exc:  # OperationalError and authorizer denials both derive from it
            message = str(exc).lower()
            if "interrupted" in message:
                raise SqlGuardError(f"Query execution timed out after {self.timeout_seconds}s.") from exc
            if "not authorized" in message or "prohibited" in message:
                raise SqlGuardError(f"Query violated authorization policy: {exc}") from exc
            raise SqlGuardError(f"SQL execution failed: {exc}") from exc
        finally:
            conn.set_authorizer(None)
            conn.set_progress_handler(None, 0)
