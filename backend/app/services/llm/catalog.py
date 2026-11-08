import logging

from app.schemas import ModelInfo, ModelsResponse, ProviderInfo
from app.services.llm.factory import UnknownProviderError, get_provider, requested_names

log = logging.getLogger(__name__)


async def catalog() -> ModelsResponse:
    """Every configured provider's models, grouped by provider, plus the default selection."""
    providers: list[ProviderInfo] = []
    models: list[ModelInfo] = []
    default: str | None = None
    for name in requested_names():
        try:
            provider = get_provider(name)
        except UnknownProviderError:
            continue
        configured = provider.is_configured()
        available = configured and provider.is_available()
        note = "" if configured else provider.setup_hint
        listed: list[ModelInfo] = []
        if configured:
            if not available:
                note = "free daily quota used up, other providers answer instead"
            try:
                listed = await provider.list_models()
            except Exception as exc:
                log.warning("could not list models for %s: %s", name, exc)
                note = note or "could not load the model list"
        providers.append(
            ProviderInfo(id=provider.name, label=provider.label or provider.name,
                         configured=configured, available=available, note=note)
        )
        models.extend(listed)
        if default is None and available and listed:
            wanted = await provider.default_model()
            default = wanted if any(m.id == wanted for m in listed) else listed[0].id
    if default is None and models:
        default = models[0].id
    return ModelsResponse(models=models, default=default or "", providers=providers)
