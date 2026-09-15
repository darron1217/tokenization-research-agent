"""Retrieval eval: questions with expected item URLs → recall@k."""
from __future__ import annotations

from pathlib import Path

import yaml

from ..db import Database
from ..urls import canonicalize
from .api import search_documents


def run_eval(db: Database, path: str, k: int = 10) -> str:
    p = Path(path)
    if not p.exists():
        return f"no eval file at {p}"
    qs = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    if not qs:
        return "eval file empty"
    hit5 = hit10 = 0
    lines = []
    for q in qs:
        expected = {canonicalize(u) for u in q.get("expected_urls", [])}
        hits = search_documents(db, q["question"], k=k)
        got = [canonicalize(h["url"]) for h in hits if h.get("url")]
        r5 = any(u in expected for u in got[:5])
        r10 = any(u in expected for u in got[:10])
        hit5 += r5
        hit10 += r10
        lines.append(f"{'✓' if r10 else '✗'} {q['question'][:60]}  (r@5={int(r5)} r@10={int(r10)})")
    n = len(qs)
    return "\n".join(lines) + f"\n\nrecall@5={hit5}/{n} ({hit5 / n:.0%})  recall@10={hit10}/{n} ({hit10 / n:.0%})"
