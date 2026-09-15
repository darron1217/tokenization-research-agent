"""Lightweight knowledge structure: entities + relations in SQLite (no graph DB)."""
from __future__ import annotations

import json

from ..config import institution_lookup
from ..db import Database
from ..llm.schemas import EnrichResult
from ..models import Item


def canonical_entity(name: str) -> str:
    n = name.strip()
    hit = institution_lookup().get(n.lower())
    return hit[0] if hit else n


def get_or_create_entity(db: Database, name: str, etype: str) -> int:
    canon = canonical_entity(name)
    row = db.q1("SELECT id, aliases FROM entities WHERE canonical=?", (canon,))
    if row:
        if name != canon:
            aliases = set(json.loads(row["aliases"] or "[]"))
            if name not in aliases:
                aliases.add(name)
                db.x("UPDATE entities SET aliases=? WHERE id=?", (json.dumps(sorted(aliases), ensure_ascii=False), row["id"]))
        return int(row["id"])
    # alias match
    row = db.q1("SELECT id FROM entities WHERE aliases LIKE ?", (f'%"{name}"%',))
    if row:
        return int(row["id"])
    cur = db.x("INSERT INTO entities(canonical, type, aliases) VALUES (?,?,?)",
               (canon, etype, json.dumps([name] if name != canon else [], ensure_ascii=False)))
    return int(cur.lastrowid)


def upsert_item_knowledge(db: Database, item: Item, res: EnrichResult) -> None:
    db.x("DELETE FROM item_entities WHERE item_id=?", (item.id,))
    db.x("DELETE FROM relations WHERE item_id=?", (item.id,))
    ids: dict[str, int] = {}
    for e in res.entities:
        ids[e.name] = get_or_create_entity(db, e.name, e.type)
        db.x("INSERT OR IGNORE INTO item_entities(item_id, entity_id, role) VALUES (?,?,?)", (item.id, ids[e.name], "mentioned"))
    if item.entity:
        eid = get_or_create_entity(db, item.entity, "org")
        db.x("INSERT OR REPLACE INTO item_entities(item_id, entity_id, role) VALUES (?,?,?)", (item.id, eid, "publisher"))
    date = (res.effective_date or (item.published_at.date().isoformat() if item.published_at else None))
    for r in res.relations:
        s_id = ids.get(r.subject) or get_or_create_entity(db, r.subject, "org")
        o_id = ids.get(r.object) or get_or_create_entity(db, r.object, "project")
        db.x("INSERT OR IGNORE INTO relations(subject_id, predicate, object_id, item_id, date) VALUES (?,?,?,?,?)",
             (s_id, r.predicate, o_id, item.id, date))


def entity_timeline(db: Database, name: str, since: str | None = None, limit: int = 50) -> list[dict]:
    canon = canonical_entity(name)
    row = db.q1("SELECT id FROM entities WHERE canonical=? OR aliases LIKE ?", (canon, f'%"{name}"%'))
    if not row:
        return []
    eid = row["id"]
    params: list = [eid, eid]
    where = ""
    if since:
        where = " AND i.published_at >= ?"
        params.append(since)
    rows = db.q(f"""SELECT DISTINCT i.id, i.title, i.published_at, i.entity, i.source_tier, i.url, a.summary_ko,
                           s.priority
                    FROM items i LEFT JOIN analyses a ON a.item_id=i.id LEFT JOIN scores s ON s.item_id=i.id
                    WHERE (i.id IN (SELECT item_id FROM item_entities WHERE entity_id=?)
                           OR i.id IN (SELECT item_id FROM relations WHERE subject_id=? OR object_id=?))
                          AND i.status IN ('enriched','indexed') {where}
                    ORDER BY i.published_at DESC LIMIT ?""", (eid, eid, eid, *params[2:], limit))
    out = []
    for r in rows:
        rels = db.q("""SELECT es.canonical s, r.predicate p, eo.canonical o FROM relations r
                       JOIN entities es ON es.id=r.subject_id JOIN entities eo ON eo.id=r.object_id WHERE r.item_id=?""", (r["id"],))
        out.append({"item_id": r["id"], "date": (r["published_at"] or "")[:10], "title": r["title"], "entity": r["entity"],
                    "tier": r["source_tier"], "priority": r["priority"], "url": r["url"], "summary": r["summary_ko"],
                    "relations": [f"{x['s']} {x['p']} {x['o']}" for x in rels]})
    return out
