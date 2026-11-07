import asyncio

import httpx
import pytest

from app.services import research as R

ARTICLE_HTML = (
    "<html><head><title>x</title></head><body><article>"
    + "".join(
        f"<p>Artemis crew update paragraph {i}: NASA said the launch window opens in March and "
        f"engineers confirmed {i + 10} checks were completed successfully this week.</p>"
        for i in range(12)
    )
    + "</article></body></html>"
)

GNEWS_RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>NASA confirms Artemis launch window - Space.com</title>
<link>https://news.google.com/rss/articles/CBMiFAKE1?oc=5</link>
<pubDate>Fri, 06 Nov 2026 10:00:00 GMT</pubDate>
<description>&lt;a href="x"&gt;NASA confirms Artemis launch window&lt;/a&gt;</description>
<source url="https://www.space.com">Space.com</source></item>
</channel></rss>"""

BING_RSS = """<?xml version="1.0"?><rss version="2.0" xmlns:News="https://www.bing.com/news/search"><channel>
<item><title>NASA confirms Artemis launch window</title>
<link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;aid=&amp;url=https%3a%2f%2fwww.example.com%2fartemis-window&amp;c=1</link>
<pubDate>Fri, 06 Nov 2026 11:00:00 GMT</pubDate>
<description>NASA says the Artemis launch window opens in March.</description>
<News:Source>Example News</News:Source></item>
<item><title>Artemis crew begins training for lunar flyby</title>
<link>http://www.bing.com/news/apiclick.aspx?url=https%3a%2f%2fother.org%2ftraining</link>
<pubDate>Fri, 06 Nov 2026 09:00:00 GMT</pubDate><description>Crew training starts.</description></item>
</channel></rss>"""


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    R._cache.clear()
    R._gdelt_last = 0.0
    monkeypatch.setattr(R.settings, "openrouter_api_key", "")


def _install(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    def fake_client(user_agent: str = R.BROWSER_UA):
        return httpx.AsyncClient(
            transport=transport, headers={"User-Agent": user_agent}, follow_redirects=True
        )

    monkeypatch.setattr(R, "_client", fake_client)

    async def no_resolve(c, sem):
        return None

    monkeypatch.setattr(R, "_resolve_google", no_resolve)


def _handler(article_status=200, article_html=ARTICLE_HTML):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "news.google.com/rss/search" in url:
            return httpx.Response(200, text=GNEWS_RSS)
        if "bing.com/news/search" in url:
            return httpx.Response(200, text=BING_RSS)
        if "gdeltproject" in url:
            return httpx.Response(429, text="slow down")
        if "wikipedia.org" in url:
            return httpx.Response(200, json=["q", [], [], []])
        body = article_html.replace("paragraph", f"paragraph-{request.url.host}")
        return httpx.Response(
            article_status, text=body, headers={"content-type": "text/html; charset=utf-8"}
        )

    return handler


def test_shorten_query_strips_publisher_and_keeps_proper_nouns():
    q = R.shorten_query(
        "Why the Fighting Irish and Marcus Freeman are facing a major test against Miami this Saturday night - Yahoo Sports"
    )
    assert "Yahoo" not in q
    assert "Marcus" in q and "Miami" in q
    assert len(q.split()) <= 8
    assert R.shorten_query("Artemis moon mission") == "Artemis moon mission"


def test_unwrap_bing_url():
    link = "http://www.bing.com/news/apiclick.aspx?ref=FexRss&url=https%3a%2f%2fwww.mirror.co.uk%2fnews%2fa-1&c=7"
    assert R.unwrap_bing_url(link) == "https://www.mirror.co.uk/news/a-1"
    assert R.unwrap_bing_url("https://example.com/x") == "https://example.com/x"


def test_google_redirect_detected_and_feed_parsed():
    items = R._parse_feed(GNEWS_RSS, google=True)
    assert len(items) == 1
    assert items[0].is_google
    assert items[0].title == "NASA confirms Artemis launch window"
    assert items[0].publisher == "Space.com"
    bing = R._parse_feed(BING_RSS, google=False)
    assert bing[0].url == "https://www.example.com/artemis-window"
    assert bing[0].publisher == "Example News"


def test_merge_dedupes_and_prefers_real_url_over_google():
    g = R._parse_feed(GNEWS_RSS, google=True)
    b = R._parse_feed(BING_RSS, google=False)
    merged = R.merge_candidates(g + b)
    titles = [c.title for c in merged]
    assert len(merged) == 2
    assert titles.count("NASA confirms Artemis launch window") == 1
    assert not any(c.is_google for c in merged)
    same_url = R.merge_candidates(
        [R.Candidate("A story", "https://x.com/a/"), R.Candidate("Totally other words", "https://www.x.com/a")]
    )
    assert len(same_url) == 1


def test_snippet_fallback_when_extraction_fails(monkeypatch):
    _install(monkeypatch, _handler(article_html="<html><body>nope</body></html>"))
    cands = R.merge_candidates(R._parse_feed(BING_RSS, google=False))
    stats = asyncio.run(R.extract_all(cands, "NASA Artemis"))
    assert all(not c.real_text for c in cands)
    assert cands[0].text == "NASA says the Artemis launch window opens in March."
    assert stats["failed"] == 0


def test_blocked_pages_counted(monkeypatch):
    _install(monkeypatch, _handler(article_status=403))
    cands = R.merge_candidates(R._parse_feed(BING_RSS, google=False))
    stats = asyncio.run(R.extract_all(cands, "NASA Artemis"))
    assert stats["blocked"] == 2


def test_full_pipeline_numbering_matches_sources(monkeypatch):
    _install(monkeypatch, _handler())
    res = asyncio.run(R.research_topic("Artemis launch window - Space.com"))
    assert res.grounded
    assert len(res.sources) >= 2
    import re

    tags = {int(n) for n in re.findall(r"\[(\d+)\]", res.brief)}
    assert tags == set(range(1, len(res.sources) + 1))
    for n, s in enumerate(res.sources, 1):
        header = next(ln for ln in res.brief.splitlines() if ln.startswith(f"[{n}] "))
        assert s.title in header


def test_ungrounded_when_everything_fails(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("network down")

    _install(monkeypatch, boom)
    res = asyncio.run(R.research_topic("Some topic nobody covers"))
    assert res.grounded is False
    assert res.brief == ""
    assert res.notes


def test_cache_hit(monkeypatch):
    calls = {"n": 0}
    inner = _handler()

    def counting(request):
        calls["n"] += 1
        return inner(request)

    _install(monkeypatch, counting)
    first = asyncio.run(R.research_topic("Artemis launch window"))
    n = calls["n"]
    assert first.grounded and n > 0
    second = asyncio.run(R.research_topic("  artemis LAUNCH window! "))
    assert calls["n"] == n
    assert second.brief == first.brief


def test_empty_topic_returns_ungrounded():
    assert asyncio.run(R.research_topic("   ")).grounded is False
