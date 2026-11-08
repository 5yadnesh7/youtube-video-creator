from app.config import settings
from app.schemas import ModelInfo
from app.services import openrouter
from app.services.llm.base import LLMProvider
from app.services.llm.ids import encode_model


class OpenRouterProvider(LLMProvider):
    name = "openrouter"
    label = "OpenRouter"
    setup_hint = "Set OPENROUTER_API_KEY in .env"

    def is_configured(self) -> bool:
        return bool(settings.openrouter_api_key)

    def accepts_model(self, model: str | None) -> bool:
        return bool(model) and "/" in model

    async def chat_text(self, messages, model, temperature, max_tokens) -> str:
        return await openrouter.chat_text(
            messages, model=model, temperature=temperature, max_tokens=max_tokens
        )

    def is_available(self) -> bool:
        return openrouter.daily_limit_error() is None

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id=encode_model(self.name, m.id), name=m.name, context_length=m.context_length,
                provider=self.name, provider_label=self.label,
            )
            for m in await openrouter.list_free_models()
        ]

    async def default_model(self) -> str | None:
        return encode_model(self.name, await openrouter.default_model())
