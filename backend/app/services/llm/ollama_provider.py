import asyncio
import logging
import time

import httpx

from app.config import settings
from app.schemas import ModelInfo
from app.services.llm.base import LLMError, LLMProvider
from app.services.llm.ids import encode_model

log = logging.getLogger(__name__)

# Cloud model names change often; these are tried in order among the models the key can use.
PREFERRED_MODELS = ["gpt-oss:120b", "gemma4:31b", "glm-5.3-flash", "deepseek-v4.1-flash", "gpt-oss:20b"]
MODEL_LIST_TTL = 30 * 60
MAX_ATTEMPTS = 3
RETRYABLE = {429, 500, 502, 503, 504}

# The model list names every cloud model, but a free key only works with some of them
# (the rest answer 402), so each model is probed once with a one-token request.
PROBE_MESSAGES = [{"role": "user", "content": "hi"}]
PROBE_CONCURRENCY = 6
STABLE_TTL = 12 * 3600  # works / is not in the plan: unlikely to change soon
TRANSIENT_TTL = 5 * 60  # timeout, rate limit, server error: look again soon
PLAN_STATUSES = {402, 403, 404}


class OllamaProvider(LLMProvider):
    name = "ollama"
    label = "Ollama cloud"
    setup_hint = "Set OLLAMA_API_KEY in .env"
    no_models_hint = "none of Ollama's cloud models are included in this key's plan (see ollama.com/settings)"

    def __init__(self) -> None:
        self._models: tuple[float, list[dict]] = (0.0, [])
        # model -> (checked at, usable, seconds the answer stays valid)
        self._usable: dict[str, tuple[float, bool, float]] = {}
        self._probe_lock: asyncio.Lock | None = None

    def is_configured(self) -> bool:
        return bool(settings.ollama_api_key)

    def accepts_model(self, model: str | None) -> bool:
        return bool(model) and "/" not in model

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0))

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {settings.ollama_api_key}", "Content-Type": "application/json"}

    async def _text_models(self, client: httpx.AsyncClient) -> list[str]:
        fetched_at, models = self._models
        if not models or time.time() - fetched_at > MODEL_LIST_TTL:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags", headers=self._headers())
            resp.raise_for_status()
            models = resp.json().get("models", [])
            self._models = (time.time(), models)
        return [m["name"] for m in models if "completion" in (m.get("capabilities") or ["completion"])]

    async def _probe(self, client: httpx.AsyncClient, name: str) -> tuple[bool, float]:
        body = {"model": name, "messages": PROBE_MESSAGES, "stream": False, "options": {"num_predict": 1}}
        try:
            resp = await client.post(
                f"{settings.ollama_base_url}/api/chat", json=body, headers=self._headers(), timeout=60.0
            )
        except (httpx.TimeoutException, httpx.TransportError):
            return False, TRANSIENT_TTL
        if resp.status_code == 200:
            return True, STABLE_TTL
        if resp.status_code in PLAN_STATUSES:
            return False, STABLE_TTL
        return False, TRANSIENT_TTL

    def _fresh(self, name: str) -> bool:
        entry = self._usable.get(name)
        return entry is not None and time.time() - entry[0] <= entry[2]

    async def _usable_models(self, client: httpx.AsyncClient) -> list[str]:
        names = await self._text_models(client)
        if any(not self._fresh(n) for n in names):
            if self._probe_lock is None:
                self._probe_lock = asyncio.Lock()
            async with self._probe_lock:
                stale = [n for n in names if not self._fresh(n)]  # another caller may have just probed
                if stale:
                    gate = asyncio.Semaphore(PROBE_CONCURRENCY)

                    async def check(n: str) -> None:
                        async with gate:
                            usable, ttl = await self._probe(client, n)
                        self._usable[n] = (time.time(), usable, ttl)

                    await asyncio.gather(*(check(n) for n in stale))
                    ok = [n for n in names if self._usable[n][1]]
                    log.info("Ollama: %d of %d cloud text models work with this key: %s", len(ok), len(names), ", ".join(ok))
        return [n for n in names if self._usable[n][1]]

    async def _pick_model(self, client: httpx.AsyncClient) -> str:
        if settings.ollama_model:
            return settings.ollama_model
        try:
            names = await self._usable_models(client)
        except Exception as exc:
            log.warning("could not list Ollama models, using %s: %s", PREFERRED_MODELS[0], exc)
            return PREFERRED_MODELS[0]
        for preferred in PREFERRED_MODELS:
            if preferred in names:
                return preferred
        return names[0] if names else PREFERRED_MODELS[0]

    async def warm(self) -> None:
        async with self._client() as client:
            await self._usable_models(client)

    async def list_models(self) -> list[ModelInfo]:
        async with self._client() as client:
            names = await self._usable_models(client)
        rank = {name: i for i, name in enumerate(PREFERRED_MODELS)}
        names.sort(key=lambda n: (rank.get(n, len(rank)), n))
        return [
            ModelInfo(id=encode_model(self.name, n), name=n, provider=self.name, provider_label=self.label)
            for n in names
        ]

    async def default_model(self) -> str | None:
        async with self._client() as client:
            return encode_model(self.name, await self._pick_model(client))

    async def chat_text(self, messages, model, temperature, max_tokens) -> str:
        async with self._client() as client:
            chosen = model or await self._pick_model(client)
            options: dict = {"temperature": temperature}
            if max_tokens:
                options["num_predict"] = max_tokens
            body = {"model": chosen, "messages": messages, "stream": False, "options": options}
            last: Exception | None = None
            for attempt in range(MAX_ATTEMPTS):
                try:
                    resp = await client.post(
                        f"{settings.ollama_base_url}/api/chat", json=body, headers=self._headers()
                    )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last = exc
                else:
                    if resp.status_code == 401:
                        raise LLMError("Ollama rejected OLLAMA_API_KEY (check the key in .env)")
                    if resp.status_code in (402, 403):
                        self._usable[chosen] = (time.time(), False, STABLE_TTL)
                        raise LLMError(
                            f"Ollama model {chosen} is not included in this key's plan "
                            "(add usage credits at ollama.com/settings or pick another model)"
                        )
                    if resp.status_code in RETRYABLE:
                        last = RuntimeError(f"HTTP {resp.status_code}")
                    elif resp.status_code >= 400:
                        raise RuntimeError(f"HTTP {resp.status_code} {resp.text[:200]}")
                    else:
                        message = (resp.json() or {}).get("message") or {}
                        content = (message.get("content") or "").strip()
                        if content:
                            return content
                        reason = "only reasoning text" if message.get("thinking") else "an empty reply"
                        raise RuntimeError(f"model {chosen} returned {reason}")
                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(1.5**attempt)
            raise RuntimeError(f"request failed: {last}")
