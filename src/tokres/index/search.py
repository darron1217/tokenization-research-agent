"""Search helpers over the Qdrant store + SQLite metadata (used by knowledge.api and enrich priors)."""
from __future__ import annotations

from datetime import datetime

from ..db import Database
from ..llm.analyzer import PriorDoc
from ..models import Item
from .qdrant_store import QdrantStore, epoch, get_store


def hybrid_search(db: Database, query: str, *, k: int = 10, since: str | None = None, until: str | None = None,
                  entities: list[str] | None = None, tiers: list[str] | None = None, region: str | None = None,
                  doc_types: list[str] | None = None, store: QdrantStore | None = None) -> list[dict]:
    store = store or get_store()
    flt = store.build_filter(since=epoch(since), until=epoch(until), entities=entities, tiers=tiers, region=region, doc_types=doc_types)
    points = store.search(query, k=k * 4, flt=flt)
    # Aggregate chunks → items (max score, keep best chunk text), then rank
    best: dict[int, dict] = {}
    for p in points:
        pl = p.payload or {}
        iid = int(pl["item_id"])
        if iid not in best or p.score > best[iid]["score"]:
            best[iid] = {"item_id": iid, "score": float(p.score), "chunk": pl.get("text", ""), "chunk_index": pl.get("chunk_index"),
                         "title": pl.get("title"), "entity": pl.get("entity"), "tier": pl.get("source_tier"),
                         "date": pl.get("date"), "doc_type": pl.get("doc_type"), "priority": pl.get("priority"),
                         "url": pl.get("canonical_url"), "region": pl.get("region")}
    hits = sorted(best.values(), key=lambda h: -h["score"])[:k]
    if hits:
        rows = db.q(f"SELECT i.id, i.url, a.summary_ko FROM items i LEFT JOIN analyses a ON a.item_id=i.id WHERE i.id IN ({','.join('?' * len(hits))})",
                    [h["item_id"] for h in hits])
        meta = {r["id"]: r for r in rows}
        for h in hits:
            m = meta.get(h["item_id"])
            if m:
                h["url"] = m["url"]
                h["summary"] = m["summary_ko"]
    return hits


def similar_items(db: Database, it: Item, k: int = 5, before: datetime | None = None, store: QdrantStore | None = None) -> list[PriorDoc]:
    """Nearest earlier documents by summary/title embedding (for novelty judgement)."""
    store = store or get_store()
    q = f"{it.title}\n{(it.content_text or it.snippet or '')[:1500]}"
    flt = store.build_filter(until=epoch(before) if before else None, exclude_item=it.id, kinds=["summary"])
    pts = store.search_dense(store.embedder.embed_query(q), k=k * 2, flt=flt)
    out: list[PriorDoc] = []
    seen: set[int] = set()
    for p in pts:
        pl = p.payload or {}
        iid = int(pl["item_id"])
        if iid in seen:
            continue
        seen.add(iid)
        out.append(PriorDoc(iid, pl.get("title", ""), pl.get("date"), pl.get("text", "")[:400]))
        if len(out) >= k:
            break
    return out
