import asyncio
import html
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote, unquote, urlparse

import feedparser
import httpx

from app.config import settings
from app.schemas import Research, ResearchSource

log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
# Wikimedia returns 403 for browser-like UAs without contact info and for bare bot UAs.
WIKI_UA = "YouTubeVideoCreator/0.1 (https://github.com/yadnesh/youtube-video-creator; research module) httpx"

TOTAL_BUDGET = 50.0
REQUEST_TIMEOUT = 8.0
DISCOVERY_BUDGET = 9.0
EXTRACT_BUDGET = 9.0
CONCURRENCY = 5
MAX_DOWNLOAD_BYTES = 1_500_000
MAX_CANDIDATES = 8
MAX_ARTICLES = 6
MAX_WORDS_PER_ARTICLE = 1500
MAX_TOTAL_LLM_WORDS = 9000
MAX_BRIEF_WORDS = 1800
MIN_REAL_CHARS = 300
LONG_TEXT_WORDS = 400
CACHE_TTL = 30 * 60
GDELT_MIN_INTERVAL = 5.0

GNEWS_SEARCH = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
BING_SEARCH = "https://www.bing.com/news/search?q={q}&format=rss"
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"

STOPWORDS = {
    "a", "an", "the", "of", "to", "in", "on", "at", "for", "and", "or", "but", "is", "are",
    "was", "were", "be", "as", "by", "with", "from", "that", "this", "it", "its", "after",
    "before", "over", "into", "says", "say", "said", "how", "why", "what", "who", "will",
}

_cache: dict[str, tuple[float, Research]] = {}
_gdelt_last = 0.0


@dataclass
class Candidate:
    title: str
    url: str
    publisher: str = ""
    published: datetime | None = None
    snippet: str = ""
    kind: str = "news"  # news | topic_url | wikipedia
    text: str = ""
    real_text: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def is_google(self) -> bool:
        return _host(self.url) == "news.google.com"

    @property
    def published_str(self) -> str:
        return self.published.strftime("%Y-%m-%d") if self.published else ""


def _client(user_agent: str = BROWSER_UA) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.9"},
        follow_redirects=True,
    )


def _host(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def _clean(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_publisher(title: str) -> tuple[str, str]:
    m = re.search(r"\s+[-–—|]\s+([^-–—|]{2,40})$", title)
    if m:
        return title[: m.start()].strip(), m.group(1).strip()
    return title.strip(), ""


def _norm_topic(topic: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", topic.lower()))


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _is_similar_title(a: str, b: str) -> bool:
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


def shorten_query(topic: str, max_words: int = 8) -> str:
    title, _ = _strip_publisher(_clean(topic))
    words = title.split()
    if len(words) <= max_words:
        return title
    keep: list[str] = []
    for i, w in enumerate(words):
        bare = re.sub(r"^\W+|\W+$", "", w)
        if not bare:
            continue
        if bare.lower() in STOPWORDS and i > 0:
            continue
        if bare[0].isupper() or any(c.isdigit() for c in bare) or i == 0:
            keep.append(bare)
    if len(keep) < 3:
        return " ".join(words[:max_words])
    return " ".join(keep[:max_words])


def unwrap_bing_url(link: str) -> str:
    try:
        parsed = urlparse(link)
        if _host(link).endswith("bing.com") and "apiclick" in parsed.path.lower():
            real = parse_qs(parsed.query).get("url")
            if real:
                return unquote(real[0])
    except ValueError:
        pass
    return link


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.strptime(value, "%Y%m%dT%H%M%SZ")
        except ValueError:
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _norm_url(url: str) -> str:
    p = urlparse(url)
    return f"{_host(url)}{p.path.rstrip('/')}".lower()


def merge_candidates(items: list[Candidate]) -> list[Candidate]:
    kept: list[Candidate] = []
    for c in items:
        if not c.title or not c.url:
            continue
        dup_idx = None
        for i, k in enumerate(kept):
            if _norm_url(c.url) == _norm_url(k.url) or _is_similar_title(c.title, k.title):
                dup_idx = i
                break
        if dup_idx is None:
            kept.append(c)
        elif kept[dup_idx].is_google and not c.is_google:
            c.published = c.published or kept[dup_idx].published
            c.snippet = c.snippet or kept[dup_idx].snippet
            kept[dup_idx] = c
    return kept


def rank_candidates(items: list[Candidate], limit: int = MAX_CANDIDATES) -> list[Candidate]:
    pinned = [c for c in items if c.kind == "topic_url"]
    wiki = [c for c in items if c.kind == "wikipedia"][:1]
    news = [c for c in items if c.kind == "news"]
    epoch = datetime.fromtimestamp(0, timezone.utc)
    news.sort(key=lambda c: c.published or epoch, reverse=True)

    out = list(pinned)
    seen_pub = {(_host(c.url) or c.publisher.lower()) for c in pinned}
    budget = limit - len(pinned) - len(wiki)
    leftovers = []
    for c in news:
        key = c.publisher.lower() if c.is_google else (_host(c.url) or c.publisher.lower())
        key = key or c.title
        if key in seen_pub:
            leftovers.append(c)
            continue
        seen_pub.add(key)
        out.append(c)
        if len(out) - len(pinned) >= budget:
            break
    for c in leftovers:
        if len(out) - len(pinned) >= budget:
            break
        out.append(c)
    return out + wiki


async def _get_text(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    resp = await asyncio.wait_for(client.get(url, **kw), REQUEST_TIMEOUT + 2)
    resp.raise_for_status()
    return resp


def _parse_feed(text: str, google: bool) -> list[Candidate]:
    out = []
    for e in feedparser.parse(text).entries:
        raw_title = _clean(e.get("title", ""))
        title, pub_suffix = _strip_publisher(raw_title)
        src = e.get("source") or e.get("news_source")
        publisher = ""
        if isinstance(src, dict):
            publisher = _clean(src.get("title", ""))
        elif isinstance(src, str):
            publisher = _clean(src)
        publisher = publisher or pub_suffix
        link = e.get("link") or ""
        if not link:
            continue
        link = link if google else unwrap_bing_url(link)
        snippet = _clean(e.get("summary", ""))
        if snippet.lower().startswith(title.lower()[:40]) and len(snippet) < len(title) + len(publisher) + 15:
            snippet = ""
        out.append(
            Candidate(
                title=title, url=link, publisher=publisher,
                published=_parse_date(e.get("published")), snippet=snippet,
            )
        )
    return out


async def _search_gnews(client: httpx.AsyncClient, q: str) -> list[Candidate]:
    resp = await _get_text(client, GNEWS_SEARCH.format(q=quote(q)))
    return _parse_feed(resp.text, google=True)[:12]


async def _search_bing(client: httpx.AsyncClient, q: str) -> list[Candidate]:
    resp = await _get_text(client, BING_SEARCH.format(q=quote(q)))
    return _parse_feed(resp.text, google=False)[:12]


async def _search_gdelt(client: httpx.AsyncClient, q: str) -> list[Candidate]:
    global _gdelt_last
    if time.monotonic() - _gdelt_last < GDELT_MIN_INTERVAL:
        return []
    _gdelt_last = time.monotonic()
    resp = await asyncio.wait_for(
        _get_text(
            client, GDELT_URL,
            params={"query": q, "mode": "artlist", "format": "json", "maxrecords": 15, "sort": "datedesc"},
        ),
        5,
    )
    try:
        articles = resp.json().get("articles", [])
    except ValueError:
        raise RuntimeError("gdelt returned non-JSON (likely rate limited)")
    out = []
    for a in articles:
        if a.get("language") and str(a["language"]).lower() != "english":
            continue
        title = _clean(a.get("title", ""))
        url = a.get("url", "")
        if title and url:
            out.append(
                Candidate(title=title, url=url, publisher=a.get("domain", ""),
                          published=_parse_date(a.get("seendate")))
            )
    return out


async def _search_wikipedia(q: str) -> Candidate | None:
    async with _client(WIKI_UA) as client:
        resp = await _get_text(
            client, WIKI_API,
            params={"action": "opensearch", "search": q, "limit": 3, "format": "json"},
        )
        data = resp.json()
        titles = data[1] if isinstance(data, list) and len(data) > 1 else []
        if not titles:
            fb = await _get_text(
                client, WIKI_API,
                params={"action": "query", "list": "search", "srsearch": q, "srlimit": 3, "format": "json"},
            )
            titles = [h["title"] for h in fb.json().get("query", {}).get("search", [])]
        qt = _tokens(q) - STOPWORDS
        pick = None
        for t in titles:
            tt = _tokens(t) - STOPWORDS
            if tt and qt and len(tt & qt) / len(tt) >= 0.5 and len(tt & qt) / len(qt) >= 0.3:
                pick = t
                break
        if not pick:
            return None
        sresp = await _get_text(client, WIKI_SUMMARY.format(title=quote(pick.replace(" ", "_"), safe="")))
        s = sresp.json()
        extract = _clean(s.get("extract", ""))
        if s.get("type") == "disambiguation" or len(extract) < 80:
            return None
        url = (s.get("content_urls") or {}).get("desktop", {}).get("page") or (
            "https://en.wikipedia.org/wiki/" + quote(pick.replace(" ", "_"))
        )
        return Candidate(
            title=s.get("title") or pick, url=url, publisher="Wikipedia",
            snippet=extract, text=extract, real_text=len(extract) >= MIN_REAL_CHARS, kind="wikipedia",
        )


async def _llm_queries(topic: str, model: str | None) -> list[str]:
    from app.services.openrouter import chat_json

    messages = [
        {"role": "system", "content": "You write news search queries. Reply with JSON only."},
        {
            "role": "user",
            "content": (
                "Give 2 short web news search queries (3-7 words each, key names/events only, "
                'no quotes) for this topic. Return {"queries": ["...", "..."]}.\n\nTopic: ' + topic
            ),
        },
    ]
    data = await asyncio.wait_for(chat_json(messages, model=model, temperature=0.2, max_tokens=120), 10)
    qs = data.get("queries") if isinstance(data, dict) else None
    return [_clean(str(q)) for q in (qs or []) if _clean(str(q))][:2]


async def build_queries(topic: str, model: str | None = None) -> list[str]:
    cleaned, _ = _strip_publisher(_clean(topic))
    queries = [cleaned, shorten_query(topic)]
    if settings.openrouter_api_key and len(cleaned.split()) > 6:
        try:
            queries += await _llm_queries(cleaned, model)
        except Exception as exc:
            log.info("research: LLM query building failed (%s), using heuristics", exc)
    out: list[str] = []
    for q in queries:
        if q and not any(q.lower() == o.lower() for o in out):
            out.append(q)
    return out[:3]


def _is_google_redirect(url: str) -> bool:
    return _host(url) == "news.google.com"


def _decode_google_sync(url: str) -> str | None:
    from googlenewsdecoder import gnewsdecoder

    res = gnewsdecoder(url, interval=0)
    if res.get("status") or res.get("success"):
        return res.get("decoded_url")
    return None


async def _resolve_google(c: Candidate, sem: asyncio.Semaphore) -> None:
    async with sem:
        try:
            real = await asyncio.wait_for(asyncio.to_thread(_decode_google_sync, c.url), 8)
        except Exception as exc:
            log.debug("research: google url decode failed: %s", exc)
            return
    if real and not _is_google_redirect(real):
        c.extra["google_url"] = c.url
        c.url = real


async def discover(
    topic: str, queries: list[str], topic_url: str | None
) -> tuple[list[Candidate], dict[str, int]]:
    counts: dict[str, int] = {}
    tasks: dict[str, asyncio.Future] = {}
    short = queries[-1] if len(queries) > 1 else queries[0]
    async with _client() as client:
        for i, q in enumerate(queries[:2]):
            tasks[f"gnews{i}"] = asyncio.ensure_future(_search_gnews(client, q))
            tasks[f"bing{i}"] = asyncio.ensure_future(_search_bing(client, q))
        tasks["gdelt"] = asyncio.ensure_future(_search_gdelt(client, min(queries, key=len)))
        tasks["wikipedia"] = asyncio.ensure_future(_search_wikipedia(short if len(short) < 60 else queries[0]))
        done, pending = await asyncio.wait(tasks.values(), timeout=DISCOVERY_BUDGET)
        for p in pending:
            p.cancel()

    items: list[Candidate] = []
    wiki: Candidate | None = None
    for name, t in tasks.items():
        base = re.sub(r"\d+$", "", name)
        if t not in done:
            log.info("research: %s timed out", name)
            continue
        try:
            res = t.result()
        except Exception as exc:
            log.info("research: %s failed: %s", name, exc)
            continue
        if name == "wikipedia":
            wiki = res
            counts[base] = 1 if res else 0
        else:
            counts[base] = counts.get(base, 0) + len(res)
            items.extend(res)
    log.info("research: discovery %s", counts)

    pinned: list[Candidate] = []
    if topic_url and topic_url.startswith("http") and not _is_google_redirect(topic_url):
        pinned.append(Candidate(title=topic, url=unwrap_bing_url(topic_url), kind="topic_url"))
    merged = merge_candidates(items)
    ranked = rank_candidates(pinned + merged + ([wiki] if wiki else []))
    return ranked, counts


def _dedupe_paragraphs(text: str, seen: set[str], max_words: int) -> str:
    out, words = [], 0
    for para in re.split(r"\n+", text):
        para = para.strip()
        if len(para) < 25:
            continue
        key = re.sub(r"\W+", " ", para.lower()).strip()
        key = key[:120]
        if key in seen:
            continue
        seen.add(key)
        n = len(para.split())
        if words + n > max_words:
            break
        out.append(para)
        words += n
    return "\n".join(out)


def _extract_sync(page_html: str, url: str) -> str:
    import trafilatura

    return trafilatura.extract(
        page_html, url=url, include_comments=False, include_tables=False, favor_precision=True
    ) or ""


async def _download(client: httpx.AsyncClient, url: str) -> tuple[str, str]:
    async with client.stream("GET", url) as resp:
        if resp.status_code >= 400:
            raise _HttpStatus(resp.status_code)
        ctype = resp.headers.get("content-type", "").lower()
        if ctype and "html" not in ctype and "xml" not in ctype:
            raise RuntimeError(f"non-html content-type {ctype}")
        buf = bytearray()
        async for chunk in resp.aiter_bytes():
            buf.extend(chunk)
            if len(buf) >= MAX_DOWNLOAD_BYTES:
                break
        enc = resp.encoding or "utf-8"
        final = str(resp.url)
    return bytes(buf).decode(enc, errors="replace"), final


class _HttpStatus(Exception):
    def __init__(self, code: int):
        super().__init__(f"HTTP {code}")
        self.code = code


def _key_tokens(s: str) -> set[str]:
    return {t for t in _tokens(s) if len(t) > 2 and t not in STOPWORDS}


def _is_relevant(text: str, title: str, topic_tokens: set[str]) -> bool:
    if not topic_tokens:
        return True
    found = topic_tokens & _tokens(text[:6000])
    return len(found) / len(topic_tokens) >= 0.5


async def _extract_one(
    client: httpx.AsyncClient,
    c: Candidate,
    sem: asyncio.Semaphore,
    seen: set[str],
    stats: dict[str, int],
    topic_tokens: set[str],
) -> None:
    if c.kind == "wikipedia":
        c.text = _dedupe_paragraphs(c.text, seen, MAX_WORDS_PER_ARTICLE) or c.text
        return
    text = ""
    if not c.is_google:
        async with sem:
            try:
                page, final = await asyncio.wait_for(_download(client, c.url), REQUEST_TIMEOUT)
                if _is_google_redirect(final) or "consent." in _host(final):
                    raise RuntimeError("redirected to consent/interstitial")
                text = await asyncio.to_thread(_extract_sync, page, c.url)
            except _HttpStatus as exc:
                stats["blocked" if exc.code in (401, 402, 403, 429) else "failed"] += 1
                log.info("research: %s -> %s", _host(c.url), exc)
            except Exception as exc:
                stats["failed"] += 1
                log.info("research: fetch %s failed: %s", _host(c.url), exc)
    else:
        stats["unresolved"] += 1
    text = _dedupe_paragraphs(text, seen, MAX_WORDS_PER_ARTICLE) if text else ""
    if len(text) >= MIN_REAL_CHARS and _is_relevant(text, c.title, topic_tokens):
        c.text, c.real_text = text, True
    else:
        c.text, c.real_text = c.snippet or "", False


async def extract_all(cands: list[Candidate], topic: str = "") -> dict[str, int]:
    topic_tokens = _key_tokens(shorten_query(topic)) if topic else set()
    stats = {"blocked": 0, "failed": 0, "unresolved": 0}
    sem = asyncio.Semaphore(CONCURRENCY)
    seen: set[str] = set()
    async with _client() as client:
        await asyncio.gather(
            *(_resolve_google(c, sem) for c in cands if c.is_google and c.kind == "news"),
            return_exceptions=True,
        )
        # URLs may have changed after resolving; drop newly-created duplicates
        deduped = merge_candidates(cands)
        cands[:] = deduped
        tasks = [asyncio.ensure_future(_extract_one(client, c, sem, seen, stats, topic_tokens)) for c in cands]
        done, pending = await asyncio.wait(tasks, timeout=EXTRACT_BUDGET)
        for p in pending:
            p.cancel()
        for c, t in zip(cands, tasks):
            if t in pending:
                c.text, c.real_text = c.snippet or "", False
    stats["unresolved"] = sum(1 for c in cands if c.is_google)
    return stats


def select_articles(cands: list[Candidate]) -> list[Candidate]:
    real = [c for c in cands if c.real_text]
    chosen = real[:MAX_ARTICLES]
    if len(chosen) < 4:
        for c in cands:
            if c not in chosen and c.text and len(chosen) < 4:
                chosen.append(c)
    order = {id(c): i for i, c in enumerate(cands)}
    chosen.sort(key=lambda c: order[id(c)])
    return chosen


def _sentences(text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", text)
    flat = re.sub(r"\b(No|Mr|Mrs|Ms|Dr|St|Jr|Sr|vs|U\.S|Inc|Corp|Co|Gov|Sen|Rep)\.\s", lambda m: m.group(0).replace(". ", ".\u0000"), flat)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", flat)
    return [p.replace("\u0000", " ").strip() for p in parts if len(p.strip()) > 25]


def _truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + " ..."


def heuristic_brief(articles: list[Candidate]) -> str:
    blocks = []
    for n, c in enumerate(articles, 1):
        meta = ", ".join(x for x in (c.publisher, c.published_str) if x)
        lines = [f"[{n}] {c.title}" + (f" ({meta})" if meta else "")]
        for s in _sentences(c.text)[:3]:
            lines.append(f"- {s} [{n}]")
        blocks.append("\n".join(lines))
    return _truncate_words("\n\n".join(blocks), MAX_BRIEF_WORDS)


async def llm_brief(topic: str, articles: list[Candidate], model: str | None, timeout: float) -> str:
    from app.services.openrouter import chat_text

    per = min(900, max(300, MAX_TOTAL_LLM_WORDS // max(1, len(articles))))
    parts = []
    for n, c in enumerate(articles, 1):
        meta = " | ".join(x for x in (c.publisher, c.published_str) if x)
        parts.append(f"=== SOURCE [{n}] {c.title} ({meta}) ===\n{_truncate_words(c.text, per)}")
    messages = [
        {
            "role": "system",
            "content": (
                "You are a meticulous news researcher preparing a fact brief for a video script writer. "
                "Use ONLY the provided source texts. Never add outside knowledge, never speculate."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Topic: {topic}\n\nWrite a FACT BRIEF in English as 12-25 bullet points ('- ' prefix). "
                "Cover concrete, verifiable facts: who, what, when, where, numbers, direct quotes, "
                "background context, why it matters, differing viewpoints, and what happens next. "
                "Tag every bullet with the source numbers it comes from, like [1] or [1][3]. "
                "If sources conflict, add a bullet starting with 'CONFLICT:' stating both versions with tags. "
                "Skip anything not supported by the texts. No intro, no outro, under 1500 words.\n\n"
                + "\n\n".join(parts)
            ),
        },
    ]
    text = await asyncio.wait_for(
        chat_text(messages, model=model, temperature=0.2, max_tokens=2200), timeout
    )
    text = text.strip()
    n_src = len(articles)

    def fix_tag(m: re.Match) -> str:
        return m.group(0) if 1 <= int(m.group(1)) <= n_src else ""

    text = re.sub(r"\[(\d{1,2})\]", fix_tag, text)
    bullets = [ln for ln in text.splitlines() if re.match(r"\s*[-*•\d]", ln)]
    if len(bullets) < 5:
        raise ValueError("LLM brief too short / not bulleted")
    return _truncate_words(text, MAX_BRIEF_WORDS)


def _ungrounded(notes: str) -> Research:
    return Research(grounded=False, brief="", notes=notes)


async def _pipeline(
    topic: str, topic_url: str | None, topic_angle: str | None, model: str | None, deadline: float
) -> Research:
    loop = asyncio.get_running_loop()
    queries = await build_queries(topic, model)
    log.info("research: queries=%s", queries)
    cands, counts = await discover(topic, queries, topic_url)
    if not cands:
        return _ungrounded(f"no articles found (discovery: {counts})")
    log.info("research: %d candidates after merge/rank", len(cands))

    stats = await extract_all(cands, topic)
    articles = select_articles(cands)
    real = [c for c in articles if c.real_text]
    log.info("research: %d real texts of %d candidates, stats=%s", len(real), len(cands), stats)
    if not articles:
        return _ungrounded(f"found {len(cands)} articles but none had readable text")

    words = sum(len(c.text.split()) for c in real)
    grounded = len(real) >= 2 or (len(real) == 1 and words >= LONG_TEXT_WORDS)

    brief = ""
    llm_note = ""
    if grounded and settings.openrouter_api_key:
        remaining = deadline - loop.time() - 2
        if remaining > 4:
            try:
                brief = await llm_brief(topic, articles, model, remaining)
            except Exception as exc:
                log.info("research: LLM condense failed (%s), heuristic brief", type(exc).__name__)
                llm_note = ", LLM condense failed (heuristic brief)"
    if not brief:
        brief = heuristic_brief(articles)

    sources = [
        ResearchSource(title=c.title, url=c.url, publisher=c.publisher, published=c.published_str)
        for c in articles
    ]
    bits = [f"{len(articles)} sources ({len(real)} with full text)"]
    if stats["blocked"]:
        bits.append(f"{stats['blocked']} paywalled/blocked skipped")
    if stats["failed"]:
        bits.append(f"{stats['failed']} failed to load")
    note = ", ".join(bits) + llm_note
    if not grounded:
        note = "thin research: " + note
    return Research(brief=brief, sources=sources, grounded=grounded, notes=note)


async def research_topic(
    topic: str,
    topic_url: str | None = None,
    topic_angle: str | None = None,
    model: str | None = None,
) -> Research:
    topic = (topic or "").strip()
    if not topic:
        return _ungrounded("empty topic")
    key = _norm_topic(topic)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        log.info("research: cache hit for %r", key)
        return hit[1].model_copy(deep=True)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + TOTAL_BUDGET
    try:
        result = await asyncio.wait_for(
            _pipeline(topic, topic_url, topic_angle, model, deadline), TOTAL_BUDGET
        )
    except asyncio.TimeoutError:
        log.warning("research: pipeline timed out after %.0fs", TOTAL_BUDGET)
        return _ungrounded("research timed out")
    except Exception as exc:
        log.warning("research: pipeline failed: %s", exc)
        return _ungrounded(f"research failed: {type(exc).__name__}")
    if result.grounded:
        _cache[key] = (time.time(), result)
    return result
