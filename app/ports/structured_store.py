"""Abstract Structured Tabular Store Port interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence


@dataclass
class TableInfo:
    """Schema of one loaded spreadsheet table, traceable to its source document and sheet."""

    table_name: str
    doc_id: str
    sheet_name: str
    columns: list[dict[str, str]] = field(default_factory=list)
    row_count: int = 0


class StructuredStorePort(ABC):
    """Abstract port for loading spreadsheets/CSV and running read-only tabular queries."""

    @abstractmethod
    def load_sheet(
        self,
        doc_id: str,
        sheet_name: str,
        columns: Sequence[str],
        rows: Sequence[Sequence[Any]],
    ) -> str:
        """Create a typed table for one sheet (preserving spreadsheet row numbers); returns the table name."""
        ...

    @abstractmethod
    def list_tables(self, doc_ids: Optional[Sequence[str]] = None) -> list[TableInfo]:
        """List loaded tables with schemas, optionally restricted to the given documents."""
        ...

    @abstractmethod
    def execute_query(
        self,
        sql_query: str,
        allowed_tables: Optional[Sequence[str]] = None,
    ) -> list[dict[str, Any]]:
        """Execute a guarded read-only SELECT, optionally restricted to an allow-list of tables."""
        ...

    @abstractmethod
    def delete_document_tables(self, doc_id: str) -> None:
        """Purge tabular tables associated with a given document ID."""
        ...
