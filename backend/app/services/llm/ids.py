from app.services.llm.factory import known_providers


def encode_model(provider: str, model: str) -> str:
    """"ollama" + "gpt-oss:120b" -> "ollama:gpt-oss:120b" so the owner survives a round trip."""
    return f"{provider}:{model}"


def split_model(model: str | None) -> tuple[str | None, str | None]:
    """Returns (provider, raw model). Ids without a known provider prefix are legacy raw ids."""
    if model:
        head, sep, rest = model.partition(":")
        if sep and rest and head in known_providers():
            return head, rest
    return None, model
