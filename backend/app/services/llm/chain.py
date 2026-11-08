import logging

from app.services.llm.base import LLMError
from app.services.llm.factory import provider_chain
from app.services.llm.ids import split_model
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
    owner, raw = split_model(model)
    if owner:
        # The user picked a model: its provider answers first, the others stay as fallback.
        providers = sorted(providers, key=lambda p: p.name != owner)
    failures: list[str] = []
    for provider in providers:
        if owner:
            use_model = raw if provider.name == owner else None
        else:
            use_model = raw if provider.accepts_model(raw) else None
        try:
            text = await provider.chat_text(messages, use_model, temperature, max_tokens)
            log.info("text answered by %s (%s)", provider.name, use_model or "its default model")
            return text
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
