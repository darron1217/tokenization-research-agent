import pytest

from tokres.urls import canonicalize


@pytest.mark.parametrize("raw,expected", [
    ("http://www.fsc.go.kr/no010101/87716?utm_source=rss&srchCtgry=", "https://fsc.go.kr/no010101/87716"),
    ("https://www.bis.org/press/p260527.htm#top", "https://bis.org/press/p260527.htm"),
    ("https://example.com/a/b/", "https://example.com/a/b"),
    ("https://Example.com:443/x?b=2&a=1", "https://example.com/x?a=1&b=2"),
    ("https://www.fsc.go.kr/no010101/87201?srchCtgry=&curPage=2&srchKey=&srchText=&srchBeginDt=2026-06-01&srchEndDt=2026-06-30",
     "https://fsc.go.kr/no010101/87201"),
])
def test_canonicalize(raw, expected):
    assert canonicalize(raw) == expected


def test_canonicalize_idempotent():
    u = "https://www.sec.gov/news/press-release/2026-100?utm_campaign=x"
    assert canonicalize(canonicalize(u)) == canonicalize(u)
