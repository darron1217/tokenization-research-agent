from datetime import UTC, datetime

from tests.conftest import FIX, spec
from tokres.sources.html_list import parse_listing
from tokres.sources.json_api import parse_records
from tokres.sources.naver import parse_response
from tokres.sources.rss import parse_feed


def test_rss_parse_filters_old(since):
    items = parse_feed((FIX / "fsc.rss").read_bytes(), "fsc_press", since)
    assert [i.title[:5] for i in items] == ["토큰증권 ", "2026년"]
    assert items[0].published_at.tzinfo is not None
    assert "utm_source" in items[0].url  # raw url kept; canonicalization happens at persist time


def test_html_list_parse():
    items = parse_listing((FIX / "fss_list.html").read_text(), spec("fss_press"), "https://www.fss.or.kr/fss/bbs/B0000188/list.do")
    assert len(items) == 2
    assert items[0].url.startswith("https://www.fss.or.kr/fss/bbs/B0000188/view.do?nttId=100")
    assert items[0].published_at == datetime(2026, 9, 12, tzinfo=UTC)


def test_naver_parse():
    import json

    items = parse_response(json.loads((FIX / "naver.json").read_text()), "naver_news", "토큰증권")
    assert items[0].title == "예탁결제원, 토큰증권 플랫폼 가동"
    assert items[0].url.startswith("https://www.hankyung.com/")
    assert "<b>" not in items[0].snippet


def test_json_api_parse():
    import json

    items = parse_records(json.loads((FIX / "hkma.json").read_text()), spec("hkma_press"))
    assert len(items) == 2 and items[0].published_at.year == 2026


def test_bird_tier_inheritance():
    from tokres.sources.bird_x import tweets_to_items

    tweets = [
        {"id": "1", "text": "New BIS paper on tokenised deposits https://www.bis.org/publ/work999.htm", "author": {"username": "BIS_org"}, "createdAt": "2026-09-15T08:00:00Z"},
        {"id": "2", "text": "gm", "author": {"username": "randomguy"}, "createdAt": "2026-09-15T08:00:00Z"},
        {"id": "3", "text": "tokenized deposits are the future", "author": {"username": "randomguy"}, "createdAt": "2026-09-15T08:00:00Z"},
    ]
    items = tweets_to_items(tweets, "x_following", None)
    urls = [i.url for i in items]
    assert "https://x.com/bis_org/status/1" in urls
    post = next(i for i in items if i.url.endswith("/status/1"))
    assert post.tier == "T0" and post.entity == "BIS"
    promoted = next(i for i in items if "bis.org/publ" in i.url)
    assert promoted.tier == "T0"
    assert not any(u.endswith("/status/2") for u in urls)  # no keywords, unregistered → dropped
    t4 = next(i for i in items if i.url.endswith("/status/3"))
    assert t4.tier == "T4"
