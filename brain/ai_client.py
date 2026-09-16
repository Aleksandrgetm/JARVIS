"""Provider-neutral text completion boundary; no execution capabilities."""

from abc import ABC, abstractmethod
from typing import Any, Dict


class AIUnavailable(RuntimeError):
    """Sanitized provider/configuration failure."""


class AIClient(ABC):
    supports_streaming = False
    def close(self) -> None:
        """Release provider resources, if any."""

    def complete_stream(self, *, on_text, **kwargs) -> str:
        """Providers without streaming retain the single-request completion path."""
        return self.complete(**kwargs)

    @abstractmethod
    def complete(self, *, system_prompt: str, user_text: str, schema: Dict[str, Any]) -> str:
        """Return JSON text only; never execute an action."""


class DisabledAIClient(AIClient):
    def complete(self, **kwargs) -> str:
        raise AIUnavailable("AI is disabled or not configured.")
