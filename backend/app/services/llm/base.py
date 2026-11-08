from abc import ABC, abstractmethod

from app.schemas import ModelInfo


class LLMError(RuntimeError):
    """No configured text provider could answer."""


class LLMProvider(ABC):
    name: str = ""
    label: str = ""

    @abstractmethod
    def is_configured(self) -> bool:
        """True when the credentials this provider needs are present."""

    @abstractmethod
    async def chat_text(
        self,
        messages: list[dict],
        model: str | None,
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        """Returns the reply text; raises on any failure so the chain can try the next provider."""

    def accepts_model(self, model: str | None) -> bool:
        """Whether an explicitly requested model id belongs to this provider."""
        return False

    setup_hint: str = ""
    no_models_hint: str = "no models available"

    async def warm(self) -> None:
        """Optional: do slow one-off preparation (e.g. checking which models work) ahead of time."""

    def is_available(self) -> bool:
        """False while the provider is configured but temporarily unusable (e.g. quota used up)."""
        return True

    async def list_models(self) -> list[ModelInfo]:
        """Models offered in the picker; ids must be encoded with ids.encode_model."""
        return []

    async def default_model(self) -> str | None:
        """Encoded id of the model this provider uses when the user picks none."""
        return None
