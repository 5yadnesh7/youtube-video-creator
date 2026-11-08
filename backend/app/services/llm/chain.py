import logging

from app.services.llm.base import LLMError
from app.services.llm.factory import provider_chain
from app.services.openrouter import extract_json

log = logging.getLogger(__name__)


async def chat_text(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.8,
    max_tokens: int | None = None,
) -> str:
    """Asks the configured text providers in order; the first one that answers wins."""
    providers = provider_chain()
    if not providers:
        raise LLMError("No text provider is configured. Set OPENROUTER_API_KEY or OLLAMA_API_KEY in .env.")
    failures: list[str] = []
    for provider in providers:
        # An explicit model id only makes sense for the provider that owns it.
        use_model = model if provider.accepts_model(model) else None
        try:
            return await provider.chat_text(messages, use_model, temperature, max_tokens)
        except Exception as exc:  # CancelledError is not an Exception, so a stop request still propagates
            log.warning("text provider %s failed: %s", provider.name, exc)
            failures.append(f"{provider.label or provider.name}: {exc}")
    hint = ""
    if not any(p.name == "ollama" for p in providers):
        hint = " Tip: set OLLAMA_API_KEY in .env to enable Ollama cloud as an automatic fallback."
    raise LLMError("All text providers failed. " + " | ".join(failures) + hint)


async def chat_json(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
) -> dict:
    text = await chat_text(messages, model=model, temperature=temperature, max_tokens=max_tokens)
    try:
        return extract_json(text)
    except ValueError:
        nudged = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": "Your reply was not valid JSON. Return valid JSON only, no commentary."},
        ]
        text = await chat_text(nudged, model=model, temperature=temperature, max_tokens=max_tokens)
        try:
            return extract_json(text)
        except ValueError as exc:
            raise LLMError(f"The model did not return valid JSON: {exc}") from exc
