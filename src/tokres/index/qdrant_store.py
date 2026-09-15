"""Qdrant hybrid store: named dense + sparse vectors, RRF fusion, payload filters."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from ..settings import get_settings
from .embed import Embedder, get_embedder, get_sparse


def collection_name(embedder_name: str) -> str:
    return "tokdocs_" + embedder_name.replace("/", "_").replace("-", "_").replace(".", "_")


def point_id(item_id: int, chunk_index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"tokres:{item_id}:{chunk_index}"))


def epoch(d: datetime | str | None) -> int | None:
    if not d:
        return None
    if isinstance(d, str):
        d = datetime.fromisoformat(d)
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return int(d.timestamp())


class QdrantStore:
    def __init__(self, client: QdrantClient | None = None, embedder: Embedder | None = None, *, sparse: bool = True):
        s = get_settings()
        self.client = client or QdrantClient(url=s.qdrant_url, timeout=60)
        self.embedder = embedder or get_embedder()
        self.collection = collection_name(self.embedder.name)
        self.use_sparse = sparse
        self._sparse = None
        self.ensure_collection()

    @property
    def sparse(self):
        if self._sparse is None and self.use_sparse:
            self._sparse = get_sparse()
        return self._sparse

    def ensure_collection(self) -> None:
        if self.client.collection_exists(self.collection):
            return
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config={"dense": qm.VectorParams(size=self.embedder.dim, distance=qm.Distance.COSINE)},
            sparse_vectors_config={"sparse": qm.SparseVectorParams(modifier=qm.Modifier.IDF)} if self.use_sparse else None,
        )
        for field, schema in (("entity", qm.PayloadSchemaType.KEYWORD), ("region", qm.PayloadSchemaType.KEYWORD),
                              ("source_tier", qm.PayloadSchemaType.KEYWORD), ("doc_type", qm.PayloadSchemaType.KEYWORD),
                              ("item_id", qm.PayloadSchemaType.INTEGER), ("published_at", qm.PayloadSchemaType.INTEGER),
                              ("kind", qm.PayloadSchemaType.KEYWORD)):
            try:
                self.client.create_payload_index(self.collection, field, schema)
            except Exception:  # noqa: BLE001 - in-memory client may not support it
                pass

    def upsert_chunks(self, item_id: int, chunks: list[dict[str, Any]], payload_base: dict[str, Any]) -> int:
        """chunks: [{index, text, embed_text}] — embed_text includes header+context, text is body."""
        if not chunks:
            return 0
        dense = self.embedder.embed_docs([c["embed_text"] for c in chunks])
        sparse = self.sparse.encode_docs([c["embed_text"] for c in chunks]) if self.use_sparse else None
        points = []
        for i, c in enumerate(chunks):
            vec: dict[str, Any] = {"dense": dense[i]}
            if sparse is not None:
                sv = sparse[i]
                vec["sparse"] = qm.SparseVector(indices=sv.indices.tolist(), values=sv.values.tolist())
            points.append(qm.PointStruct(id=point_id(item_id, c["index"]), vector=vec,
                                         payload={**payload_base, "item_id": item_id, "chunk_index": c["index"],
                                                  "text": c["text"][:4000], "kind": c.get("kind", "chunk")}))
        self.client.upsert(self.collection, points=points, wait=True)
        return len(points)

    def delete_item(self, item_id: int) -> None:
        self.client.delete(self.collection, points_selector=qm.FilterSelector(
            filter=qm.Filter(must=[qm.FieldCondition(key="item_id", match=qm.MatchValue(value=item_id))])))

    @staticmethod
    def build_filter(*, since: int | None = None, until: int | None = None, entities: list[str] | None = None,
                     tiers: list[str] | None = None, region: str | None = None, doc_types: list[str] | None = None,
                     exclude_item: int | None = None, kinds: list[str] | None = None) -> qm.Filter | None:
        must: list[Any] = []
        if since is not None or until is not None:
            must.append(qm.FieldCondition(key="published_at", range=qm.Range(gte=since, lte=until)))
        if entities:
            must.append(qm.FieldCondition(key="entity", match=qm.MatchAny(any=entities)))
        if tiers:
            must.append(qm.FieldCondition(key="source_tier", match=qm.MatchAny(any=tiers)))
        if region:
            must.append(qm.FieldCondition(key="region", match=qm.MatchValue(value=region)))
        if doc_types:
            must.append(qm.FieldCondition(key="doc_type", match=qm.MatchAny(any=doc_types)))
        if kinds:
            must.append(qm.FieldCondition(key="kind", match=qm.MatchAny(any=kinds)))
        must_not: list[Any] = []
        if exclude_item is not None:
            must_not.append(qm.FieldCondition(key="item_id", match=qm.MatchValue(value=exclude_item)))
        if not must and not must_not:
            return None
        return qm.Filter(must=must or None, must_not=must_not or None)

    def search(self, query: str, k: int = 20, flt: qm.Filter | None = None, prefetch: int = 60) -> list[qm.ScoredPoint]:
        dense_q = self.embedder.embed_query(query)
        pre = [qm.Prefetch(query=dense_q, using="dense", limit=prefetch, filter=flt)]
        if self.use_sparse:
            sq = self.sparse.encode_query(query)
            pre.append(qm.Prefetch(query=qm.SparseVector(indices=sq.indices.tolist(), values=sq.values.tolist()),
                                   using="sparse", limit=prefetch, filter=flt))
        res = self.client.query_points(self.collection, prefetch=pre, query=qm.FusionQuery(fusion=qm.Fusion.RRF),
                                       limit=k, with_payload=True, query_filter=flt)
        return res.points

    def search_dense(self, vector: list[float], k: int, flt: qm.Filter | None = None) -> list[qm.ScoredPoint]:
        return self.client.query_points(self.collection, query=vector, using="dense", limit=k, with_payload=True,
                                        query_filter=flt).points


@lru_cache
def get_store() -> QdrantStore:
    return QdrantStore()
