from abc import ABC, abstractmethod


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
