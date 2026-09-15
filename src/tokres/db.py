from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Item, RunStats, Status

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, detect_types=0, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.migrate()

    # ---------- migrations ----------
    def migrate(self) -> None:
        self.conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        applied = {r[0] for r in self.conn.execute("SELECT version FROM schema_version")}
        for sql_file in sorted(MIGRATIONS_DIR.glob("m*.sql")):
            m = re.match(r"m(\d+)_", sql_file.name)
            if not m:
                continue
            version = int(m.group(1))
            if version in applied:
                continue
            # executescript issues its own COMMIT, so it cannot live inside tx()
            self.conn.executescript(sql_file.read_text(encoding="utf-8"))
            self.conn.execute("INSERT INTO schema_version(version, applied_at) VALUES (?, ?)", (version, utcnow()))

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self.conn.close()

    # ---------- items ----------
    def insert_item(self, *, canonical_url: str, url: str, source: str, source_tier: str, entity: str | None,
                    region: str | None, title: str, snippet: str | None, published_at: datetime | None,
                    keyword_score: int = 0, extra: dict[str, Any] | None = None) -> int | None:
        """Insert if new. Returns new id, or None if canonical_url already exists."""
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO items(canonical_url,url,source,source_tier,entity,region,title,snippet,
               published_at,discovered_at,status,keyword_score,extra_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (canonical_url, url, source, source_tier, entity, region, title, snippet,
             published_at.isoformat() if published_at else None, utcnow(), Status.discovered.value,
             keyword_score, json.dumps(extra or {}, ensure_ascii=False)),
        )
        return cur.lastrowid if cur.rowcount else None

    def get_item(self, item_id: int) -> Item | None:
        row = self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return self._row_to_item(row) if row else None

    def get_item_by_url(self, canonical_url: str) -> Item | None:
        row = self.conn.execute("SELECT * FROM items WHERE canonical_url=?", (canonical_url,)).fetchone()
        return self._row_to_item(row) if row else None

    def items_by_status(self, status: Status, limit: int | None = None, order: str = "published_at ASC, id ASC") -> list[Item]:
        sql = f"SELECT * FROM items WHERE status=? ORDER BY {order}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self._row_to_item(r) for r in self.conn.execute(sql, (status.value,))]

    def set_status(self, item_id: int, status: Status, error: str | None = None) -> None:
        if error:
            self.conn.execute(
                "UPDATE items SET status=?, attempts=attempts+1, last_error=? WHERE id=?",
                (status.value, error[:500], item_id),
            )
        else:
            self.conn.execute("UPDATE items SET status=?, last_error=NULL WHERE id=?", (status.value, item_id))

    def update_item(self, item_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [v.isoformat() if isinstance(v, datetime) else v for v in fields.values()]
        self.conn.execute(f"UPDATE items SET {cols} WHERE id=?", (*vals, item_id))

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> Item:
        d = dict(row)
        d.pop("extra_json", None)
        for k in ("published_at", "discovered_at"):
            if d.get(k):
                d[k] = datetime.fromisoformat(d[k])
        return Item(**d)

    def item_extra(self, item_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT extra_json FROM items WHERE id=?", (item_id,)).fetchone()
        return json.loads(row[0]) if row and row[0] else {}

    # ---------- runs ----------
    def start_run(self, stage: str) -> int:
        cur = self.conn.execute("INSERT INTO runs(stage, started_at) VALUES (?, ?)", (stage, utcnow()))
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, stats: RunStats) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at=?, ok=?, failed=?, notes=? WHERE id=?",
            (utcnow(), stats.ok, stats.failed, "\n".join(stats.notes)[:4000], run_id),
        )

    def record_source_health(self, source: str, ok: bool, error: str | None = None) -> int:
        """Returns consecutive failure count after update."""
        if ok:
            self.conn.execute(
                """INSERT INTO source_health(source,last_ok_at,consecutive_failures) VALUES (?,?,0)
                   ON CONFLICT(source) DO UPDATE SET last_ok_at=excluded.last_ok_at, consecutive_failures=0, last_error=NULL""",
                (source, utcnow()),
            )
            return 0
        self.conn.execute(
            """INSERT INTO source_health(source,last_error_at,consecutive_failures,last_error) VALUES (?,?,1,?)
               ON CONFLICT(source) DO UPDATE SET last_error_at=excluded.last_error_at,
               consecutive_failures=consecutive_failures+1, last_error=excluded.last_error""",
            (source, utcnow(), (error or "")[:500]),
        )
        row = self.conn.execute("SELECT consecutive_failures FROM source_health WHERE source=?", (source,)).fetchone()
        return int(row[0]) if row else 1

    # ---------- llm usage ----------
    def record_usage(self, *, stage: str, model: str, item_id: int | None, input_tokens: int, cache_read: int,
                     cache_write: int, output_tokens: int, run_id: int | None = None) -> None:
        self.conn.execute(
            """INSERT INTO llm_usage(run_id,stage,model,item_id,input_tokens,cache_read_tokens,cache_write_tokens,
               output_tokens,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (run_id, stage, model, item_id, input_tokens, cache_read, cache_write, output_tokens, utcnow()),
        )

    # ---------- generic helpers ----------
    def q(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def q1(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def x(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)
