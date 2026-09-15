"""MCP server exposing the knowledge API to chat LLMs (Claude Desktop, Claude Code, Cursor, ...).

stdio:  tokres-mcp
http:   tokres-mcp --transport streamable-http --host 0.0.0.0 --port 8765
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from mcp.server.mcpserver import MCPServer

from ..db import Database
from ..knowledge import api
from ..settings import get_settings

mcp = MCPServer(
    "tokres",
    instructions=(
        "Curated corpus of asset-tokenization policy and adoption documents from Korean and global regulators, FMIs and banks. "
        "Source tiers: T0 regulator/central bank primary, T1 market-infrastructure/institution official, T2 major press, "
        "T3 analysis/opinion, T4 unverified social. For policy/regulatory claims prefer T0/T1 and cite item_id + url + date."
    ),
)
_db: Database | None = None


def db() -> Database:
    global _db
    if _db is None:
        _db = Database(get_settings().db_path)
    return _db


def _j(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, default=str, indent=1)


@mcp.tool()
def search_documents(query: str, since: str | None = None, until: str | None = None, entities: list[str] | None = None,
                     tiers: list[str] | None = None, region: str | None = None, doc_types: list[str] | None = None,
                     limit: int = 10) -> str:
    """Hybrid (semantic + keyword) search over tokenization policy/adoption documents. Filters: since/until (ISO dates),
    entities (canonical institution names e.g. "BIS", "금융위원회"), tiers (["T0","T1"] for primary sources only),
    region (KR/US/EU/UK/HK/SG/GLOBAL), doc_types (regulation/guideline/press_release/report/speech/news/opinion).
    Returns item_id, title, entity, tier, date, url, summary, best chunk."""
    return _j(api.search_documents(db(), query, k=limit, since=since, until=until, entities=entities, tiers=tiers,
                                   region=region, doc_types=doc_types))


@mcp.tool()
def get_document(item_id: int, full_text: bool = False) -> str:
    """Get one document's structured analysis (summary_ko, key_facts, why_it_matters, what_changed, materiality, novelty,
    entities, relations, effective_date, priority) and optionally its extracted full text."""
    return _j(api.get_document(db(), item_id, full_text=full_text))


@mcp.tool()
def entity_timeline(entity: str, since: str | None = None, limit: int = 40) -> str:
    """Chronological events (documents + stated relations) for an institution, project, regulation or product."""
    return _j(api.entity_timeline(db(), entity, since, limit))


@mcp.tool()
def list_entities(type: str | None = None, query: str | None = None, limit: int = 100) -> str:
    """List known entities (org/project/regulation/product) with document counts. Use to find canonical names."""
    return _j(api.list_entities(db(), type, query, limit))


@mcp.tool()
def list_dossiers() -> str:
    """List living dossiers (current-state summaries) by slug."""
    return _j(api.list_dossiers(db()))


@mcp.tool()
def get_dossier(slug: str) -> str:
    """Read a living dossier (Markdown): current state, key positions, timeline, open issues. Cites item_ids."""
    return api.get_dossier(slug) or "(no dossier for this slug)"


@mcp.tool()
def recent_briefs(days: int = 7, min_priority: str = "fyi") -> str:
    """Items from recent daily briefs with priority (must_read > notable > fyi) and summaries."""
    return _j(api.recent_briefs(db(), days, min_priority))


@mcp.tool()
def rate_document(item_id: int, rating: str, note: str | None = None) -> str:
    """Record human feedback on an item's usefulness (rating: up|down). This is the only write operation."""
    return _j(api.rate_document(db(), item_id, rating, note))


@mcp.resource("brief://{date}")
def brief_resource(date: str) -> str:
    """Daily brief Markdown for YYYY-MM-DD."""
    p = get_settings().reports_dir / f"{date}.md"
    return p.read_text(encoding="utf-8") if p.exists() else f"(no brief for {date})"


@mcp.resource("dossier://{slug}")
def dossier_resource(slug: str) -> str:
    return api.get_dossier(slug) or "(no dossier)"


@mcp.prompt()
def weekly_review(days: int = 7) -> str:
    """Summarize the week's tokenization developments for a decision-maker."""
    return (f"Use recent_briefs(days={days}) and, for the must_read items, get_document. Produce a Korean weekly review: "
            "1) 규제·정책 변화, 2) 인프라·상용화, 3) 국내 시사점, 4) 주시할 마일스톤. Cite [item_id] for every claim.")


@mcp.prompt()
def compare_positions(entity_a: str, entity_b: str, topic: str) -> str:
    """Compare two institutions' positions on a topic over time."""
    return (f"Use entity_timeline for '{entity_a}' and '{entity_b}', then search_documents for '{topic}' filtered to each entity. "
            f"Compare their positions on {topic}: where they agree, where they differ, how each evolved, and implications for Korea. "
            "Cite [item_id] and dates; prefer T0/T1 sources.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http", "sse"])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
