from __future__ import annotations

import re
from datetime import UTC, datetime
from time import mktime

from dateutil import parser as dtparser


def parse_date(value, fmt: str | None = None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if hasattr(value, "tm_year"):  # time.struct_time from feedparser
        return datetime.fromtimestamp(mktime(value), tz=UTC)
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"\s+", " ", s)
    if fmt:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    # Korean forms: 2026.09.15 / 2026-09-15 / 2026년 9월 15일
    m = re.search(r"(\d{4})[.\-/년]\s?(\d{1,2})[.\-/월]\s?(\d{1,2})", s)
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=UTC)
        except ValueError:
            return None
    try:
        d = dtparser.parse(s, fuzzy=True)
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except (ValueError, OverflowError):
        return None
