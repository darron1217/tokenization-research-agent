"""Tool definitions shared by `ask` (Claude tool runner) — thin wrappers over knowledge.api."""
from __future__ import annotations

import json
from typing import Any

from anthropic import beta_tool

from ..db import Database
from ..knowledge import api

_db: Database | None = None


def bind(db: Database) -> None:
    global _db
    _db = db


def _j(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)


@beta_tool
def search_documents(query: str, since: str | None = None, until: str | None = None, entities: list[str] | None = None,
                     tiers: list[str] | None = None, region: str | None = None, limit: int = 10) -> str:
    """Hybrid semantic+keyword search over the tokenization corpus. Returns items with item_id, title, entity, tier
    (T0 regulator primary … T4 unverified social), date, url, summary and the best-matching chunk.
    Args:
        query: natural-language query (Korean or English)
        since: ISO date lower bound (e.g. 2025-01-01)
        until: ISO date upper bound
        entities: canonical institution names to restrict to (e.g. ["BIS", "한국은행"])
        tiers: e.g. ["T0","T1"] to restrict to primary sources
        region: KR | US | EU | UK | HK | SG | GLOBAL
        limit: max items
    """
    assert _db is not None
    return _j(api.search_documents(_db, query, k=limit, since=since, until=until, entities=entities, tiers=tiers, region=region))


@beta_tool
def get_document(item_id: int, full_text: bool = False) -> str:
    """Fetch one document's analysis (summary, key facts, why it matters, entities, relations) and optionally its full text.
    Args:
        item_id: the item id from search results
        full_text: include extracted full text (up to 20k chars)
    """
    assert _db is not None
    return _j(api.get_document(_db, item_id, full_text=full_text))


@beta_tool
def entity_timeline(entity: str, since: str | None = None, limit: int = 40) -> str:
    """Chronological list of documents and stated relations involving an institution/project (e.g. "BIS", "Project Agorá", "금융위원회").
    Args:
        entity: canonical or alias name
        since: ISO date lower bound
        limit: max events
    """
    assert _db is not None
    return _j(api.entity_timeline(_db, entity, since, limit))


@beta_tool
def get_dossier(slug: str) -> str:
    """Read the living dossier (current-state summary + timeline) for an institution/project/topic slug. Use list_dossiers to find slugs.
    Args:
        slug: dossier slug
    """
    return api.get_dossier(slug) or "(no dossier)"


@beta_tool
def list_dossiers() -> str:
    """List available dossiers (slug, kind, title, updated_at)."""
    assert _db is not None
    return _j(api.list_dossiers(_db))


@beta_tool
def recent_briefs(days: int = 7) -> str:
    """Items from the daily briefs of the last N days with priority (must_read/notable/fyi) and summaries.
    Args:
        days: lookback window in days
    """
    assert _db is not None
    return _j(api.recent_briefs(_db, days))


ALL_TOOLS = [search_documents, get_document, entity_timeline, get_dossier, list_dossiers, recent_briefs]
