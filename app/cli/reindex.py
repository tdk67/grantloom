"""Rebuild the dense vector index for the configured embedder.

Usage: python -m app.cli.reindex
Needed after changing models.embedding_backend / embedding_model (the vector store refuses to mix
embedding spaces). Re-embeds the stored chunks of every active document; nothing is re-extracted.
"""

from __future__ import annotations

import asyncio
import sys

from app.adapters.chroma_adapter import ChromaVectorStoreAdapter, drop_collection
from app.adapters.sqlite_registry import SqliteRegistryAdapter
from app.core.config import get_settings
from app.ports.models import ChunkVector
from app.services.inference import build_embedder


async def reindex() -> int:
    settings = get_settings()
    embedder = build_embedder(settings)
    drop_collection(settings.database.chroma_path)
    store = ChromaVectorStoreAdapter(settings.database.chroma_path, embedder.fingerprint)
    registry = SqliteRegistryAdapter(settings.database.sqlite_path)
    total = 0
    for doc in registry.list_documents(state="active"):
        chunks = registry.get_chunks_for_document(doc.doc_id)
        for offset in range(0, len(chunks), settings.ingestion.embed_batch_size):
            batch = chunks[offset : offset + settings.ingestion.embed_batch_size]
            vectors = await embedder.embed_batch([c.text_content for c in batch])
            store.add_chunks([
                ChunkVector(chunk_id=c.chunk_id, doc_id=c.doc_id, embedding=v, text=c.text_content, metadata=c.metadata)
                for c, v in zip(batch, vectors)
            ])
        total += len(chunks)
        print(f"Re-embedded {len(chunks):4d} chunks of {doc.filename}")
    print(f"Vector index rebuilt with '{embedder.fingerprint}' ({total} chunks).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(reindex()))
