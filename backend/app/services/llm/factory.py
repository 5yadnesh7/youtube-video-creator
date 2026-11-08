import logging
from typing import Callable

from app.config import settings
from app.services.llm.base import LLMProvider

log = logging.getLogger(__name__)

_REGISTRY: dict[str, Callable[[], LLMProvider]] = {}
_INSTANCES: dict[str, LLMProvider] = {}


class UnknownProviderError(KeyError):
    pass


def register_provider(name: str, factory: Callable[[], LLMProvider]) -> None:
    """Adds a provider. To support a new service: subclass LLMProvider, then register it here."""
    key = name.strip().lower()
    _REGISTRY[key] = factory
    _INSTANCES.pop(key, None)


def known_providers() -> list[str]:
    return sorted(_REGISTRY)


def get_provider(name: str) -> LLMProvider:
    key = name.strip().lower()
    if key not in _REGISTRY:
        raise UnknownProviderError(f"Unknown text provider '{name}'. Known: {', '.join(known_providers())}")
    if key not in _INSTANCES:
        _INSTANCES[key] = _REGISTRY[key]()
    return _INSTANCES[key]


def requested_names() -> list[str]:
    return [n.strip().lower() for n in settings.llm_providers.split(",") if n.strip()]


def provider_chain() -> list[LLMProvider]:
    """Providers from LLM_PROVIDERS, in order, skipping unknown names and ones without credentials."""
    chain: list[LLMProvider] = []
    for name in requested_names():
        try:
            provider = get_provider(name)
        except UnknownProviderError as exc:
            log.warning("%s", exc)
            continue
        if provider.is_configured() and provider not in chain:
            chain.append(provider)
    return chain
