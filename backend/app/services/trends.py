import asyncio
import html
import logging
import re
import time
from difflib import SequenceMatcher
from itertools import zip_longest

import feedparser
import httpx

from app.config import settings
from app.schemas import Trend

log = logging.getLogger(__name__)

GEOS = ["US", "IN", "GB", "CA", "AU", "DE", "BR", "JP"]
NEWS_TOPICS = ["TECHNOLOGY", "BUSINESS", "SCIENCE", "ENTERTAINMENT", "SPORTS", "WORLD"]
GOOGLE_TRENDS_URL = "https://trends.google.com/trending/rss?geo={geo}"
NEWS_TOP_URL = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
NEWS_TOPIC_URL = (
    "https://news.google.com/rss/headlines/section/topic/{topic}?hl=en-US&gl=US&ceid=US:en"
)
REDDIT_JSON_URL = "https://www.reddit.com/r/all/hot.json?limit=50"
REDDIT_RSS_URL = "https://www.reddit.com/r/all/hot/.rss"
USER_AGENT = "python:youtube-video-creator:v0.1 (trend fetcher)"
TIMEOUT = 10.0
CACHE_TTL = 30 * 60
CATEGORIES = [
    "technology", "business", "science", "entertainment", "sports",
    "world", "health", "lifestyle", "general",
]
SOURCE_ORDER = ["google_trends", "google_news", "reddit"]

_cache: dict[int, tuple[float, list[Trend]]] = {}


def _clean_title(title: str, strip_publisher: bool = False) -> str:
    title = html.unescape(title or "")
    title = re.sub(r"<[^>]+>", " ", title)
    if strip_publisher:
        title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title)
    return re.sub(r"\s+", " ", title).strip()


def _tokens(title: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", title.lower()))


def _is_duplicate(a: str, b: str) -> bool:
    if a.lower() == b.lower():
        return True
    ta, tb = _tokens(a), _tokens(b)
    if ta and tb:
        inter = len(ta & tb)
        if inter / len(ta | tb) >= 0.6:
            return True
        if min(len(ta), len(tb)) >= 3 and inter / min(len(ta), len(tb)) >= 0.85:
            return True
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.85


def dedupe(items: list[Trend]) -> list[Trend]:
    kept: list[Trend] = []
    for item in items:
        if not item.title:
            continue
        if any(_is_duplicate(item.title, k.title) for k in kept):
            continue
        kept.append(item)
    return kept


def _interleave(lists: list[list[Trend]]) -> list[Trend]:
    return [t for row in zip_longest(*lists) for t in row if t]


async def _fetch_google_trends(client: httpx.AsyncClient) -> list[Trend]:
    async def one(geo: str) -> list[Trend]:
        resp = await client.get(GOOGLE_TRENDS_URL.format(geo=geo))
        resp.raise_for_status()
        out = []
        for e in feedparser.parse(resp.text).entries:
            title = _clean_title(e.get("title", ""))
            if not title:
                continue
            traffic = e.get("ht_approx_traffic")
            angle = f"Searches: {traffic} ({geo})" if traffic else ""
            url = e.get("ht_news_item_url") or None
            out.append(Trend(title=title, source="google_trends", angle=angle, url=url))
        return out

    results = await asyncio.gather(*(one(g) for g in GEOS), return_exceptions=True)
    lists = []
    for geo, r in zip(GEOS, results):
        if isinstance(r, Exception):
            log.warning("google trends geo=%s failed: %s", geo, r)
        else:
            lists.append(r)
    if not lists:
        raise RuntimeError("google trends: all geos failed")
    return _interleave(lists)


async def _fetch_google_news(client: httpx.AsyncClient) -> list[Trend]:
    feeds = [(NEWS_TOP_URL, "general")] + [
        (NEWS_TOPIC_URL.format(topic=t), t.lower()) for t in NEWS_TOPICS
    ]

    async def one(url: str, category: str) -> list[Trend]:
        resp = await client.get(url)
        resp.raise_for_status()
        out = []
        for e in feedparser.parse(resp.text).entries[:25]:
            title = _clean_title(e.get("title", ""), strip_publisher=True)
            if title:
                out.append(
                    Trend(title=title, source="google_news", category=category,
                          url=e.get("link"))
                )
        return out

    results = await asyncio.gather(*(one(u, c) for u, c in feeds), return_exceptions=True)
    lists = []
    for (url, _), r in zip(feeds, results):
        if isinstance(r, Exception):
            log.warning("google news feed %s failed: %s", url, r)
        else:
            lists.append(r)
    if not lists:
        raise RuntimeError("google news: all feeds failed")
    return _interleave(lists)


async def _fetch_reddit(client: httpx.AsyncClient) -> list[Trend]:
    try:
        resp = await client.get(REDDIT_JSON_URL)
        resp.raise_for_status()
        out = []
        for child in resp.json()["data"]["children"]:
            d = child["data"]
            if d.get("stickied") or d.get("over_18"):
                continue
            title = _clean_title(d.get("title", ""))
            if title:
                sub = d.get("subreddit")
                permalink = d.get("permalink")
                out.append(
                    Trend(
                        title=title,
                        source="reddit",
                        angle=f"Hot on r/{sub} ({d.get('score', 0)} upvotes)" if sub else "",
                        url=("https://www.reddit.com" + permalink) if permalink else None,
                    )
                )
        if out:
            return out
    except Exception as exc:
        log.warning("reddit json failed (%s), trying rss", exc)
    resp = await client.get(REDDIT_RSS_URL)
    resp.raise_for_status()
    out = [
        Trend(title=_clean_title(e.get("title", "")), source="reddit", url=e.get("link"))
        for e in feedparser.parse(resp.text).entries
        if e.get("title")
    ]
    if not out:
        raise RuntimeError("reddit: empty feed")
    return out


async def _llm_rank(candidates: list[Trend], limit: int, model: str | None) -> list[Trend]:
    from app.services.llm import chat_json

    pool = candidates[:80]
    listing = "\n".join(f"{i}. [{t.source}] {t.title}" for i, t in enumerate(pool))
    messages = [
        {
            "role": "system",
            "content": (
                "You are a YouTube strategist who picks topics with the highest viral "
                "potential for short explainer videos. Reply with JSON only."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Pick the best {limit} topics from this list, favouring broad appeal, "
                "curiosity and variety (avoid near-duplicates, mix categories and sources).\n"
                f"Categories allowed: {', '.join(CATEGORIES)}.\n"
                'Return {"topics": [{"index": <int>, "category": <str>, "angle": '
                "<one-line video angle>}]} ordered best first.\n\n" + listing
            ),
        },
    ]
    data = await chat_json(messages, model=model, temperature=0.5)
    picks = data.get("topics") if isinstance(data, dict) else None
    if not picks:
        raise ValueError("LLM returned no topics")
    out, seen = [], set()
    for p in picks:
        try:
            idx = int(p["index"])
        except (KeyError, TypeError, ValueError):
            continue
        if idx in seen or not 0 <= idx < len(pool):
            continue
        seen.add(idx)
        base = pool[idx]
        cat = str(p.get("category", "")).lower().strip()
        if cat not in CATEGORIES:
            cat = base.category if base.category in CATEGORIES else "general"
        out.append(
            base.model_copy(update={"category": cat, "angle": str(p.get("angle", "")).strip()})
        )
        if len(out) >= limit:
            break
    if not out:
        raise ValueError("LLM picks were all invalid")
    return out


async def get_top_trends(
    limit: int = 10, model: str | None = None, force: bool = False
) -> list[Trend]:
    cached = _cache.get(limit)
    if not force and cached and time.time() - cached[0] < CACHE_TTL:
        return list(cached[1])

    fetchers = {
        "google_trends": _fetch_google_trends,
        "google_news": _fetch_google_news,
        "reddit": _fetch_reddit,
    }
    async with httpx.AsyncClient(
        timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        results = await asyncio.gather(
            *(f(client) for f in fetchers.values()), return_exceptions=True
        )

    by_source: dict[str, list[Trend]] = {}
    for name, r in zip(fetchers, results):
        if isinstance(r, Exception):
            log.warning("trend source %s failed: %s", name, r)
        elif r:
            by_source[name] = r
    if not by_source:
        raise RuntimeError("All trend sources failed")

    merged = dedupe(_interleave([by_source[s] for s in SOURCE_ORDER if s in by_source]))
    trends: list[Trend] | None = None
    if _llm_available():
        try:
            trends = await _llm_rank(merged, limit, model)
        except Exception as exc:
            log.warning("LLM ranking failed, using heuristic: %s", exc)
    if trends is None:
        trends = merged[:limit]

    _cache[limit] = (time.time(), trends)
    return list(trends)


def _llm_available() -> bool:
    try:
        from app.services.llm import provider_chain

        return bool(provider_chain())
    except Exception:
        return False
