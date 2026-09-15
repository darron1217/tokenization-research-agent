"""Fetch item URLs and extract main text (HTML via trafilatura, PDF via pypdf). Also 1-hop link promotion."""
from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import trafilatura
from pypdf import PdfReader
from selectolax.parser import HTMLParser

from .config import domain_info, keyword_score
from .db import Database
from .models import Item, RunStats, Status
from .ratelimit import PoliteClient
from .urls import canonicalize, host_of

log = logging.getLogger(__name__)

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 40
MIN_HTML_TEXT = 500
MAX_ATTEMPTS = 3
_HANGUL = re.compile(r"[가-힣]")


@dataclass
class Extracted:
    text: str
    kind: str  # html | pdf | snippet | unsupported
    lang: str
    links: list[str]


def detect_lang(text: str) -> str:
    if not text:
        return "und"
    sample = text[:2000]
    return "ko" if len(_HANGUL.findall(sample)) > len(sample) * 0.05 else "en"


def extract_html(html: str, url: str) -> tuple[str, list[str]]:
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True,
                               favor_precision=True, deduplicate=True) or ""
    tree = HTMLParser(html)
    links = []
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if href.startswith("http"):
            links.append(href)
        elif href.startswith("/"):
            links.append(urljoin(url, href))
    return text.strip(), links


def find_attachment(html: str, url: str) -> str | None:
    tree = HTMLParser(html)
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        label = (a.text() or "").lower() + " " + href.lower()
        if href and (".pdf" in href.lower() or "filety=attach" in href.lower() or "getfile" in href.lower()) and ".hwp" not in label:
            return urljoin(url, href)
    return None


def extract_pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for i, page in enumerate(reader.pages):
        if i >= MAX_PDF_PAGES:
            break
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            continue
    text = "\n".join(parts)
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def fetch_and_extract(c: PoliteClient, item: Item) -> Extracted:
    if item.url.startswith("https://x.com/"):
        return Extracted(item.snippet or item.title, "snippet", detect_lang(item.snippet or ""), [])
    r = c.get(item.url)
    r.raise_for_status()
    ctype = (r.headers.get("content-type") or "").lower()
    if "pdf" in ctype or item.url.lower().endswith(".pdf"):
        if len(r.content) > MAX_PDF_BYTES:
            raise ValueError("pdf too large")
        text = extract_pdf(r.content)
        return Extracted(text, "pdf", detect_lang(text), [])
    html = r.text
    text, links = extract_html(html, item.url)
    if len(text) < MIN_HTML_TEXT:
        att = find_attachment(html, item.url)
        if att:
            try:
                ra = c.get(att)
                if ra.status_code == 200 and ("pdf" in (ra.headers.get("content-type") or "").lower() or ra.content[:4] == b"%PDF"):
                    if len(ra.content) <= MAX_PDF_BYTES:
                        pdf_text = extract_pdf(ra.content)
                        if len(pdf_text) > len(text):
                            return Extracted((text + "\n\n" + pdf_text).strip(), "pdf", detect_lang(pdf_text), links)
            except Exception as e:  # noqa: BLE001
                log.debug("attachment fetch failed %s: %r", att, e)
    if not text:
        text = item.snippet or item.title
        return Extracted(text, "snippet", detect_lang(text), links)
    return Extracted(text, "html", detect_lang(text), links)


def promote_links(db: Database, item: Item, links: list[str]) -> int:
    """1-hop: if a T2-T4 item links to a T0/T1 domain page, create that item so the primary source gets processed."""
    if item.source_tier in ("T0", "T1"):
        return 0
    n = 0
    for link in links[:50]:
        info = domain_info(host_of(link))
        if not info or info["tier"] not in ("T0", "T1"):
            continue
        if host_of(link) == host_of(item.url):
            continue
        canon = canonicalize(link)
        if len(canon) < 25 or canon.count("/") < 3:  # skip homepages
            continue
        new_id = db.insert_item(canonical_url=canon, url=link, source=f"promoted:{item.source}", source_tier=info["tier"],
                                entity=info.get("entity"), region=info.get("region"), title=item.title, snippet=None,
                                published_at=item.published_at, keyword_score=item.keyword_score,
                                extra={"promoted_from": item.id})
        if new_id:
            n += 1
    return n


def fetch_pending(db: Database, limit: int | None = None) -> RunStats:
    stats = RunStats(stage="fetch")
    run_id = db.start_run("fetch")
    items = db.items_by_status(Status.discovered, limit=limit)
    with PoliteClient() as c:
        for it in items:
            try:
                ex = fetch_and_extract(c, it)
                kscore, _ = keyword_score(ex.text[:20000])
                with db.tx():
                    db.update_item(it.id, content_text=ex.text, extract_kind=ex.kind, lang=ex.lang,
                                   content_hash=hashlib.sha1(ex.text.encode("utf-8")).hexdigest(),
                                   keyword_score=max(it.keyword_score, kscore))
                    db.set_status(it.id, Status.fetched)
                    promoted = promote_links(db, it, ex.links)
                stats.ok += 1
                if promoted:
                    stats.note(f"item {it.id}: promoted {promoted} primary link(s)")
            except Exception as e:  # noqa: BLE001
                stats.failed += 1
                with db.tx():
                    if it.attempts + 1 >= MAX_ATTEMPTS:
                        # Give up on fetching but still let triage see title+snippet
                        db.update_item(it.id, content_text=it.snippet or it.title, extract_kind="unsupported",
                                       lang=detect_lang(it.snippet or it.title))
                        db.set_status(it.id, Status.fetched, error=f"gave up: {e!r}")
                    else:
                        db.set_status(it.id, Status.discovered, error=repr(e))
                log.warning("fetch failed item %s (%s): %r", it.id, it.url, e)
    with db.tx():
        db.finish_run(run_id, stats)
    return stats
