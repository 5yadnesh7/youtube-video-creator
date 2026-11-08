import asyncio
import json
import re
import threading
import time
from collections import deque
from typing import Any

import httpx

from app.config import settings
from app.schemas import ModelInfo

FALLBACK_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
CACHE_TTL = 30 * 60
MAX_RETRIES = 3
BACKOFF_BASE = 1.5
MAX_FALLBACK_MODELS = 3
RETRYABLE = {408, 429, 500, 502, 503, 504}

PREFERRED_FAMILIES = [
    "llama-3.3-70b",
    "qwen3",
    "qwen",
    "deepseek",
    "gemma-3",
    "gemma",
    "mistral",
    "llama",
]

_cache: dict[str, Any] = {"at": 0.0, "models": []}

DAILY_LIMIT_MARKER = "free-models-per-day"
# Re-probe after this long even if the reset is later, in case credits were added.
DAILY_LIMIT_RECHECK = 600.0
_daily_block: tuple[float, str] | None = None


class DailyLimitError(RuntimeError):
    pass


class RateLimitError(RuntimeError):
    def __init__(self, wait: float):
        super().__init__(f"OpenRouter per-minute rate limit reached; retry in {wait:.0f}s")
        self.wait = wait


# The free tier allows 20 requests/minute across all models, text and voice alike;
# stay under it so retries and per-scene calls never starve each other.
RATE_LIMIT_PER_MIN = 16
MINUTE_LIMIT_MARKER = "free-models-per-min"
_window: deque[float] = deque()
_window_lock = threading.Lock()


def _reserve_slot() -> float:
    with _window_lock:
        now = time.monotonic()
        while _window and now - _window[0] >= 60:
            _window.popleft()
        if len(_window) < RATE_LIMIT_PER_MIN:
            _window.append(now)
            return 0.0
        return 60 - (now - _window[0]) + 0.05


async def throttle() -> None:
    while (wait := _reserve_slot()) > 0:
        await asyncio.sleep(wait)


def minute_limit_wait(resp: httpx.Response) -> float | None:
    """Seconds to wait when resp is the per-minute free-tier 429, else None."""
    if resp.status_code != 429 or MINUTE_LIMIT_MARKER not in resp.text:
        return None
    wait = 20.0
    try:
        reset = int(resp.json()["error"]["metadata"]["headers"]["X-RateLimit-Reset"]) / 1000
        wait = reset - time.time()
    except (ValueError, KeyError, TypeError):
        pass
    return min(max(wait, 1.0) + 0.5, 65.0)


def note_daily_limit(resp: httpx.Response) -> DailyLimitError | None:
    """Records and returns an error when resp is the free-tier per-day 429, else None."""
    global _daily_block
    if resp.status_code != 429 or DAILY_LIMIT_MARKER not in resp.text:
        return None
    reset = time.time() + 3600
    try:
        reset = int(resp.json()["error"]["metadata"]["headers"]["X-RateLimit-Reset"]) / 1000
    except (ValueError, KeyError, TypeError):
        pass
    when = time.strftime("%d %b %H:%M", time.localtime(reset))
    message = (
        "OpenRouter free-tier daily limit reached (50 requests/day across free models). "
        f"It resets at {when} local time. Wait, or add credits on openrouter.ai to raise it."
    )
    _daily_block = (min(reset, time.time() + DAILY_LIMIT_RECHECK), message)
    return DailyLimitError(message)


def daily_limit_error() -> DailyLimitError | None:
    if _daily_block and time.time() < _daily_block[0]:
        return DailyLimitError(_daily_block[1])
    return None


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0))


def _headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:3000",
        "X-Title": "AI YouTube Video Creator",
    }
    if settings.openrouter_api_key:
        headers["Authorization"] = f"Bearer {settings.openrouter_api_key}"
    return headers


def _is_free(m: dict) -> bool:
    mid = str(m.get("id", ""))
    pricing = m.get("pricing") or {}
    zero = all(str(pricing.get(k, "")) in ("0", "0.0", "0.00") for k in ("prompt", "completion"))
    return zero or mid.endswith(":free")


def _outputs_text(m: dict) -> bool:
    arch = m.get("architecture") or {}
    outs = arch.get("output_modalities")
    if outs is None:
        modality = str(arch.get("modality", "text->text"))
        outs = modality.split("->")[-1].replace("+", ",").split(",")
    return [str(o).strip() for o in outs] == ["text"]


def _rank(m: ModelInfo) -> tuple[int, int]:
    mid = m.id.lower()
    for i, fam in enumerate(PREFERRED_FAMILIES):
        if fam in mid:
            return (i, -m.context_length)
    return (len(PREFERRED_FAMILIES), -m.context_length)


async def list_free_models() -> list[ModelInfo]:
    if _cache["models"] and time.time() - _cache["at"] < CACHE_TTL:
        return list(_cache["models"])
    async with _make_client() as client:
        resp = await client.get(f"{settings.openrouter_base_url}/models", headers=_headers())
        resp.raise_for_status()
        data = resp.json().get("data", [])
    models = [
        ModelInfo(
            id=m["id"],
            name=m.get("name") or m["id"],
            context_length=int(m.get("context_length") or 0),
        )
        for m in data
        if m.get("id") and _is_free(m) and _outputs_text(m)
    ]
    models.sort(key=_rank)
    _cache["models"] = models
    _cache["at"] = time.time()
    return list(models)


async def default_model() -> str:
    try:
        models = await list_free_models()
    except Exception:
        return FALLBACK_MODEL
    return models[0].id if models else FALLBACK_MODEL


def _extract_content(payload: dict) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ValueError(f"unexpected response: {str(payload)[:200]}")
    if not content or not str(content).strip():
        raise ValueError("empty completion")
    return str(content)


async def _call_model(client: httpx.AsyncClient, model: str, body: dict) -> str:
    last: Exception | None = None
    for attempt in range(MAX_RETRIES):
        delay = BACKOFF_BASE**attempt
        try:
            await throttle()
            resp = await client.post(
                f"{settings.openrouter_base_url}/chat/completions",
                headers=_headers(),
                json={**body, "model": model},
            )
            limit = note_daily_limit(resp)
            if limit:
                raise limit
            minute_wait = minute_limit_wait(resp)
            if minute_wait is not None:
                last = RateLimitError(minute_wait)
                delay = minute_wait
            elif resp.status_code in RETRYABLE:
                last = RuntimeError(f"HTTP {resp.status_code}")
            elif resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code} {resp.text[:200]}")
            else:
                payload = resp.json()
                if payload.get("error"):
                    last = RuntimeError(str(payload["error"])[:200])
                else:
                    return _extract_content(payload)
        except (httpx.TimeoutException, httpx.TransportError, ValueError) as e:
            last = e
        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(delay)
    raise last or RuntimeError("failed")


async def chat_text(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.8,
    max_tokens: int | None = None,
) -> str:
    blocked = daily_limit_error()
    if blocked:
        raise blocked
    primary = model or await default_model()
    candidates = [primary]
    try:
        for m in await list_free_models():
            if m.id not in candidates:
                candidates.append(m.id)
    except Exception:
        if FALLBACK_MODEL not in candidates:
            candidates.append(FALLBACK_MODEL)
    candidates = candidates[: MAX_FALLBACK_MODELS + 1]

    body: dict[str, Any] = {"messages": messages, "temperature": temperature}
    if max_tokens:
        body["max_tokens"] = max_tokens

    errors: list[str] = []
    async with _make_client() as client:
        for mid in candidates:
            try:
                return await _call_model(client, mid, body)
            except DailyLimitError:
                raise
            except Exception as e:
                errors.append(f"{mid}: {e}")
    raise RuntimeError("All OpenRouter models failed. " + " | ".join(errors))


def extract_json(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("no JSON object found")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                obj = json.loads(cleaned[start : i + 1])
                if not isinstance(obj, dict):
                    raise ValueError("JSON is not an object")
                return obj
    raise ValueError("unterminated JSON object")


async def chat_json(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
) -> dict:
    text = await chat_text(messages, model, temperature, max_tokens)
    try:
        return extract_json(text)
    except ValueError:
        retry = messages + [
            {"role": "assistant", "content": text},
            {
                "role": "user",
                "content": "That was not valid JSON. Return valid JSON only: a single JSON object, no markdown fences, no commentary.",
            },
        ]
        text = await chat_text(retry, model, max(temperature - 0.3, 0.2), max_tokens)
        try:
            return extract_json(text)
        except ValueError as e:
            raise RuntimeError(f"Model did not return valid JSON: {e}")
