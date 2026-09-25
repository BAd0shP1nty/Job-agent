"""Configurable embedding backends with an explicit setup check.

* ``hashing`` (default) - local, deterministic feature-hashing of word unigrams and
  bigrams with sub-linear TF weighting. No model download, no network, works offline.
* ``sentence-transformers`` - optional; requires ``pip install sentence-transformers``
  and downloads the configured model on first use.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Protocol

_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#/.-]*")
_STOP = set(
    "a an the and or of to in for on with by at from as is are be was were this that these those our your their "
    "you we they it its will can may must should have has had not no into across using use per via etc".split()
)


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def tokenize(text: str) -> list[str]:
    return [t.strip(".-/") for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t.strip(".-/")) > 1]


class HashingEmbedder:
    name = "hashing"

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def _index(self, feature: str) -> tuple[int, float]:
        digest = hashlib.md5(feature.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "little") % self.dim, (1.0 if digest[4] & 1 else -1.0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * self.dim
            tokens = tokenize(text)
            counts: dict[str, int] = {}
            for tok in tokens:
                counts[tok] = counts.get(tok, 0) + 1
            for a, b in zip(tokens, tokens[1:]):
                counts[f"{a}_{b}"] = counts.get(f"{a}_{b}", 0) + 1
            for feature, count in counts.items():
                idx, sign = self._index(feature)
                vec[idx] += sign * (1.0 + math.log(count))
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


class SentenceTransformerEmbedder:  # pragma: no cover - optional dependency
    name = "sentence-transformers"

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name)
        self.dim = self.model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self.model.encode(texts, normalize_embeddings=True)]


@dataclass
class EmbeddingStatus:
    backend: str
    ok: bool
    message: str


def get_embedder(backend: str = "hashing", model_name: str = "all-MiniLM-L6-v2") -> Embedder:
    if backend == "sentence-transformers":
        return SentenceTransformerEmbedder(model_name)
    return HashingEmbedder()


def check_embedding_setup(backend: str, model_name: str) -> EmbeddingStatus:
    """Explicit setup check shown in the GUI."""
    try:
        embedder = get_embedder(backend, model_name)
        vec = embedder.embed(["service delivery manager"])[0]
        return EmbeddingStatus(embedder.name, len(vec) > 0, f"{embedder.name} ready (dim={len(vec)})")
    except ImportError as exc:
        return EmbeddingStatus(backend, False, f"Backend '{backend}' is not installed: {exc}. Falling back to hashing.")
    except Exception as exc:  # pragma: no cover
        return EmbeddingStatus(backend, False, f"Embedding backend failed: {exc}")


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
