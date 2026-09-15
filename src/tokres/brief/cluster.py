"""Cluster near-duplicate items (same story via multiple sources) for a brief.
Strategy: title similarity (token Jaccard on normalized titles) + same ±3 days, plus explicit links
(promoted_from / updates_item_id). Vector similarity is used when an embedder is available."""
from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import datetime

from ..db import Database
from ..models import TIER_ORDER

_WORD = re.compile(r"[가-힣a-z0-9]+")
_STOP = {"the", "of", "and", "for", "to", "in", "on", "a", "an", "및", "등", "관련", "발표", "with", "by"}


def _tokens(title: str) -> set[str]:
    return {w for w in _WORD.findall(title.lower()) if w not in _STOP and len(w) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cluster_items(rows: list[dict], sim_threshold: float = 0.5, embed_sims: dict[tuple[int, int], float] | None = None,
                  embed_threshold: float = 0.9) -> list[list[dict]]:
    """rows: dicts with id, title, published_at (datetime|None), source_tier, extra(promoted_from), updates_item_id."""
    parent: dict[int, int] = {r["id"]: r["id"] for r in rows}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    ids = {r["id"] for r in rows}
    toks = {r["id"]: _tokens(r["title"]) for r in rows}
    for r in rows:
        pf = r.get("promoted_from")
        if isinstance(pf, int) and pf in ids:
            union(r["id"], pf)
        up = r.get("updates_item_id")
        if isinstance(up, int) and up in ids and r.get("novelty") == "repeat":
            union(r["id"], up)
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            da, dbb = a.get("published_at"), b.get("published_at")
            if da and dbb and abs((da - dbb).days) > 3:
                continue
            sim = _jaccard(toks[a["id"]], toks[b["id"]])
            es = (embed_sims or {}).get((a["id"], b["id"])) or (embed_sims or {}).get((b["id"], a["id"])) or 0.0
            if sim >= sim_threshold or es >= embed_threshold:
                union(a["id"], b["id"])
    groups: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        groups[find(r["id"])].append(r)
    out = []
    for g in groups.values():
        # head = best tier, then primary_source, then earliest published
        g.sort(key=lambda r: (TIER_ORDER.get(r["source_tier"], 9), not r.get("primary_source"),
                              r.get("published_at") or datetime.max))
        out.append(g)
    return out


def persist_clusters(db: Database, groups: list[list[dict]], brief_date: str) -> None:
    for g in groups:
        cid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"cluster:{brief_date}:{g[0]['id']}"))
        for i, r in enumerate(g):
            db.x("INSERT OR REPLACE INTO clusters(cluster_id, item_id, is_head, brief_date) VALUES (?,?,?,?)",
                 (cid, r["id"], int(i == 0), brief_date))
