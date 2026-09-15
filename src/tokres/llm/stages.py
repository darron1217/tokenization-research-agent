"""Triage and enrich stage runners over the DB."""
from __future__ import annotations

import json
import logging

from ..db import Database, utcnow
from ..models import Item, RunStats, Status
from .analyzer import Analyzer, ItemForLLM, PriorDoc, prompt_version
from .schemas import Relevance

log = logging.getLogger(__name__)


def to_llm_item(it: Item, priors: list[PriorDoc] | None = None) -> ItemForLLM:
    return ItemForLLM(item_id=it.id, title=it.title, source=it.source, tier=it.source_tier, entity=it.entity,
                      published_at=it.published_at.date().isoformat() if it.published_at else None, url=it.url,
                      text=it.content_text or it.snippet or "", extract_kind=it.extract_kind, priors=priors or [])


def run_triage(db: Database, analyzer: Analyzer, limit: int | None = None) -> RunStats:
    stats = RunStats(stage="triage")
    run_id = db.start_run("triage")
    pv = prompt_version("triage")
    for it in db.items_by_status(Status.fetched, limit=limit):
        # Free gate: non-primary sources with zero keyword hits after full-text fetch are dropped without an LLM call
        if it.source_tier in ("T2", "T3", "T4") and it.keyword_score == 0:
            with db.tx():
                db.set_status(it.id, Status.dropped)
            continue
        try:
            res = analyzer.triage(to_llm_item(it))
            with db.tx():
                db.x("""INSERT OR REPLACE INTO triage(item_id,relevance,topics,doc_type,impact_type,primary_source,reason,model,prompt_version,created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (it.id, res.relevance.value, json.dumps(res.topics), res.doc_type, res.impact_type, int(res.primary_source),
                      res.reason, getattr(analyzer, "triage_model", "stub"), pv, utcnow()))
                if res.relevance in (Relevance.core, Relevance.relevant):
                    db.set_status(it.id, Status.triaged)
                elif res.relevance == Relevance.tangential:
                    db.set_status(it.id, Status.triaged)  # kept but enrich will skip (score stage uses relevance)
                else:
                    db.set_status(it.id, Status.dropped)
            stats.ok += 1
        except Exception as e:  # noqa: BLE001
            stats.failed += 1
            with db.tx():
                db.set_status(it.id, Status.fetched if it.attempts < 2 else Status.error, error=repr(e))
            log.warning("triage failed for %s: %r", it.id, e)
    with db.tx():
        db.finish_run(run_id, stats)
    return stats


def find_priors(db: Database, it: Item, k: int = 5) -> list[PriorDoc]:
    """Similar earlier documents for novelty judgement. Uses the vector index when available, else entity+recency."""
    try:
        from ..index.search import similar_items

        rows = similar_items(db, it, k=k, before=it.published_at)
        if rows:
            return rows
    except Exception as e:  # noqa: BLE001
        log.debug("vector priors unavailable: %r", e)
    params: list = []
    where = "a.item_id != ?"
    params.append(it.id)
    if it.entity:
        where += " AND i.entity = ?"
        params.append(it.entity)
    if it.published_at:
        where += " AND (i.published_at IS NULL OR i.published_at <= ?)"
        params.append(it.published_at.isoformat())
    rows = db.q(f"""SELECT i.id, i.title, i.published_at, a.summary_ko FROM analyses a JOIN items i ON i.id=a.item_id
                    WHERE {where} ORDER BY i.published_at DESC LIMIT ?""", (*params, k))
    return [PriorDoc(r["id"], r["title"], (r["published_at"] or "")[:10] or None, r["summary_ko"]) for r in rows]


def run_enrich(db: Database, analyzer: Analyzer, limit: int | None = None, max_items: int | None = None) -> RunStats:
    stats = RunStats(stage="enrich")
    run_id = db.start_run("enrich")
    pv = prompt_version("enrich")
    rows = db.q("""SELECT i.* FROM items i JOIN triage t ON t.item_id=i.id
                   WHERE i.status='triaged' AND t.relevance IN ('core','relevant')
                   ORDER BY CASE i.source_tier WHEN 'T0' THEN 0 WHEN 'T1' THEN 1 ELSE 2 END,
                            t.relevance='core' DESC, i.published_at ASC, i.id ASC""")
    items = [db._row_to_item(r) for r in rows]
    cap = max_items if max_items is not None else len(items)
    for it in items[: min(cap, limit or cap)]:
        try:
            res = analyzer.enrich(to_llm_item(it, find_priors(db, it)))
            with db.tx():
                db.x("""INSERT OR REPLACE INTO analyses(item_id,summary_ko,key_facts,why_it_matters,what_changed,materiality,
                        materiality_reason,novelty,updates_item_id,confidence,entities,relations,effective_date,model,prompt_version,created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (it.id, res.summary_ko, json.dumps(res.key_facts, ensure_ascii=False), res.why_it_matters, res.what_changed,
                      res.materiality, res.materiality_reason, res.novelty, res.updates_item_id, res.confidence,
                      json.dumps([e.model_dump() for e in res.entities], ensure_ascii=False),
                      json.dumps([r.model_dump() for r in res.relations], ensure_ascii=False), res.effective_date,
                      getattr(analyzer, "enrich_model", "stub"), pv, utcnow()))
                db.set_status(it.id, Status.enriched)
                from ..knowledge.entities import upsert_item_knowledge

                upsert_item_knowledge(db, it, res)
            stats.ok += 1
        except Exception as e:  # noqa: BLE001
            stats.failed += 1
            with db.tx():
                db.set_status(it.id, Status.triaged, error=repr(e))
            log.warning("enrich failed for %s: %r", it.id, e)
    # Tangential items skip enrich but still get scored/archived
    with db.tx():
        db.x("""UPDATE items SET status='enriched' WHERE status='triaged' AND id IN
                (SELECT item_id FROM triage WHERE relevance='tangential')""")
    with db.tx():
        db.finish_run(run_id, stats)
    return stats
