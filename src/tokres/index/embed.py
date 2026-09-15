"""Embedders (dense) + sparse BM25 with Korean morphological pre-tokenization. Explicit model selection, no silent fallback."""
from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache
from typing import Protocol

from ..settings import get_settings

DIM = 1024


class Embedder(Protocol):
    name: str
    dim: int

    def embed_docs(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class VoyageEmbedder:
    def __init__(self, model: str):
        import voyageai

        s = get_settings()
        if not s.voyage_api_key:
            raise RuntimeError("VOYAGE_API_KEY required for EMBED_MODEL=" + model)
        self.client = voyageai.Client(api_key=s.voyage_api_key)
        self.name = model
        self.dim = DIM

    def embed_docs(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            out.extend(self.client.embed(texts[i:i + 64], model=self.name, input_type="document", output_dimension=DIM).embeddings)
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.client.embed([text], model=self.name, input_type="query", output_dimension=DIM).embeddings[0]


class FastembedEmbedder:
    """Local multilingual-e5-large via fastembed (ONNX, no torch). ~2.2GB download on first use.
    e5 expects "query: " / "passage: " prefixes."""

    def __init__(self, model: str = "intfloat/multilingual-e5-large"):
        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name=model)
        self.name = "e5-large"
        self.dim = DIM

    def embed_docs(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self.model.embed([f"passage: {t}" for t in texts], batch_size=16)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self.model.embed([f"query: {text}"]))).tolist()


class HashEmbedder:
    """Deterministic pseudo-embedding for tests (no model download)."""

    name = "hash"
    dim = DIM

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * DIM
        for tok in re.findall(r"[가-힣a-z0-9]+", text.lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % DIM] += 1.0
            v[(h >> 16) % DIM] += 0.5
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed_docs(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


@lru_cache
def get_embedder(name: str | None = None) -> Embedder:
    name = name or get_settings().embed_model
    if name.startswith("voyage"):
        return VoyageEmbedder(name)
    if name in ("e5-large", "multilingual-e5-large", "local"):
        return FastembedEmbedder()
    if name == "hash":
        return HashEmbedder()
    raise ValueError(f"unknown EMBED_MODEL {name}")


# ---------- sparse (BM25) with Korean morphemes ----------
@lru_cache
def _kiwi():
    from kiwipiepy import Kiwi

    return Kiwi()


_KEEP_TAGS = ("NN", "SL", "SN", "SH", "VV", "VA", "XR")


def ko_tokenize(text: str) -> str:
    """Return whitespace-joined tokens: Korean morphemes (nouns/verbs/foreign/numbers) + latin words lowercased."""
    if not re.search(r"[가-힣]", text):
        return text.lower()
    toks = []
    for t in _kiwi().tokenize(text):
        if t.tag.startswith(_KEEP_TAGS):
            toks.append(t.form.lower())
    return " ".join(toks)


class SparseEncoder:
    def __init__(self):
        from fastembed import SparseTextEmbedding

        self.model = SparseTextEmbedding(model_name="Qdrant/bm25")

    def encode_docs(self, texts: list[str]):
        return list(self.model.embed([ko_tokenize(t) for t in texts]))

    def encode_query(self, text: str):
        return next(iter(self.model.query_embed(ko_tokenize(text))))


@lru_cache
def get_sparse() -> SparseEncoder:
    return SparseEncoder()
