from abc import ABC, abstractmethod
from typing import List, AsyncGenerator, Any
from app.engine.models import Capability, ProviderHealth

class BaseProviderPlugin(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique key identifying the provider (e.g., 'ollama_free_api', 'gemini')."""
        pass

    @property
    @abstractmethod
    def supported_capabilities(self) -> List[Capability]:
        """List of capabilities supported by this provider."""
        pass

    @abstractmethod
    async def check_health(self) -> ProviderHealth:
        """Returns health status used for automated failover."""
        pass

    @abstractmethod
    async def generate_stream(self, prompt_or_request: Any, model: str) -> AsyncGenerator[str, None]:
        """Streams text chunks for a given prompt."""
        pass