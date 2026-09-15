from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..config import load_scoring
from ..db import Database, utcnow
from ..llm.analyzer import Analyzer, BriefItemForLLM
from ..notify.slack import brief_blocks, post_slack
from ..scoring.rules import PRIORITY_RANK
from ..settings import get_settings
from .cluster import cluster_items, persist_clusters

log = logging.getLogger(__name__)
SECTIONS = [("must_read", "🔴 Must read"), ("notable", "🟠 Notable"), ("fyi", "🟢 FYI")]


def _select_rows(db: Database) -> list[dict]:
    rows = db.q("""SELECT i.id, i.title, i.url, i.source, i.source_tier, i.entity, i.published_at, i.extra_json,
                          t.doc_type, t.primary_source, a.summary_ko, a.key_facts, a.why_it_matters, a.what_changed,
                          a.materiality, a.materiality_reason, a.novelty, a.updates_item_id, a.confidence,
                          s.priority, s.final_score
                   FROM items i LEFT JOIN triage t ON t.item_id=i.id LEFT JOIN analyses a ON a.item_id=i.id
                   LEFT JOIN scores s ON s.item_id=i.id
                   WHERE i.status IN ('enriched','indexed') AND i.brief_date IS NULL""")
    out = []
    for r in rows:
        d = dict(r)
        extra = json.loads(d.pop("extra_json") or "{}")
        d["promoted_from"] = extra.get("promoted_from")
        d["published_at"] = datetime.fromisoformat(d["published_at"]) if d["published_at"] else None
        d["date"] = d["published_at"].date().isoformat() if d["published_at"] else None
        d["key_facts"] = json.loads(d["key_facts"]) if d.get("key_facts") else []
        d["summary"] = d.pop("summary_ko") or ""
        d["tier"] = d["source_tier"]
        d["priority"] = d["priority"] or "archive"
        out.append(d)
    return out


def build_brief(db: Database, analyzer: Analyzer, date: str | None = None, *, send_slack: bool = True,
                warnings: list[str] | None = None) -> Path | None:
    s = get_settings()
    date = date or datetime.now(UTC).astimezone().date().isoformat()
    rows = _select_rows(db)
    if not rows:
        log.info("brief: no new items")
        return None
    groups = cluster_items(rows)
    with db.tx():
        persist_clusters(db, groups, date)
    # corroboration changes scores → recompute for these items
    from ..scoring.rules import compute_scores

    compute_scores(db, [r["id"] for r in rows])
    fresh = {r["id"]: r["priority"] for r in db.q(
        f"SELECT item_id AS id, priority FROM scores WHERE item_id IN ({','.join('?' * len(rows))})", [r["id"] for r in rows])}
    for r in rows:
        r["priority"] = fresh.get(r["id"], r["priority"])
    clusters = []
    for g in groups:
        # g[0] is the best-tier / primary / earliest item; the cluster inherits the max priority of its members
        best_priority = max((r["priority"] for r in g), key=lambda p: PRIORITY_RANK[p])
        head = dict(g[0])
        head["priority"] = best_priority
        clusters.append({"head": head, "others": [r for r in g if r["id"] != head["id"]]})
    clusters.sort(key=lambda c: (-PRIORITY_RANK[c["head"]["priority"]], -(c["head"].get("final_score") or 0)))
    sections = [{"key": k, "label": lbl, "clusters": [c for c in clusters if c["head"]["priority"] == k][:15]} for k, lbl in SECTIONS]
    archived = [c["head"] for c in clusters if c["head"]["priority"] == "archive"][:40]
    top = [c["head"] for sec in sections for c in sec["clusters"]]
    try:
        intro = analyzer.brief_intro([BriefItemForLLM(t["id"], t["title"], t["entity"], t["tier"], t["priority"], t["summary"]) for t in top[:10]])
        headline = intro.headline_points
    except Exception as e:  # noqa: BLE001
        log.warning("brief intro failed: %r", e)
        headline = []
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=False, trim_blocks=True, lstrip_blocks=True)
    md = env.get_template("brief.md.j2").render(date=date, headline=headline, sections=sections, archived=archived,
                                                warnings=warnings or [], generated_at=utcnow(),
                                                rule_version=load_scoring()["rule_version"])
    out = s.reports_dir / f"{date}.md"
    if out.exists():  # multiple runs per day → append a run marker
        md = out.read_text(encoding="utf-8") + "\n\n---\n\n" + md
    out.write_text(md, encoding="utf-8")
    counts = {k: len(sec["clusters"]) for k, sec in zip([k for k, _ in SECTIONS], sections, strict=False)}
    counts["archive"] = len(archived)
    with db.tx():
        db.x(f"UPDATE items SET brief_date=? WHERE id IN ({','.join('?' * len(rows))})", (date, *[r["id"] for r in rows]))
    if send_slack and s.has_slack:
        blocks, text = brief_blocks(date, headline, top, counts, warnings or [])
        try:
            if post_slack(blocks, text):
                with db.tx():
                    db.x("INSERT OR REPLACE INTO deliveries(brief_date, channel, sent_at) VALUES (?,?,?)", (date, "slack", utcnow()))
        except Exception as e:  # noqa: BLE001
            log.warning("slack delivery failed: %r", e)
    return out
