"""KùzuDB columnar embedded graph adapter implementing GraphStorePort."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional, Sequence

import kuzu

from app.core.errors import StorageError
from app.ports.graph_store import GraphStorePort
from app.ports.models import GraphEdge, GraphNode

logger = logging.getLogger("grantloom.adapters.kuzu_adapter")

# Predefined parameterized Cypher query templates
CYPHER_TEMPLATES: dict[str, str] = {
    "requirements_by_doc": (
        "MATCH (d:Document)-[r:REQUIRES]->(req:Requirement) "
        "WHERE d.id = $doc_id "
        "RETURN d.id AS doc_id, req.id AS req_id, req.text AS text, r.source_chunk_id AS source_chunk_id"
    ),
    "people_by_doc": (
        "MATCH (d:Document)-[r:MENTIONS]->(p:Person) "
        "WHERE d.id = $doc_id "
        "RETURN d.id AS doc_id, p.id AS person_id, p.name AS name, r.source_chunk_id AS source_chunk_id"
    ),
    "affiliations_by_person": (
        "MATCH (p:Person)-[r:AFFILIATED_WITH]->(prj:Project) "
        "WHERE p.name = $person_name OR p.id = $person_id "
        "RETURN p.name AS person_name, prj.title AS project_title, r.source_chunk_id AS source_chunk_id"
    ),
    "funder_projects": (
        "MATCH (f:Funder)-[r:FUNDS]->(prj:Project) "
        "WHERE f.name = $funder_name OR f.id = $funder_id "
        "RETURN f.name AS funder_name, prj.title AS project_title, r.source_chunk_id AS source_chunk_id"
    ),
    "traverse_all": (
        "MATCH (p:Person)-[r:AFFILIATED_WITH]->(prj:Project) "
        "RETURN p.name AS person_name, prj.title AS project_title, r.source_chunk_id AS source_chunk_id"
    ),
    "edge_traceability": (
        "MATCH (a)-[r]->(b) "
        "WHERE r.source_chunk_id = $source_chunk_id "
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
        """Initialize node and relationship tables idempotently."""
        node_schemas = [
            "CREATE NODE TABLE IF NOT EXISTS Document(id STRING, filename STRING, PRIMARY KEY(id));",
            "CREATE NODE TABLE IF NOT EXISTS Requirement(id STRING, text STRING, PRIMARY KEY(id));",
            "CREATE NODE TABLE IF NOT EXISTS Person(id STRING, name STRING, PRIMARY KEY(id));",
            "CREATE NODE TABLE IF NOT EXISTS Project(id STRING, title STRING, PRIMARY KEY(id));",
            "CREATE NODE TABLE IF NOT EXISTS Funder(id STRING, name STRING, PRIMARY KEY(id));",
        ]
        rel_schemas = [
            "CREATE REL TABLE IF NOT EXISTS REQUIRES(FROM Document TO Requirement, source_chunk_id STRING);",
            "CREATE REL TABLE IF NOT EXISTS MENTIONS(FROM Document TO Person, source_chunk_id STRING);",
            "CREATE REL TABLE IF NOT EXISTS AFFILIATED_WITH(FROM Person TO Project, source_chunk_id STRING);",
            "CREATE REL TABLE IF NOT EXISTS FUNDS(FROM Funder TO Project, source_chunk_id STRING);",
        ]

        for stmt in node_schemas:
            try:
                self.conn.execute(stmt)
            except Exception as exc:
                logger.debug("Node table create notice: %s", exc)

        for stmt in rel_schemas:
            try:
                self.conn.execute(stmt)
            except Exception as exc:
                logger.debug("Rel table create notice: %s", exc)

    def add_nodes(self, nodes: Sequence[GraphNode]) -> None:
        """Add graph nodes into the columnar store."""
        for node in nodes:
            label = node.label
            props = dict(node.properties)
            props["id"] = node.node_id

            # Parameterized node insertion
            if label == "Document":
                filename = props.get("filename", "")
                cypher = "MERGE (n:Document {id: $id}) ON CREATE SET n.filename = $filename;"
                self.conn.execute(cypher, {"id": node.node_id, "filename": filename})
            elif label == "Requirement":
                text = props.get("text", "")
                cypher = "MERGE (n:Requirement {id: $id}) ON CREATE SET n.text = $text;"
                self.conn.execute(cypher, {"id": node.node_id, "text": text})
            elif label == "Person":
                name = props.get("name", "")
                cypher = "MERGE (n:Person {id: $id}) ON CREATE SET n.name = $name;"
                self.conn.execute(cypher, {"id": node.node_id, "name": name})
            elif label == "Project":
                title = props.get("title", "")
                cypher = "MERGE (n:Project {id: $id}) ON CREATE SET n.title = $title;"
                self.conn.execute(cypher, {"id": node.node_id, "title": title})
            elif label == "Funder":
                name = props.get("name", "")
                cypher = "MERGE (n:Funder {id: $id}) ON CREATE SET n.name = $name;"
                self.conn.execute(cypher, {"id": node.node_id, "name": name})
            else:
                logger.warning("Unrecognized node label '%s', skipped.", label)

    def add_edges(self, edges: Sequence[GraphEdge]) -> None:
        """Add directed edges with mandatory source chunk provenance."""
        for edge in edges:
            if not edge.source_chunk_id:
                raise StorageError(
                    f"GraphEdge from '{edge.source_id}' to '{edge.target_id}' missing mandatory source_chunk_id."
                )

            rel_type = edge.rel_type.upper()
            params = {
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "source_chunk_id": edge.source_chunk_id,
            }

            if rel_type == "REQUIRES":
                cypher = (
                    "MATCH (a:Document), (b:Requirement) "
                    "WHERE a.id = $source_id AND b.id = $target_id "
                    "CREATE (a)-[:REQUIRES {source_chunk_id: $source_chunk_id}]->(b);"
                )
            elif rel_type == "MENTIONS":
                cypher = (
                    "MATCH (a:Document), (b:Person) "
                    "WHERE a.id = $source_id AND b.id = $target_id "
                    "CREATE (a)-[:MENTIONS {source_chunk_id: $source_chunk_id}]->(b);"
                )
            elif rel_type == "AFFILIATED_WITH":
                cypher = (
                    "MATCH (a:Person), (b:Project) "
                    "WHERE a.id = $source_id AND b.id = $target_id "
                    "CREATE (a)-[:AFFILIATED_WITH {source_chunk_id: $source_chunk_id}]->(b);"
                )
            elif rel_type == "FUNDS":
                cypher = (
                    "MATCH (a:Funder), (b:Project) "
                    "WHERE a.id = $source_id AND b.id = $target_id "
                    "CREATE (a)-[:FUNDS {source_chunk_id: $source_chunk_id}]->(b);"
                )
            else:
                logger.warning("Unsupported relationship type '%s', skipped.", rel_type)
                continue

            try:
                self.conn.execute(cypher, params)
            except Exception as exc:
                logger.error("Failed to insert graph edge: %s", exc)
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
            results: list[dict[str, Any]] = []
            cols = res.get_column_names()
            while res.has_next():
                row = res.get_next()
                results.append(dict(zip(cols, row)))
            return results
        except Exception as exc:
            logger.error("Cypher query execution failed: %s", exc)
            raise StorageError(f"Cypher template '{template_id}' failed: {exc}") from exc

    def delete_document(self, doc_id: str) -> None:
        """Purge all nodes and edges belonging to a given document ID."""
        try:
            # Delete attached requirements and relationships
            self.conn.execute(
                "MATCH (d:Document)-[r:REQUIRES]->(req:Requirement) WHERE d.id = $doc_id DELETE r, req;",
                {"doc_id": doc_id},
            )
            self.conn.execute(
                "MATCH (d:Document)-[r:MENTIONS]->(p:Person) WHERE d.id = $doc_id DELETE r;",
                {"doc_id": doc_id},
            )
            self.conn.execute(
                "MATCH (d:Document) WHERE d.id = $doc_id DELETE d;",
                {"doc_id": doc_id},
            )
            logger.info("Purged graph nodes and edges for doc_id '%s'.", doc_id)
        except Exception as exc:
            logger.error("Failed to delete document '%s' from graph store: %s", doc_id, exc)

    def close(self) -> None:
        """Explicitly release resources for Windows file handle cleanup."""
        try:
            del self.conn
            del self.db
        except Exception:
            pass
