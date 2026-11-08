from app.config import settings
from app.services import openrouter
from app.services.llm.base import LLMProvider


class OpenRouterProvider(LLMProvider):
    name = "openrouter"
    label = "OpenRouter"

    def is_configured(self) -> bool:
        return bool(settings.openrouter_api_key)

    def accepts_model(self, model: str | None) -> bool:
        return bool(model) and "/" in model

    async def chat_text(self, messages, model, temperature, max_tokens) -> str:
        return await openrouter.chat_text(
            messages, model=model, temperature=temperature, max_tokens=max_tokens
        )
