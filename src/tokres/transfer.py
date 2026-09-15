"""Export/import of the SQLite state as JSONL (for migration between hosts; Qdrant is rebuilt from this)."""
from __future__ import annotations

import json
from pathlib import Path

from .db import Database

TABLES = ["items", "triage", "analyses", "scores", "clusters", "entities", "item_entities", "relations", "dossiers",
          "feedback", "source_candidates", "backfill_progress", "deliveries", "source_health"]


def export_db(db: Database, path: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with p.open("w", encoding="utf-8") as f:
        for t in TABLES:
            for row in db.q(f"SELECT * FROM {t}"):
                f.write(json.dumps({"_t": t, **dict(row)}, ensure_ascii=False) + "\n")
                n += 1
    return f"exported {n} rows to {p}"


def import_db(db: Database, path: str) -> str:
    n = 0
    with db.tx(), Path(path).open(encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            t = d.pop("_t")
            cols = ", ".join(d.keys())
            qs = ", ".join("?" * len(d))
            db.x(f"INSERT OR IGNORE INTO {t}({cols}) VALUES ({qs})", list(d.values()))
            n += 1
    return f"imported {n} rows"
