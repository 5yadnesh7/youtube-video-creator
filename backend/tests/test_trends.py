import asyncio
import sys
import types

import httpx
import pytest

from app.schemas import Trend
from app.services import trends as tr

TRENDS_RSS = """<?xml version="1.0"?><rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>Alpha Storm</title><ht:approx_traffic>500+</ht:approx_traffic></item>
<item><title>Solar Eclipse Tonight</title><ht:approx_traffic>1000+</ht:approx_traffic></item>
</channel></rss>"""

NEWS_RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Solar eclipse tonight - BBC News</title><link>http://n/1</link></item>
<item><title>New chip announced - The Verge</title><link>http://n/2</link></item>
</channel></rss>"""

REDDIT_RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>From RSS</title></item></channel></rss>"""

REDDIT_JSON = {
    "data": {
        "children": [
            {"data": {"title": "Cat rescues dog", "subreddit": "aww", "score": 9,
                      "permalink": "/r/aww/x"}},
            {"data": {"title": "Pinned", "stickied": True}},
        ]
    }
}


def make_handler(fail=()):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for key in fail:
            if key in url:
                return httpx.Response(500)
        if "trends.google.com" in url:
            return httpx.Response(200, text=TRENDS_RSS)
        if "news.google.com" in url:
            return httpx.Response(200, text=NEWS_RSS)
        if "reddit.com" in url:
            return httpx.Response(200, json=REDDIT_JSON)
        return httpx.Response(404)

    return handler


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    tr._cache.clear()
    monkeypatch.setattr(tr.settings, "openrouter_api_key", "")
    real = httpx.AsyncClient

    def install(handler):
        monkeypatch.setattr(
            tr.httpx,
            "AsyncClient",
            lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
        )

    install(make_handler())
    return install


def run(**kw):
    return asyncio.run(tr.get_top_trends(**kw))


def test_dedupe_fuzzy_and_case():
    items = [
        Trend(title="Solar Eclipse Tonight", source="google_trends"),
        Trend(title="solar eclipse tonight", source="google_news"),
        Trend(title="Solar eclipse visible tonight", source="reddit"),
        Trend(title="Totally different", source="reddit"),
    ]
    assert [t.title for t in tr.dedupe(items)] == ["Solar Eclipse Tonight", "Totally different"]


def test_merge_diversity_and_cleaning():
    res = run(limit=10)
    titles = [t.title.lower() for t in res]
    assert len(titles) == len(set(titles))
    assert {t.source for t in res} == {"google_trends", "google_news", "reddit"}
    assert not any("BBC" in t.title for t in res)
    assert "pinned" not in titles
    trend = next(t for t in res if t.title == "Alpha Storm")
    assert "500+" in trend.angle


def test_partial_failure(patch_env):
    patch_env(make_handler(fail=("trends.google.com", "reddit.com")))
    res = run(limit=10)
    assert res and {t.source for t in res} == {"google_news"}


def test_all_fail(patch_env):
    patch_env(make_handler(fail=("google.com", "reddit.com")))
    with pytest.raises(RuntimeError):
        run(limit=10)


def test_reddit_rss_fallback(patch_env):
    def handler(request):
        url = str(request.url)
        if "hot.json" in url:
            return httpx.Response(403)
        if "reddit.com" in url:
            return httpx.Response(200, text=REDDIT_RSS)
        return make_handler(fail=("google.com",))(request)

    patch_env(handler)
    assert [t.title for t in run(limit=5)] == ["From RSS"]


def test_fallback_ranking_when_llm_fails(monkeypatch):
    # key set but app.services.openrouter missing/failing -> heuristic round-robin
    monkeypatch.setattr(tr.settings, "openrouter_api_key", "k")
    monkeypatch.setitem(sys.modules, "app.services.openrouter", None)
    res = run(limit=3)
    assert [t.source for t in res] == ["google_trends", "google_news", "reddit"]


def test_llm_ranking(monkeypatch):
    async def chat_json(messages, model=None, temperature=0.7, max_tokens=None):
        return {
            "topics": [
                {"index": 0, "category": "Sports", "angle": "Why"},
                {"index": 99, "category": "x", "angle": ""},
            ]
        }

    mod = types.ModuleType("app.services.openrouter")
    mod.chat_json = chat_json
    monkeypatch.setitem(sys.modules, "app.services.openrouter", mod)
    monkeypatch.setattr(tr.settings, "openrouter_api_key", "k")
    res = run(limit=3)
    assert len(res) == 1 and res[0].category == "sports" and res[0].angle == "Why"


def test_cache_and_force(patch_env):
    calls = []

    def handler(request):
        calls.append(1)
        return make_handler()(request)

    patch_env(handler)
    run(limit=5)
    n = len(calls)
    run(limit=5)
    assert len(calls) == n
    run(limit=5, force=True)
    assert len(calls) == 2 * n
