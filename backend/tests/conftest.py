import pytest

from app.config import settings
from app.services import openrouter


@pytest.fixture(autouse=True)
def fresh_openrouter_limits(monkeypatch):
    # A real OLLAMA_API_KEY in .env must never make tests call the network.
    monkeypatch.setattr(settings, "ollama_api_key", "")
    monkeypatch.setattr(settings, "llm_providers", "openrouter,ollama")
    openrouter._window.clear()
    openrouter._daily_block = None
    yield
    openrouter._window.clear()
    openrouter._daily_block = None
