"""Generic HTML listing scraper driven by CSS selectors from sources.yaml.

Spec extra keys:
  item: CSS selector for each row/card
  link: selector inside item for the <a> (or "self" if item itself is the <a>)
  title: selector for title text (or "self")
  date: selector for date text (nullable)
  date_format: strptime format hint (fallback: fuzzy parsing)
  base_url: for resolving relative hrefs
  url may contain {page} / {year} / {from} / {to} placeholders (page starts at 1)
  backfill: {stop_on_date_before_from: bool, max_pages: int, by_year: bool}
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from ..models import RawItem, SourceSpec
from ..ratelimit import PoliteClient
from ._dates import parse_date


def _sel_text(node: Node, selector: str | None) -> str:
    if not selector:
        return ""
    if selector == "self":
        return node.text(separator=" ", strip=True)
    for s in [x.strip() for x in selector.split(",")]:
        n = node.css_first(s)
        if n is not None:
            return n.text(separator=" ", strip=True)
    return ""


def _sel_href(node: Node, selector: str | None) -> str | None:
    if selector == "self" or not selector:
        return node.attributes.get("href")
    for s in [x.strip() for x in selector.split(",")]:
        n = node.css_first(s)
        if n is not None and n.attributes.get("href"):
            return n.attributes.get("href")
    return None


def parse_listing(html: str | bytes, spec: SourceSpec, page_url: str) -> list[RawItem]:
    x = spec.extra
    tree = HTMLParser(html)
    base = x.get("base_url") or page_url
    out: list[RawItem] = []
    for node in tree.css(x["item"]):
        href = _sel_href(node, x.get("link"))
        title = _sel_text(node, x.get("title") or x.get("link"))
        if x.get("title_strip"):
            title = re.sub(x["title_strip"], "", title).strip(" -–|")
        if not href or not title or len(title) < 4:
            continue
        if href.startswith("javascript:") or href.startswith("#"):
            continue
        url = urljoin(base, href)
        date = None
        if x.get("date") == "self":
            m = re.search(r"\b\d{1,2} [A-Z][a-z]{2} 20\d\d\b|\b20\d\d[.\-/]\d{1,2}[.\-/]\d{1,2}\b|\b[A-Z][a-z]+ \d{1,2}, 20\d\d\b", node.text(separator=" ", strip=True) or "")
            date = parse_date(m.group(0)) if m else None
        elif x.get("date"):
            date = parse_date(_sel_text(node, x.get("date")), x.get("date_format"))
        if x.get("require_date") and date is None:
            continue
        out.append(RawItem(url=url, title=title, source=spec.name, published_at=date))
    return out


def render_url(template: str, *, page: int = 1, year: int | None = None, date_from: datetime | None = None,
               date_to: datetime | None = None, fmt: str = "%Y-%m-%d") -> str:
    return (template.replace("{page}", str(page))
            .replace("{year}", str(year or datetime.now().year))
            .replace("{from}", date_from.strftime(fmt) if date_from else "")
            .replace("{to}", date_to.strftime(fmt) if date_to else ""))


class HtmlListAdapter:
    def discover(self, spec: SourceSpec, since: datetime) -> list[RawItem]:
        assert spec.url
        url = render_url(spec.url, page=int(spec.extra.get("page_start", 1)), year=datetime.now().year)
        with PoliteClient() as c:
            r = c.get(url)
            r.raise_for_status()
            items = parse_listing(r.text, spec, url)
        return [i for i in items if not (i.published_at and i.published_at < since)]

    def backfill(self, spec: SourceSpec, date_from: datetime, date_to: datetime, cursor: str | None) -> Iterable[tuple[list[RawItem], str | None]]:
        assert spec.url
        bf = spec.extra.get("backfill") or {}
        template = bf.get("url") or spec.url
        max_pages = int(bf.get("max_pages", 50))
        stop_on_old = bool(bf.get("stop_on_date_before_from", False))
        by_year = bool(bf.get("by_year", False))
        fmt = bf.get("date_format") or spec.extra.get("date_format") or "%Y-%m-%d"
        # cursor = "year:page" or "page"
        first = int(spec.extra.get("page_start", 1))
        if by_year:
            year, page = (int(v) for v in (cursor or f"{date_to.year}:{first}").split(":"))
        else:
            year, page = date_to.year, int(cursor or first)
        # Backfill sub-spec may override selectors
        eff = spec.model_copy(deep=True)
        for k in ("item", "link", "title", "date", "date_format", "base_url"):
            if k in bf:
                eff.extra[k] = bf[k]
        with PoliteClient() as c:
            while True:
                url = render_url(template, page=page, year=year, date_from=date_from, date_to=date_to, fmt=fmt)
                r = c.get(url)
                if r.status_code == 404:
                    yield [], None
                    return
                r.raise_for_status()
                items = parse_listing(r.text, eff, url)
                dated = [i.published_at for i in items if i.published_at]
                keep = [i for i in items if not i.published_at or date_from <= i.published_at <= date_to]
                exhausted = not items
                if stop_on_old and dated and max(dated) < date_from:
                    exhausted = True
                if by_year:
                    if exhausted or page >= max_pages:
                        if year - 1 < date_from.year:
                            yield keep, None
                            return
                        yield keep, f"{year - 1}:{first}"
                        year, page = year - 1, first
                        continue
                    yield keep, f"{year}:{page + 1}"
                    page += 1
                else:
                    if exhausted or page - first + 1 >= max_pages:
                        yield keep, None
                        return
                    yield keep, str(page + 1)
                    page += 1


def _utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)
