"""Index stage: enriched items → chunks (summary as chunk 0) → Qdrant."""
from __future__ import annotations

import json
import logging

from ..db import Database
from ..models import RunStats, Status
from .chunk import chunk_text, make_header
from .qdrant_store import QdrantStore, epoch, get_store

log = logging.getLogger(__name__)
LLM_CONTEXT_MIN_CHARS = 15000  # only long primary-source docs get per-chunk LLM context


def index_item(db: Database, store: QdrantStore, item_id: int, analyzer=None) -> int:
    it = db.get_item(item_id)
    if not it:
        return 0
    a = db.q1("SELECT summary_ko, key_facts FROM analyses WHERE item_id=?", (item_id,))
    t = db.q1("SELECT doc_type FROM triage WHERE item_id=?", (item_id,))
    sc = db.q1("SELECT priority FROM scores WHERE item_id=?", (item_id,))
    date = it.published_at.date().isoformat() if it.published_at else None
    summary = a["summary_ko"] if a else None
    header = make_header(it.title, it.entity, t["doc_type"] if t else None, date, summary)
    payload = {"canonical_url": it.canonical_url, "title": it.title, "source": it.source, "source_tier": it.source_tier,
               "entity": it.entity, "region": it.region, "doc_type": t["doc_type"] if t else None,
               "priority": sc["priority"] if sc else None, "published_at": epoch(it.published_at), "date": date, "lang": it.lang}
    chunks: list[dict] = []
    if summary:
        facts = json.loads(a["key_facts"] or "[]")
        stext = summary + ("\n" + "\n".join(f"- {f}" for f in facts) if facts else "")
        chunks.append({"index": 0, "text": stext, "embed_text": f"{header}\n{stext}", "kind": "summary"})
    body = it.content_text or ""
    use_llm_ctx = analyzer is not None and it.source_tier in ("T0", "T1") and len(body) >= LLM_CONTEXT_MIN_CHARS
    for c in chunk_text(body, header):
        ctx = ""
        if use_llm_ctx:
            try:
                ctx = analyzer.chunk_context(body, c.text).context
            except Exception as e:  # noqa: BLE001
                log.debug("chunk context failed: %r", e)
        embed_text = f"{header}\n{ctx}\n{c.text}" if ctx else f"{header}\n{c.text}"
        chunks.append({"index": c.index, "text": c.text, "embed_text": embed_text, "kind": "chunk"})
    store.delete_item(item_id)
    return store.upsert_chunks(item_id, chunks, payload)


def index_pending(db: Database, analyzer=None, limit: int | None = None, store: QdrantStore | None = None) -> RunStats:
    stats = RunStats(stage="index")
    store = store or get_store()
    for it in db.items_by_status(Status.enriched, limit=limit):
        try:
            n = index_item(db, store, it.id, analyzer)
            with db.tx():
                db.set_status(it.id, Status.indexed)
            stats.ok += 1
            stats.note(f"#{it.id}: {n} chunks") if n > 30 else None
        except Exception as e:  # noqa: BLE001
            stats.failed += 1
            log.warning("index failed for %s: %r", it.id, e)
            stats.note(f"#{it.id}: {e!r}"[:200])
    return stats
