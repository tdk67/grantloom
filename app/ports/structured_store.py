"""Abstract Structured Tabular Store Port interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence


class StructuredStorePort(ABC):
    """Abstract port for loading spreadsheets/CSV and running read-only tabular queries."""

    @abstractmethod
    def load_sheet(
        self,
        table_name: str,
        columns: Sequence[str],
        rows: Sequence[Sequence[Any]],
    ) -> None:
        """Create a typed tabular table and insert rows preserving original spreadsheet row numbers."""
        ...

    @abstractmethod
    def execute_query(self, sql_query: str) -> list[dict[str, Any]]:
        """Execute a validated read-only SELECT statement against the structured tables."""
        ...

    @abstractmethod
    def delete_document_tables(self, doc_id: str) -> None:
        """Purge tabular tables associated with a given document ID."""
        ...
