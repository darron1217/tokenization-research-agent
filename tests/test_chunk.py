from datetime import UTC

from tokres.index.chunk import chunk_text, make_header


def test_chunk_mixed_korean_english():
    text = ("한국은행은 예금토큰 실증을 진행했다. " * 40 + "\n\n" + "The BIS published a report on tokenised deposits. " * 40)
    chunks = chunk_text(text, make_header("t", "한국은행", "report", "2026-09-15"))
    assert len(chunks) >= 3
    assert all(len(c.text) <= 1400 for c in chunks)
    assert chunks[0].index == 1  # 0 reserved for summary
    # overlap: consecutive chunks share some text
    assert any(chunks[i].text[-40:] in chunks[i + 1].text or chunks[i].text.split(". ")[-2] in chunks[i + 1].text
               for i in range(len(chunks) - 1))


def test_backfill_cursor_html(monkeypatch):
    """Paginated backfill stops when listing dates fall before date_from."""
    from datetime import datetime

    from tests.conftest import FIX, spec
    from tokres.sources import html_list

    pages = {1: (FIX / "fss_list.html").read_text(), 2: (FIX / "fss_list.html").read_text().replace("2026-09", "2024-01")}

    class R:
        def __init__(self, t): self.text, self.status_code = t, 200
        def raise_for_status(self): pass

    class C:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url): return R(pages[int(url.split("pageIndex=")[1])])

    monkeypatch.setattr(html_list, "PoliteClient", C)
    batches = list(html_list.HtmlListAdapter().backfill(spec("fss_press"), datetime(2026, 1, 1, tzinfo=UTC),
                                                         datetime(2026, 12, 31, tzinfo=UTC), None))
    assert len(batches) == 2 and batches[-1][1] is None
    assert len(batches[0][0]) == 2 and len(batches[1][0]) == 0
