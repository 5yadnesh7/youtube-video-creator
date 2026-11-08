from app.services.llm.base import LLMError, LLMProvider
from app.services.llm.chain import chat_json, chat_text
from app.services.llm.factory import (
    UnknownProviderError,
    get_provider,
    known_providers,
    provider_chain,
    register_provider,
    requested_names,
)
from app.services.llm.ollama_provider import OllamaProvider
from app.services.llm.openrouter_provider import OpenRouterProvider

register_provider("openrouter", OpenRouterProvider)
register_provider("ollama", OllamaProvider)

__all__ = [
    "LLMError",
    "LLMProvider",
    "UnknownProviderError",
    "chat_json",
    "chat_text",
    "get_provider",
    "known_providers",
    "provider_chain",
    "register_provider",
    "requested_names",
]
