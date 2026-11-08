import json
import re

from pydantic import ValidationError

from app.schemas import JobSettings, Research, Scene, Script
from app.services.openrouter import chat_json, chat_text

# Measured from real voices: edge/HeyGen speak English at ~141-151 wpm and Hinglish at ~128-137 wpm.
WPM = {"english": 150, "hinglish": 130}
TOLERANCE = 0.12
SCENE_TOLERANCE = 0.35
MAX_ATTEMPTS = 3


def _words(text: str) -> int:
    return len(text.split())


def _targets(settings: JobSettings) -> tuple[int, int]:
    words = round(settings.duration_seconds / 60 * WPM[settings.language])
    per_scene = 8 if settings.format == "vertical" else 11
    scenes = max(3, round(settings.duration_seconds / per_scene))
    return words, scenes


def _research_block(research: Research | None) -> str:
    if not research or not research.grounded or not research.brief.strip():
        return (
            "\nNo verified research is available for this topic. Write only what you are confident is accurate, "
            "avoid specific dates, numbers and quotes you are unsure about, and do not present guesses as news.\n"
        )
    sources = "\n".join(
        f"[{i}] {s.title} ({s.publisher})" if s.publisher else f"[{i}] {s.title}"
        for i, s in enumerate(research.sources, 1)
    )
    return f"""
RESEARCH (collected from recent articles; this is your ONLY source of facts):
{research.brief.strip()}

SOURCES:
{sources}

Research rules:
- Build the script from the facts above. Do not invent names, numbers, dates, quotes or events that are not in the research.
- If sources disagree, say so briefly ("reports differ on...") instead of picking one silently.
- If the research is thinner than the video length needs, spend the time on context, explanation, why it matters and what to watch next, still without making up specifics.
- Do not put source tags like [1] in the narration.
"""


def _build_prompt(
    topic: str, settings: JobSettings, words: int, scenes: int, research: Research | None = None
) -> list[dict]:
    if settings.language == "hinglish":
        lang = (
            "Narration language: Hinglish, meaning Hindi written in Roman script "
            '(e.g. "Aaj hum baat karenge..."). Do NOT use Devanagari. Casual, energetic, natural spoken style.'
        )
    else:
        lang = "Narration language: English, conversational and energetic spoken style."
    vertical = settings.format == "vertical"
    orientation = "vertical YouTube Short" if vertical else "horizontal YouTube video"
    span = "6-10" if vertical else "8-15"
    system = (
        "You are an expert YouTube scriptwriter. You write tight, engaging voice-over scripts "
        "and reply with a single JSON object only."
    )
    seconds = settings.duration_seconds
    length = f"{seconds} seconds" if seconds < 120 else f"{seconds // 60} minutes"
    user = f"""Write a voice-over script for a {orientation} about: {topic}
{_research_block(research)}
{lang}

Requirements:
- The video must run about {length} when read aloud at a natural pace, so the total narration length is about {words} words across all scenes (acceptable range {round(words * (1 - TOLERANCE))}-{round(words * (1 + TOLERANCE))}).
- About {scenes} scenes (each scene covers roughly {span} seconds of speech).
- Structure: the first scene is a strong hook that grabs attention in the first 3 seconds (a bold claim, surprising fact or question); then body scenes that deliver value in a clear order; the final scene is a short call to action (like, subscribe, comment).
- No stage directions, no emojis, no scene labels inside narration. Plain spoken text only.
- visual_query: ENGLISH only, concrete and searchable stock-footage keywords, 2-4 words (e.g. "city traffic night", "person typing laptop"). Never abstract concepts.
- media_type: "video" or "image"; use "video" for most scenes, "image" for a few where a still works (diagrams, landmarks, objects).
- mood: one word (e.g. energetic, calm, dramatic, inspiring, mysterious).

Return JSON exactly in this shape:
{{"title": "catchy video title", "hook": "the hook line, same as first scene narration", "scenes": [{{"narration": "...", "visual_query": "...", "media_type": "video", "mood": "energetic"}}]}}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse(data: dict) -> Script:
    scenes_raw = data.get("scenes")
    if not isinstance(scenes_raw, list):
        raise ValueError("missing scenes")
    scenes: list[Scene] = []
    for s in scenes_raw:
        if not isinstance(s, dict):
            continue
        narration = str(s.get("narration", "")).strip()
        if not narration:
            continue
        query = re.sub(r"\s+", " ", str(s.get("visual_query", "")).strip()) or "abstract background"
        query = " ".join(query.split()[:4])
        media = s.get("media_type", "video")
        scenes.append(
            Scene(
                narration=narration,
                visual_query=query,
                media_type=media if media in ("video", "image") else "video",
                mood=str(s.get("mood", "neutral")).strip().lower() or "neutral",
            )
        )
    if not scenes:
        raise ValueError("no valid scenes")
    title = str(data.get("title", "")).strip() or "Untitled"
    hook = str(data.get("hook", "")).strip() or scenes[0].narration
    return Script(title=title, hook=hook, scenes=scenes)


def _within(value: int, target: int, tol: float = TOLERANCE) -> bool:
    return target * (1 - tol) <= value <= target * (1 + tol)


def _clamp(script: Script, words: int, scenes: int) -> Script:
    out = list(script.scenes)
    max_scenes = round(scenes * (1 + SCENE_TOLERANCE))
    if len(out) > max_scenes:
        out = out[: max_scenes - 1] + [out[-1]]
    max_words = round(words * (1 + TOLERANCE))
    excess = sum(_words(s.narration) for s in out) - max_words
    if excess > 0:
        i = len(out) - 2
        while i > 0 and excess > 0 and len(out) > 3:
            n = _words(out[i].narration)
            if n <= excess + 4:
                excess -= n
                out.pop(i)
            i -= 1
    if excess > 0 and len(out) > 1:
        idx = len(out) - 2 if len(out) > 2 else 0
        ws = out[idx].narration.split()
        keep = max(6, len(ws) - excess)
        trimmed = " ".join(ws[:keep]).rstrip(",;:")
        if not trimmed.endswith((".", "!", "?")):
            trimmed += "."
        out[idx] = out[idx].model_copy(update={"narration": trimmed})
    return Script(title=script.title, hook=script.hook, scenes=out)


async def generate_script(
    topic: str,
    settings: JobSettings,
    model: str | None = None,
    research: Research | None = None,
) -> Script:
    words, scenes = _targets(settings)
    messages = _build_prompt(topic, settings, words, scenes, research)
    best: Script | None = None
    best_score = float("inf")
    last_err: Exception | None = None

    for _ in range(MAX_ATTEMPTS):
        try:
            data = await chat_json(messages, model=model or settings.model, temperature=0.8, max_tokens=max(4096, words * 5))
            script = _parse(data)
        except (ValueError, ValidationError, RuntimeError) as e:
            last_err = e
            continue
        w = sum(_words(s.narration) for s in script.scenes)
        n = len(script.scenes)
        if _within(w, words) and _within(n, scenes, SCENE_TOLERANCE):
            return script
        score = abs(w - words) / words + abs(n - scenes) / scenes
        if score < best_score:
            best, best_score = script, score
        messages = messages + [
            {
                "role": "user",
                "content": (
                    f"Your script had {w} words and {n} scenes. I need about {words} words and about {scenes} scenes. "
                    "Rewrite the full script and return JSON only."
                ),
            }
        ]
    if best is None:
        raise RuntimeError(f"Script generation failed: {last_err}")
    return _clamp(best, words, scenes)


async def to_devanagari(text: str, model: str | None = None) -> str:
    if not text.strip():
        return text
    messages = [
        {
            "role": "system",
            "content": "You transliterate Roman-script Hinglish into natural Devanagari Hindi for text-to-speech.",
        },
        {
            "role": "user",
            "content": (
                "Convert this Hinglish (Hindi in Roman script) into natural Devanagari Hindi. "
                "Write English loanwords the way they are commonly written in Devanagari. "
                "Keep the meaning and punctuation. Output ONLY the Devanagari text, no quotes, no explanation.\n\n"
                f"{text}"
            ),
        },
    ]
    try:
        out = await chat_text(messages, model=model, temperature=0.2)
    except Exception:
        return text
    out = out.strip().strip("\"'`“”‘’").strip()
    if not out or not re.search(r"[ऀ-ॿ]", out):
        return text
    return out


_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


async def to_devanagari_batch(texts: list[str], model: str | None = None) -> list[str]:
    """Converts every text in one request; any item the model gets wrong keeps its original text."""
    if not texts:
        return []
    messages = [
        {
            "role": "system",
            "content": "You transliterate Roman-script Hinglish into natural Devanagari Hindi for text-to-speech. Reply with a single JSON object only.",
        },
        {
            "role": "user",
            "content": (
                "Convert each Hinglish (Hindi in Roman script) item into natural Devanagari Hindi. "
                "Write English loanwords the way they are commonly written in Devanagari. "
                "Keep the meaning and punctuation of every item.\n"
                f'Return {{"items": [...]}} with exactly {len(texts)} strings, in the same order.\n\n'
                f"{json.dumps(texts, ensure_ascii=False)}"
            ),
        },
    ]
    try:
        data = await chat_json(messages, model=model, temperature=0.2, max_tokens=4096)
    except Exception:
        return list(texts)
    items = data.get("items")
    if not isinstance(items, list) or len(items) != len(texts):
        return list(texts)
    return [
        item.strip() if isinstance(item, str) and _DEVANAGARI.search(item) else original
        for item, original in zip(items, texts)
    ]
