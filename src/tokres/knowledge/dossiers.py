"""Living dossiers: per institution/project/topic Markdown, incrementally updated from new must_read/notable items."""
from __future__ import annotations

import re

from ..config import load_topics
from ..db import Database, utcnow
from ..llm.analyzer import Analyzer, BriefItemForLLM
from ..models import RunStats
from ..settings import get_settings


def slugify(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z가-힣]+", "-", name).strip("-").lower()
    return s or "x"


def _targets(db: Database) -> list[tuple[str, str, str]]:
    """(slug, kind, title): institutions from topics.yaml + projects with >=2 documents + topics."""
    out = [(slugify(k), "org", k) for k in load_topics()["institutions"]]
    for r in db.q("""SELECT e.canonical, COUNT(*) n FROM entities e JOIN item_entities ie ON ie.entity_id=e.id
                     WHERE e.type='project' GROUP BY e.id HAVING n >= 2"""):
        out.append((slugify(r["canonical"]), "project", r["canonical"]))
    for k, v in load_topics()["topics"].items():
        out.append((k, "topic", v["ko"]))
    return out


def _new_docs(db: Database, kind: str, title: str, key: str, after_item: int | None) -> list[BriefItemForLLM]:
    params: list = []
    if kind in ("org", "project"):
        where = """(i.entity = ? OR i.id IN (SELECT ie.item_id FROM item_entities ie JOIN entities e ON e.id=ie.entity_id
                    WHERE e.canonical = ? OR e.aliases LIKE ?))"""
        params += [title, title, f'%"{title}"%']
    else:
        where = "t.topics LIKE ?"
        params.append(f'%"{key}"%')
    if after_item:
        where += " AND i.id > ?"
        params.append(after_item)
    rows = db.q(f"""SELECT i.id, i.title, i.entity, i.source_tier, i.published_at, s.priority, a.summary_ko, a.why_it_matters
                    FROM items i JOIN analyses a ON a.item_id=i.id JOIN scores s ON s.item_id=i.id LEFT JOIN triage t ON t.item_id=i.id
                    WHERE {where} AND s.priority IN ('must_read','notable') ORDER BY i.published_at ASC LIMIT 40""", params)
    return [BriefItemForLLM(r["id"], f"{(r['published_at'] or '')[:10]} {r['title']}", r["entity"], r["source_tier"], r["priority"],
                            (r["summary_ko"] or "") + " / " + (r["why_it_matters"] or "")) for r in rows]


def update_dossiers(db: Database, analyzer: Analyzer, rebuild: bool = False) -> RunStats:
    stats = RunStats(stage="dossiers")
    ddir = get_settings().dossiers_dir
    for slug, kind, title in _targets(db):
        row = db.q1("SELECT last_item_id FROM dossiers WHERE slug=?", (slug,))
        after = None if rebuild else (row["last_item_id"] if row else None)
        docs = _new_docs(db, kind, title, slug, after)
        if not docs:
            continue
        path = ddir / f"{slug}.md"
        prev = "" if rebuild else (path.read_text(encoding="utf-8") if path.exists() else "")
        try:
            res = analyzer.dossier_update(title, prev, docs)
        except Exception as e:  # noqa: BLE001
            stats.failed += 1
            stats.note(f"{slug}: {e!r}"[:200])
            continue
        if res.changed or not path.exists():
            path.write_text(res.markdown, encoding="utf-8")
        with db.tx():
            db.x("INSERT OR REPLACE INTO dossiers(slug, kind, title, updated_at, last_item_id) VALUES (?,?,?,?,?)",
                 (slug, kind, title, utcnow(), max(d.item_id for d in docs)))
        stats.ok += 1
        stats.note(f"{slug}: +{len(docs)} docs")
    return stats
