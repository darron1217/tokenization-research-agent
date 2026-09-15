from __future__ import annotations

import shutil
import sqlite3

from rich import print as rprint

from .db import Database
from .settings import get_settings


def run_doctor(db: Database) -> None:
    s = get_settings()
    def ok(b):
        return "[green]OK[/green]" if b else "[red]MISSING[/red]"
    rprint(f"data dir: {s.data_dir}  sqlite {sqlite3.sqlite_version}")
    rprint(f"ANTHROPIC_API_KEY: {ok(s.has_llm)}")
    rprint(f"EMBED_MODEL={s.embed_model}  VOYAGE_API_KEY: {ok(bool(s.voyage_api_key)) if s.embed_model.startswith('voyage') else 'n/a (local e5-large)'}")
    rprint(f"NAVER keys: {ok(s.has_naver)}   SLACK webhook: {ok(s.has_slack)}   X cookies: {ok(s.has_x)}   bird binary: {ok(shutil.which('bird'))}")
    try:
        from qdrant_client import QdrantClient

        c = QdrantClient(url=s.qdrant_url, timeout=5)
        cols = [x.name for x in c.get_collections().collections]
        rprint(f"Qdrant {s.qdrant_url}: [green]OK[/green] collections={cols}")
    except Exception as e:  # noqa: BLE001
        rprint(f"Qdrant {s.qdrant_url}: [red]unreachable[/red] ({e.__class__.__name__})")
    n = db.q1("SELECT COUNT(*) c FROM items")["c"]
    v = db.q1("SELECT MAX(version) v FROM schema_version")["v"]
    rprint(f"DB: schema v{v}, {n} items")
    for r in db.q("SELECT source, consecutive_failures, last_error FROM source_health WHERE consecutive_failures>0"):
        rprint(f"  [yellow]source {r['source']}[/yellow] failing x{r['consecutive_failures']}: {(r['last_error'] or '')[:100]}")
