import pytest

from app.services import openrouter


@pytest.fixture(autouse=True)
def fresh_openrouter_limits():
    openrouter._window.clear()
    openrouter._daily_block = None
    yield
    openrouter._window.clear()
    openrouter._daily_block = None
