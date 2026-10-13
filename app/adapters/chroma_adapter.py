"""ChromaDB implementation of VectorStorePort with disabled ONNX embedding function."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.core.errors import StorageError
from app.ports.models import ChunkVector, VectorSearchResult
from app.ports.vector_store import VectorStorePort

logger = logging.getLogger("grantloom.adapters.chroma_adapter")

COLLECTION_NAME = "grantloom_chunks"


class ChromaVectorStoreAdapter(VectorStorePort):
    """In-process ChromaDB vector store with default ONNX embedder explicitly disabled."""

    def __init__(self, chroma_path: str | Path) -> None:
        path_obj = Path(chroma_path)
        path_obj.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(path_obj),
            settings=ChromaSettings(anonymized_telemetry=False, is_persistent=True),
        )
        # embedding_function=None explicitly disables the default ~250MB ONNX embedder
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=None,
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(self, chunks: Sequence[ChunkVector]) -> None:
        """Persist chunk vectors and metadata into ChromaDB index."""
        if not chunks:
            return

        ids: list[str] = []
        embeddings: list[list[float]] = []
        documents: list[str] = []
        metadatas: list[dict] = []

        for chunk in chunks:
            ids.append(chunk.chunk_id)
            embeddings.append(chunk.embedding)
            documents.append(chunk.text)
            # Ensure metadata keys are primitives supported by Chroma
            clean_meta = {"doc_id": chunk.doc_id}
            for k, v in chunk.metadata.items():
                if isinstance(v, (str, int, float, bool)):
                    clean_meta[k] = v
                elif v is not None:
                    clean_meta[k] = str(v)
            metadatas.append(clean_meta)

        try:
            self.collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )
        except Exception as exc:
            raise StorageError(f"Failed to index {len(chunks)} vectors in ChromaDB: {exc}") from exc

    def query(self, vector: Sequence[float], top_k: int) -> list[VectorSearchResult]:
        """Query top-k nearest neighbors by cosine similarity."""
        try:
            total = self.collection.count()
            if total == 0:
                return []
            effective_k = min(top_k, total)

            results = self.collection.query(
                query_embeddings=[list(vector)],
                n_results=effective_k,
                include=["documents", "metadatas", "distances"],
            )

            out: list[VectorSearchResult] = []
            ids = results.get("ids", [[]])[0]
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for i in range(len(ids)):
                dist = distances[i] if i < len(distances) and distances[i] is not None else 1.0
                # In cosine space, similarity = 1.0 - distance
                score = max(0.0, min(1.0, 1.0 - dist))
                meta = metas[i] if i < len(metas) and metas[i] is not None else {}
                doc_id = str(meta.get("doc_id", ""))
                out.append(
                    VectorSearchResult(
                        chunk_id=ids[i],
                        doc_id=doc_id,
                        score=score,
                        text=docs[i] if i < len(docs) else "",
                        metadata=meta,
                    )
                )
            return out
        except Exception as exc:
            raise StorageError(f"ChromaDB vector query failed: {exc}") from exc

    def delete_document(self, doc_id: str) -> None:
        """Purge all chunk vectors associated with a document ID."""
        try:
            self.collection.delete(where={"doc_id": doc_id})
        except Exception as exc:
            raise StorageError(f"Failed to delete ChromaDB vectors for document '{doc_id}': {exc}") from exc

    def count(self) -> int:
        """Return total indexed vectors in collection."""
        try:
            return self.collection.count()
        except Exception as exc:
            raise StorageError(f"Failed to get ChromaDB collection count: {exc}") from exc
