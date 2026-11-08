import asyncio
import logging
import time

import httpx

from app.config import settings
from app.schemas import ModelInfo
from app.services.llm.base import LLMError, LLMProvider
from app.services.llm.ids import encode_model

log = logging.getLogger(__name__)

# Cloud model names change often; these are tried in order against the live list.
PREFERRED_MODELS = ["gpt-oss:120b", "gemma4:31b", "glm-5.3-flash", "deepseek-v4.1-flash", "gpt-oss:20b"]
MODEL_LIST_TTL = 30 * 60
MAX_ATTEMPTS = 3
RETRYABLE = {429, 500, 502, 503, 504}


class OllamaProvider(LLMProvider):
    name = "ollama"
    label = "Ollama cloud"
    setup_hint = "Set OLLAMA_API_KEY in .env"

    def __init__(self) -> None:
        self._models: tuple[float, list[dict]] = (0.0, [])

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

    async def _pick_model(self, client: httpx.AsyncClient) -> str:
        if settings.ollama_model:
            return settings.ollama_model
        try:
            names = await self._text_models(client)
        except Exception as exc:
            log.warning("could not list Ollama models, using %s: %s", PREFERRED_MODELS[0], exc)
            return PREFERRED_MODELS[0]
        for preferred in PREFERRED_MODELS:
            if preferred in names:
                return preferred
        return names[0] if names else PREFERRED_MODELS[0]

    async def list_models(self) -> list[ModelInfo]:
        async with self._client() as client:
            names = await self._text_models(client)
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
                    if resp.status_code in (401, 403):
                        raise LLMError("Ollama rejected OLLAMA_API_KEY (check the key in .env)")
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
