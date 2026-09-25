"""Hybrid retrieval: semantic similarity + exact skill/keyword matching."""
from __future__ import annotations

from dataclasses import dataclass

from rag.chunking import Chunk, chunk_job, chunk_resume
from rag.embeddings import get_embedder, tokenize
from rag.vector_store import InMemoryVectorStore
from screening.skill_filter import _pattern, terms_for_skill


@dataclass
class RetrievedEvidence:
    chunk: Chunk
    score: float
    semantic: float
    keyword: float
    matched_terms: list[str]


class ResumeIndex:
    """Index of the active resume, built once per search run."""

    def __init__(self, resume_text: str, sections: list[dict] | None = None, backend: str = "hashing",
                 model_name: str = "all-MiniLM-L6-v2"):
        self.embedder = get_embedder(backend, model_name)
        self.chunks = chunk_resume(resume_text, sections)
        self.store = InMemoryVectorStore(self.embedder)
        self.store.add(self.chunks)

    def retrieve(self, query: str, skills: list[str], k: int = 4, alpha: float = 0.6) -> list[RetrievedEvidence]:
        return hybrid_rank(self.store.query(query, k=max(k * 3, 10)), query, skills, k, alpha)


def hybrid_rank(candidates: list[tuple[Chunk, float]], query: str, skills: list[str], k: int,
                alpha: float) -> list[RetrievedEvidence]:
    query_tokens = set(tokenize(query))
    results = []
    for chunk, semantic in candidates:
        matched = [s for s in skills if any(_pattern(t).search(chunk.text) for t in terms_for_skill(s))]
        chunk_tokens = set(tokenize(chunk.text))
        overlap = len(query_tokens & chunk_tokens) / (len(query_tokens) or 1)
        keyword = min(1.0, 0.25 * len(matched) + 0.5 * overlap)
        results.append(RetrievedEvidence(chunk, alpha * max(semantic, 0.0) + (1 - alpha) * keyword, semantic,
                                          keyword, matched))
    results.sort(key=lambda r: r.score, reverse=True)
    return [r for r in results[:k] if r.score > 0.05]


def retrieve_job_evidence(description: str, job_key: str, skills: list[str], query: str,
                          backend: str = "hashing", k: int = 5) -> list[RetrievedEvidence]:
    """Pick the listing passages most relevant to the candidate's confirmed skills."""
    chunks = chunk_job(description, job_key)
    store = InMemoryVectorStore(get_embedder(backend))
    store.add(chunks)
    return hybrid_rank(store.query(query, k=max(k * 3, 10)), query, skills, k, alpha=0.5)
