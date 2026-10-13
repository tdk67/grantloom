"""KùzuDB columnar embedded graph adapter implementing GraphStorePort."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Sequence

import kuzu

from app.core.errors import StorageError
from app.ports.graph_store import GraphStorePort
from app.ports.models import GraphEdge, GraphNode

logger = logging.getLogger("grantloom.adapters.kuzu_adapter")

# Node label -> the property holding its display text
NODE_TEXT_PROPERTY: dict[str, str] = {
    "Document": "filename",
    "Requirement": "text",
    "Person": "name",
    "Project": "title",
    "Funder": "name",
}

# Relationship type -> (source label, target label)
REL_SCHEMA: dict[str, tuple[str, str]] = {
    "REQUIRES": ("Document", "Requirement"),
    "MENTIONS": ("Document", "Person"),
    "MENTIONS_FUNDER": ("Document", "Funder"),
    "DESCRIBES": ("Document", "Project"),
    "AFFILIATED_WITH": ("Person", "Project"),
    "FUNDS": ("Funder", "Project"),
}

# Predefined parameterized Cypher query templates (no free-form Cypher, decision O5)
CYPHER_TEMPLATES: dict[str, str] = {
    "requirements_by_doc": (
        "MATCH (d:Document)-[r:REQUIRES]->(req:Requirement) WHERE d.id = $doc_id "
        "RETURN d.id AS doc_id, req.id AS req_id, req.text AS text, r.source_chunk_id AS source_chunk_id"
    ),
    "people_by_doc": (
        "MATCH (d:Document)-[r:MENTIONS]->(p:Person) WHERE d.id = $doc_id "
        "RETURN d.id AS doc_id, p.id AS person_id, p.name AS name, r.source_chunk_id AS source_chunk_id"
    ),
    "list_people": (
        "MATCH (d:Document)-[r:MENTIONS]->(p:Person) "
        "RETURN p.id AS entity_id, p.name AS name, r.source_chunk_id AS source_chunk_id"
    ),
    "list_funders": (
        "MATCH (d:Document)-[r:MENTIONS_FUNDER]->(f:Funder) "
        "RETURN f.id AS entity_id, f.name AS name, r.source_chunk_id AS source_chunk_id"
    ),
    "list_projects": (
        "MATCH (d:Document)-[r:DESCRIBES]->(p:Project) "
        "RETURN p.id AS entity_id, p.title AS name, r.source_chunk_id AS source_chunk_id"
    ),
    "projects_by_person": (
        "MATCH (p:Person)-[r:AFFILIATED_WITH]->(prj:Project) WHERE p.id = $entity_id "
        "RETURN p.name AS person_name, prj.title AS project_title, r.source_chunk_id AS source_chunk_id"
    ),
    "people_by_project": (
        "MATCH (p:Person)-[r:AFFILIATED_WITH]->(prj:Project) WHERE prj.id = $entity_id "
        "RETURN p.name AS person_name, prj.title AS project_title, r.source_chunk_id AS source_chunk_id"
    ),
    "projects_by_funder": (
        "MATCH (f:Funder)-[r:FUNDS]->(prj:Project) WHERE f.id = $entity_id "
        "RETURN f.name AS funder_name, prj.title AS project_title, r.source_chunk_id AS source_chunk_id"
    ),
    "funders_by_project": (
        "MATCH (f:Funder)-[r:FUNDS]->(prj:Project) WHERE prj.id = $entity_id "
        "RETURN f.name AS funder_name, prj.title AS project_title, r.source_chunk_id AS source_chunk_id"
    ),
    "edge_traceability": (
        "MATCH (a)-[r]->(b) WHERE r.source_chunk_id = $source_chunk_id "
        "RETURN r.source_chunk_id AS source_chunk_id"
    ),
}


class KuzuGraphStoreAdapter(GraphStorePort):
    """In-process columnar graph store backed by KùzuDB with provenance-tracked edges."""

    def __init__(self, kuzu_path: str) -> None:
        raw_path = Path(kuzu_path)
        if raw_path.suffix == "":
            raw_path.mkdir(parents=True, exist_ok=True)
            self.db_path = str(raw_path / "graph.kuzu")
        else:
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path = str(raw_path)
        self.kuzu_path = kuzu_path
        self.db = kuzu.Database(self.db_path)
        self.conn = kuzu.Connection(self.db)
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize node and relationship tables idempotently (IF NOT EXISTS)."""
        for label, prop in NODE_TEXT_PROPERTY.items():
            self.conn.execute(f"CREATE NODE TABLE IF NOT EXISTS {label}(id STRING, {prop} STRING, PRIMARY KEY(id));")
        for rel, (src, dst) in REL_SCHEMA.items():
            self.conn.execute(f"CREATE REL TABLE IF NOT EXISTS {rel}(FROM {src} TO {dst}, source_chunk_id STRING);")

    def add_nodes(self, nodes: Sequence[GraphNode]) -> None:
        """Add graph nodes into the columnar store (MERGE by id)."""
        for node in nodes:
            prop = NODE_TEXT_PROPERTY.get(node.label)
            if prop is None:
                raise StorageError(f"Unsupported graph node label '{node.label}'.")
            value = str(node.properties.get(prop, ""))
            try:
                self.conn.execute(
                    f"MERGE (n:{node.label} {{id: $id}}) ON CREATE SET n.{prop} = $value;",
                    {"id": node.node_id, "value": value},
                )
            except Exception as exc:
                raise StorageError(f"Failed to merge {node.label} node '{node.node_id}': {exc}") from exc

    def add_edges(self, edges: Sequence[GraphEdge]) -> None:
        """Add directed edges with mandatory source chunk provenance."""
        for edge in edges:
            if not edge.source_chunk_id:
                raise StorageError(
                    f"GraphEdge from '{edge.source_id}' to '{edge.target_id}' missing mandatory source_chunk_id."
                )
            rel_type = edge.rel_type.upper()
            if rel_type not in REL_SCHEMA:
                raise StorageError(f"Unsupported relationship type '{rel_type}'.")
            src, dst = REL_SCHEMA[rel_type]
            cypher = (
                f"MATCH (a:{src}), (b:{dst}) WHERE a.id = $source_id AND b.id = $target_id "
                f"CREATE (a)-[:{rel_type} {{source_chunk_id: $source_chunk_id}}]->(b);"
            )
            try:
                self.conn.execute(
                    cypher,
                    {"source_id": edge.source_id, "target_id": edge.target_id, "source_chunk_id": edge.source_chunk_id},
                )
            except Exception as exc:
                raise StorageError(f"Failed to create edge {rel_type}: {exc}") from exc

    def query_template(self, template_id: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute a predefined parameterized Cypher template without free-form injection."""
        template = CYPHER_TEMPLATES.get(template_id)
        if not template:
            raise StorageError(
                f"Unknown Cypher template ID '{template_id}'. Allowed templates: {list(CYPHER_TEMPLATES.keys())}"
            )
        try:
            res = self.conn.execute(template, params)
            cols = res.get_column_names()
            results: list[dict[str, Any]] = []
            while res.has_next():
                results.append(dict(zip(cols, res.get_next())))
            return results
        except Exception as exc:
            raise StorageError(f"Cypher template '{template_id}' failed: {exc}") from exc

    def delete_document(self, doc_id: str) -> None:
        """Purge every edge whose provenance chunk belongs to doc_id, then orphaned nodes.

        Chunk IDs are '<doc_id>:<page>:<index>', so provenance prefix matching catches edges between
        shared entities (e.g. Person -> Project) that were extracted from this document.
        """
        prefix = f"{doc_id}:"
        try:
            for rel in REL_SCHEMA:
                self.conn.execute(
                    f"MATCH ()-[r:{rel}]->() WHERE r.source_chunk_id STARTS WITH $prefix DELETE r;",
                    {"prefix": prefix},
                )
            self.conn.execute("MATCH (d:Document) WHERE d.id = $doc_id DETACH DELETE d;", {"doc_id": doc_id})
            for label in NODE_TEXT_PROPERTY:
                if label == "Document":
                    continue
                self.conn.execute(f"MATCH (n:{label}) WHERE NOT EXISTS {{ MATCH (n)-[]-() }} DELETE n;")
            logger.info("Purged graph edges and orphaned nodes for doc_id '%s'.", doc_id)
        except Exception as exc:
            raise StorageError(f"Failed to delete document '{doc_id}' from graph store: {exc}") from exc

    def ping(self) -> None:
        try:
            self.conn.execute("MATCH (d:Document) RETURN count(d);")
        except Exception as exc:
            raise StorageError(f"Graph store not reachable: {exc}") from exc

    def close(self) -> None:
        """Explicitly release resources for Windows file handle cleanup."""
        try:
            del self.conn
            del self.db
        except AttributeError:
            pass
