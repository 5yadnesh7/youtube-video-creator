from app.schemas import Chapter, JobSettings, Script, Seo
from app.services.openrouter import chat_json

MAX_CHAPTERS = 8
DESC_LIMIT = 2000


def _fmt(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def _hashtag(tag: str) -> str:
    t = "".join(ch for ch in tag.strip().lstrip("#") if ch.isalnum() or ch == "_")
    return f"#{t}" if t else ""


def _clip_title(title: str, limit: int = 70) -> str:
    title = title.strip()
    if len(title) <= limit:
        return title
    cut = title[: limit - 1].rsplit(" ", 1)[0]
    return cut.rstrip(" ,:-|") or title[:limit]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = it.lower()
        if it and key not in seen:
            seen.add(key)
            out.append(it)
    return out


async def generate_seo(
    script: Script,
    settings: JobSettings,
    scene_starts: list[float] | None = None,
    model: str | None = None,
) -> Seo:
    vertical = settings.format == "vertical"
    starts = scene_starts or []

    chapters: list[Chapter] = []
    group = 1
    if not vertical and starts:
        n = min(len(script.scenes), len(starts))
        group = -(-n // MAX_CHAPTERS)
        for i in range(0, n, group):
            chapters.append(Chapter(start_seconds=0.0 if i == 0 else float(starts[i]), title=""))

    narration = " ".join(s.narration for s in script.scenes)[:2500]
    lang_note = (
        "Write in a mix of English and Hinglish (Hindi in Roman script), searchable by English keywords."
        if settings.language == "hinglish"
        else "Write in English."
    )
    chapter_note = ""
    if chapters:
        segs = [
            f"{ci}: {' '.join(s.narration for s in script.scenes[ci * group : (ci + 1) * group])[:200]}"
            for ci in range(len(chapters))
        ]
        chapter_note = (
            f"\n- chapter_titles: list of exactly {len(chapters)} short chapter titles (2-6 words), "
            "one per segment below, in order. The first is the intro.\nSegments:\n" + "\n".join(segs)
        )

    prompt = f"""Create YouTube SEO metadata for this {"Short (vertical)" if vertical else "video (horizontal)"}.
{lang_note}
Video title: {script.title}
Script: {narration}

Return JSON only with keys:
- title: SEO-optimized, max 70 characters, primary keyword first, compelling, no false clickbait.
- summary: 2-3 sentence summary of the video.
- hook_line: one punchy opening line for the description.
- tags: 10-15 search keywords/phrases (no # symbol).
- hashtags: 3-5 topical hashtags without the # symbol.{chapter_note}"""
    messages = [
        {"role": "system", "content": "You are a YouTube SEO expert. Reply with a single JSON object only."},
        {"role": "user", "content": prompt},
    ]

    try:
        data = await chat_json(messages, model=model or settings.model, temperature=0.6, max_tokens=2048)
    except RuntimeError:
        data = {}

    title = _clip_title(str(data.get("title") or script.title))
    summary = str(data.get("summary") or script.hook).strip()
    hook_line = str(data.get("hook_line") or script.hook).strip()

    raw_tags = data.get("tags") if isinstance(data.get("tags"), list) else []
    tags = _dedupe([str(t).strip().lstrip("#") for t in raw_tags])
    if len(tags) < 10:
        extra = [w.strip(".,:;!?").lower() for w in script.title.split() if len(w.strip(".,:;!?")) > 3]
        tags = _dedupe(tags + extra)
    tags = tags[:15]

    raw_ht = data.get("hashtags") if isinstance(data.get("hashtags"), list) else []
    hashtags = _dedupe([_hashtag(str(h)) for h in raw_ht])
    if not hashtags:
        hashtags = _dedupe([_hashtag(t) for t in tags[:3]])
    if vertical:
        hashtags = [h for h in hashtags if h.lower() != "#shorts"][:4] + ["#Shorts"]
    else:
        hashtags = hashtags[:5]

    if chapters:
        titles = data.get("chapter_titles") if isinstance(data.get("chapter_titles"), list) else []
        for i, ch in enumerate(chapters):
            given = str(titles[i]).strip()[:60] if i < len(titles) else ""
            ch.title = given or ("Intro" if i == 0 else f"Part {i + 1}")

    parts = [hook_line, "", summary]
    if chapters:
        parts += ["", "\n".join(f"{_fmt(c.start_seconds)} {c.title}" for c in chapters)]
    parts += ["", "Keywords: " + ", ".join(tags), "", " ".join(hashtags)]
    description = "\n".join(parts)
    if len(description) > DESC_LIMIT:
        tail = "\n\n" + " ".join(hashtags)
        description = description[: DESC_LIMIT - len(tail)].rstrip() + tail

    return Seo(title=title, description=description, tags=tags, hashtags=hashtags, chapters=chapters)
