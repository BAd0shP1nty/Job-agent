"""Vector stores.

The default is an in-memory store: the datasets involved (one resume and one job
description at a time) are small, so a database is unnecessary. ChromaDB can be
enabled with ``USE_CHROMA=true`` to persist resume chunks under
``data/vector_store`` if the corpus grows.
"""
from __future__ import annotations

from typing import Protocol

from rag.chunking import Chunk
from rag.embeddings import Embedder, cosine


class VectorStore(Protocol):
    def add(self, chunks: list[Chunk]) -> None: ...

    def query(self, text: str, k: int = 5, document_prefix: str | None = None) -> list[tuple[Chunk, float]]: ...


class InMemoryVectorStore:
    def __init__(self, embedder: Embedder):
        self.embedder = embedder
        self._items: list[tuple[Chunk, list[float]]] = []

    def add(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        vectors = self.embedder.embed([c.text for c in chunks])
        self._items.extend(zip(chunks, vectors))

    def query(self, text: str, k: int = 5, document_prefix: str | None = None) -> list[tuple[Chunk, float]]:
        qv = self.embedder.embed([text])[0]
        scored = [(c, cosine(qv, v)) for c, v in self._items
                  if document_prefix is None or c.document.startswith(document_prefix)]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:k]


class ChromaVectorStore:  # pragma: no cover - optional dependency
    def __init__(self, embedder: Embedder, path: str, collection: str = "resume"):
        import chromadb

        self.embedder = embedder
        self.client = chromadb.PersistentClient(path=path)
        self.collection = self.client.get_or_create_collection(collection)
        self._chunks: dict[str, Chunk] = {}

    def add(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        self.collection.upsert(
            ids=[c.chunk_id for c in chunks],
            documents=[c.text for c in chunks],
            embeddings=self.embedder.embed([c.text for c in chunks]),
            metadatas=[{"document": c.document, "section": c.section or "", "start": c.start or 0,
                        "end": c.end or 0} for c in chunks],
        )
        self._chunks.update({c.chunk_id: c for c in chunks})

    def query(self, text: str, k: int = 5, document_prefix: str | None = None) -> list[tuple[Chunk, float]]:
        res = self.collection.query(query_embeddings=self.embedder.embed([text]), n_results=k)
        out = []
        for cid, dist in zip(res["ids"][0], res["distances"][0]):
            chunk = self._chunks.get(cid)
            if chunk and (document_prefix is None or chunk.document.startswith(document_prefix)):
                out.append((chunk, 1.0 - float(dist)))
        return out
