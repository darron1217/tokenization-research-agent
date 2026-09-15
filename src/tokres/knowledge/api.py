"""Single internal API used by `tokres search/ask` and the MCP server. All results carry citations (item_id, url, tier, date)."""
from __future__ import annotations

import json
import logging
from typing import Any

from ..db import Database
from ..settings import get_settings
from .entities import entity_timeline as _timeline

log = logging.getLogger(__name__)


def _fallback_fts(db: Database, query: str, k: int, tiers: list[str] | None, since: str | None, entities: list[str] | None) -> list[dict]:
    """SQLite LIKE fallback when Qdrant is unavailable (keeps the CLI/MCP usable)."""
    terms = [t for t in query.replace('"', " ").split() if len(t) > 1][:6]
    where = ["i.status IN ('enriched','indexed')"]
    params: list[Any] = []
    for t in terms:
        where.append("(i.title LIKE ? OR a.summary_ko LIKE ? OR i.content_text LIKE ?)")
        params += [f"%{t}%"] * 3
    if tiers:
        where.append(f"i.source_tier IN ({','.join('?' * len(tiers))})")
        params += tiers
    if since:
        where.append("i.published_at >= ?")
        params.append(since)
    if entities:
        where.append(f"i.entity IN ({','.join('?' * len(entities))})")
        params += entities
    rows = db.q(f"""SELECT i.id, i.title, i.url, i.entity, i.source_tier, i.published_at, a.summary_ko, s.priority, t.doc_type
                    FROM items i LEFT JOIN analyses a ON a.item_id=i.id LEFT JOIN scores s ON s.item_id=i.id LEFT JOIN triage t ON t.item_id=i.id
                    WHERE {' AND '.join(where)} ORDER BY i.published_at DESC LIMIT ?""", (*params, k))
    return [{"item_id": r["id"], "score": 0.0, "title": r["title"], "url": r["url"], "entity": r["entity"], "tier": r["source_tier"],
             "date": (r["published_at"] or "")[:10] or None, "summary": r["summary_ko"], "priority": r["priority"],
             "doc_type": r["doc_type"], "chunk": None, "backend": "sqlite-like"} for r in rows]


def search_documents(db: Database, query: str, *, k: int = 10, since: str | None = None, until: str | None = None,
                     entities: list[str] | None = None, tiers: list[str] | None = None, region: str | None = None,
                     doc_types: list[str] | None = None) -> list[dict]:
    try:
        from ..index.search import hybrid_search

        hits = hybrid_search(db, query, k=k, since=since, until=until, entities=entities, tiers=tiers, region=region, doc_types=doc_types)
        for h in hits:
            h["backend"] = "qdrant-hybrid"
        return hits
    except Exception as e:  # noqa: BLE001
        log.warning("vector search unavailable (%r); falling back to SQLite", e)
        return _fallback_fts(db, query, k, tiers, since, entities)


def get_document(db: Database, item_id: int, full_text: bool = False, max_chars: int = 20000) -> dict | None:
    r = db.q1("""SELECT i.*, t.relevance, t.doc_type, t.impact_type, t.topics, a.summary_ko, a.key_facts, a.why_it_matters,
                        a.what_changed, a.materiality, a.materiality_reason, a.novelty, a.updates_item_id, a.confidence,
                        a.entities, a.relations, a.effective_date, s.priority, s.final_score
                 FROM items i LEFT JOIN triage t ON t.item_id=i.id LEFT JOIN analyses a ON a.item_id=i.id
                 LEFT JOIN scores s ON s.item_id=i.id WHERE i.id=?""", (item_id,))
    if not r:
        return None
    d = dict(r)
    d.pop("extra_json", None)
    text = d.pop("content_text", None) or ""
    for k in ("key_facts", "entities", "relations", "topics"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except Exception:  # noqa: BLE001
                pass
    d["date"] = (d.get("published_at") or "")[:10] or None
    d["tier"] = d.pop("source_tier")
    d["text_chars"] = len(text)
    if full_text:
        d["text"] = text[:max_chars] + ("\n[...truncated]" if len(text) > max_chars else "")
    return d


def entity_timeline(db: Database, entity: str, since: str | None = None, limit: int = 50) -> list[dict]:
    return _timeline(db, entity, since, limit)


def list_entities(db: Database, etype: str | None = None, query: str | None = None, limit: int = 100) -> list[dict]:
    where, params = [], []
    if etype:
        where.append("e.type=?")
        params.append(etype)
    if query:
        where.append("(e.canonical LIKE ? OR e.aliases LIKE ?)")
        params += [f"%{query}%", f"%{query}%"]
    rows = db.q(f"""SELECT e.id, e.canonical, e.type, e.aliases, COUNT(ie.item_id) n FROM entities e
                    LEFT JOIN item_entities ie ON ie.entity_id=e.id {('WHERE ' + ' AND '.join(where)) if where else ''}
                    GROUP BY e.id ORDER BY n DESC LIMIT ?""", (*params, limit))
    return [{"id": r["id"], "name": r["canonical"], "type": r["type"], "aliases": json.loads(r["aliases"] or "[]"), "documents": r["n"]} for r in rows]


def recent_briefs(db: Database, days: int = 7, min_priority: str = "fyi") -> list[dict]:
    from ..scoring.rules import PRIORITY_RANK

    rows = db.q("""SELECT i.id, i.title, i.url, i.entity, i.source_tier, i.published_at, i.brief_date, a.summary_ko, a.why_it_matters,
                          s.priority, t.doc_type
                   FROM items i JOIN scores s ON s.item_id=i.id LEFT JOIN analyses a ON a.item_id=i.id LEFT JOIN triage t ON t.item_id=i.id
                   WHERE i.brief_date IS NOT NULL AND i.brief_date != 'backfill' AND i.brief_date >= date('now', ?)
                   ORDER BY i.brief_date DESC, s.final_score DESC""", (f"-{int(days)} days",))
    out = [dict(r) for r in rows if PRIORITY_RANK.get(r["priority"], 0) >= PRIORITY_RANK.get(min_priority, 1)]
    for d in out:
        d["item_id"] = d.pop("id")
        d["tier"] = d.pop("source_tier")
        d["date"] = (d.get("published_at") or "")[:10] or None
    return out


def get_dossier(slug: str) -> str | None:
    p = get_settings().dossiers_dir / f"{slug}.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def list_dossiers(db: Database) -> list[dict]:
    return [dict(r) for r in db.q("SELECT slug, kind, title, updated_at FROM dossiers ORDER BY updated_at DESC")]


def rate_document(db: Database, item_id: int, rating: str, note: str | None = None) -> dict:
    from ..db import utcnow

    if rating not in ("up", "down"):
        raise ValueError("rating must be up|down")
    with db.tx():
        db.x("INSERT INTO feedback(item_id, rating, note, created_at) VALUES (?,?,?,?)", (item_id, rating, note, utcnow()))
    return {"item_id": item_id, "rating": rating, "ok": True}
