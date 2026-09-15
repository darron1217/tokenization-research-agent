from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from .config import load_sources
from .db import Database
from .settings import get_settings

app = typer.Typer(help="tokres: tokenization policy research agent", no_args_is_help=True)
console = Console()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("pypdf").setLevel(logging.ERROR)


def _db() -> Database:
    return Database(get_settings().db_path)


def _since(spec: str) -> datetime:
    now = datetime.now(UTC)
    if spec.endswith("d"):
        return now - timedelta(days=int(spec[:-1]))
    if spec.endswith("h"):
        return now - timedelta(hours=int(spec[:-1]))
    return datetime.fromisoformat(spec).replace(tzinfo=UTC)


def _print_stats(stats) -> None:
    rprint(f"[bold]{stats.stage}[/bold]: ok={stats.ok} failed={stats.failed}")
    for n in stats.notes:
        rprint(f"  {n}")


@app.command()
def discover(source: str | None = typer.Option(None, help="Only this source name"),
             tiers: str | None = typer.Option(None, help="Comma list, e.g. T0,T1"),
             since: str = typer.Option("3d", help="e.g. 3d, 12h, 2026-01-01")):
    """Poll sources and insert new items."""
    from .sources.base import discover_all

    db = _db()
    stats = discover_all(db, load_sources(), _since(since), tiers=set(tiers.split(",")) if tiers else None,
                         only={source} if source else None)
    _print_stats(stats)


@app.command()
def backfill(source: str | None = typer.Option(None), tiers: str | None = typer.Option("T0,T1"),
             from_: str = typer.Option(..., "--from"), to: str | None = typer.Option(None, "--to"),
             dry_run: bool = typer.Option(False, "--dry-run"), no_resume: bool = typer.Option(False, "--no-resume"),
             max_batches: int | None = typer.Option(None, help="Stop after N pages per source (for testing)")):
    """Walk paginated archives over a date range. Resumable. Items then flow through fetch/triage/enrich as usual."""
    from .backfill import backfill_source

    db = _db()
    d_from = datetime.fromisoformat(from_).replace(tzinfo=UTC)
    d_to = datetime.fromisoformat(to).replace(tzinfo=UTC) if to else datetime.now(UTC)
    tier_set = set(tiers.split(",")) if tiers else None
    for spec in load_sources():
        if source and spec.name != source:
            continue
        if not source and (tier_set and spec.tier not in tier_set):
            continue
        if not spec.enabled or spec.type == "bird_x":
            continue
        stats = backfill_source(db, spec, d_from, d_to, resume=not no_resume, dry_run=dry_run, max_batches=max_batches)
        _print_stats(stats)
    if not dry_run:
        with db.tx():
            db.x("UPDATE items SET brief_date='backfill' WHERE brief_date IS NULL AND published_at < ?",
                 ((datetime.now(UTC) - timedelta(days=7)).isoformat(),))


@app.command()
def fetch(limit: int | None = typer.Option(None)):
    """Fetch and extract text for discovered items."""
    from .extract import fetch_pending

    _print_stats(fetch_pending(_db(), limit))


@app.command()
def triage(limit: int | None = None, no_llm: bool = typer.Option(False, "--no-llm")):
    from .llm.anthropic_analyzer import make_analyzer
    from .llm.stages import run_triage

    db = _db()
    _print_stats(run_triage(db, make_analyzer(db, not no_llm), limit))


@app.command()
def enrich(limit: int | None = None, max_items: int | None = typer.Option(None, "--max"),
           no_llm: bool = typer.Option(False, "--no-llm"), model: str | None = typer.Option(None, help="Override enrich model")):
    from .llm.anthropic_analyzer import make_analyzer
    from .llm.stages import run_enrich

    db = _db()
    kw = {"enrich_model": model} if model else {}
    _print_stats(run_enrich(db, make_analyzer(db, not no_llm, **kw), limit, max_items=max_items))


@app.command()
def score():
    """(Re)compute priority scores for all enriched items with current rules."""
    from .scoring.rules import compute_scores

    _print_stats(compute_scores(_db()))


@app.command()
def brief(date: str | None = None, no_slack: bool = typer.Option(False, "--no-slack"), no_llm: bool = typer.Option(False, "--no-llm")):
    """Render the daily brief from un-briefed items."""
    from .brief.render import build_brief
    from .llm.anthropic_analyzer import make_analyzer
    from .pipeline import source_warnings

    db = _db()
    out = build_brief(db, make_analyzer(db, not no_llm), date, send_slack=not no_slack, warnings=source_warnings(db))
    rprint(f"brief: {out or 'no new items'}")


@app.command("run-daily")
def run_daily_cmd(limit: int | None = None, no_llm: bool = typer.Option(False, "--no-llm"),
                  no_slack: bool = typer.Option(False, "--no-slack"), no_index: bool = typer.Option(False, "--no-index"),
                  since_days: int = 3, source: str | None = None):
    """Full daily pipeline."""
    from .pipeline import run_daily

    res = run_daily(_db(), limit=limit, use_llm=not no_llm, send_slack=not no_slack, do_index=not no_index,
                    since_days=since_days, only_sources={source} if source else None)
    for st in res.values():
        _print_stats(st)


@app.command()
def index(limit: int | None = None, rebuild: bool = typer.Option(False, help="Re-index all enriched/indexed items"),
          no_llm: bool = typer.Option(True, "--llm/--no-llm", help="Use LLM for chunk context on long T0/T1 PDFs")):
    from .index.pipeline import index_pending
    from .llm.anthropic_analyzer import make_analyzer

    db = _db()
    if rebuild:
        with db.tx():
            db.x("UPDATE items SET status='enriched' WHERE status='indexed'")
    _print_stats(index_pending(db, make_analyzer(db, True) if no_llm else None, limit))


@app.command()
def search(query: str, k: int = 10, since: str | None = None, entity: str | None = None,
           tier: str | None = typer.Option(None, help="Comma list, e.g. T0,T1"), region: str | None = None,
           answer: bool = typer.Option(False, help="Generate an answer with citations")):
    from .knowledge.api import search_documents

    hits = search_documents(_db(), query, k=k, since=since, entities=[entity] if entity else None,
                            tiers=tier.split(",") if tier else None, region=region)
    t = Table(title=f"search: {query}")
    for col in ("id", "date", "tier", "entity", "title", "score"):
        t.add_column(col)
    for h in hits:
        t.add_row(str(h["item_id"]), h.get("date") or "-", h["tier"], h.get("entity") or "-", h["title"][:70], f"{h['score']:.3f}")
    console.print(t)
    if answer:
        from .knowledge.ask import ask as _ask

        rprint(_ask(_db(), query))


@app.command()
def ask(question: str):
    """Agentic Q&A over the corpus (Claude + search tools)."""
    from .knowledge.ask import ask as _ask

    rprint(_ask(_db(), question))


@app.command()
def rate(item_id: int, rating: str = typer.Argument(..., help="up|down"), note: str | None = None):
    from .db import utcnow

    db = _db()
    with db.tx():
        db.x("INSERT INTO feedback(item_id, rating, note, created_at) VALUES (?,?,?,?)", (item_id, rating, note, utcnow()))
    rprint(f"recorded {rating} for #{item_id}")


@app.command()
def dossiers(rebuild: bool = False, no_llm: bool = typer.Option(False, "--no-llm")):
    from .knowledge.dossiers import update_dossiers
    from .llm.anthropic_analyzer import make_analyzer

    db = _db()
    _print_stats(update_dossiers(db, make_analyzer(db, not no_llm), rebuild=rebuild))


@app.command("discover-sources")
def discover_sources_cmd(dry_run: bool = typer.Option(False, "--dry-run"), no_llm: bool = typer.Option(False, "--no-llm")):
    from .discovery.run import discover_sources
    from .llm.anthropic_analyzer import make_analyzer

    db = _db()
    out = discover_sources(db, make_analyzer(db, not no_llm), dry_run=dry_run)
    rprint(f"candidates report: {out}")


@app.command("apply-candidates")
def apply_candidates_cmd(report: str):
    from .discovery.run import apply_candidates

    rprint(apply_candidates(_db(), report))


@app.command()
def eval(questions: str = "evals/questions.yaml"):
    from .knowledge.evals import run_eval

    rprint(run_eval(_db(), questions))


@app.command()
def doctor():
    """Check environment, keys, DB, Qdrant, bird."""
    from .doctor import run_doctor

    run_doctor(_db())


@app.command()
def export(path: str = "data/export.jsonl"):
    from .transfer import export_db

    rprint(export_db(_db(), path))


@app.command("import")
def import_cmd(path: str):
    from .transfer import import_db

    rprint(import_db(_db(), path))


@app.command()
def stats():
    db = _db()
    t = Table(title="items by status")
    t.add_column("status")
    t.add_column("count")
    for r in db.q("SELECT status, COUNT(*) c FROM items GROUP BY status ORDER BY c DESC"):
        t.add_row(r["status"], str(r["c"]))
    console.print(t)
    t2 = Table(title="priority")
    t2.add_column("priority")
    t2.add_column("count")
    for r in db.q("SELECT priority, COUNT(*) c FROM scores GROUP BY priority"):
        t2.add_row(r["priority"], str(r["c"]))
    console.print(t2)
    u = db.q1("SELECT SUM(input_tokens) i, SUM(cache_read_tokens) cr, SUM(output_tokens) o FROM llm_usage")
    rprint(f"llm tokens: input={u['i'] or 0} cache_read={u['cr'] or 0} output={u['o'] or 0}")


if __name__ == "__main__":
    app()
