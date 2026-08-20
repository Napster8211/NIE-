import logging
from typing import Dict, List, Optional

from app.providers.base import BaseProviderPlugin
from app.providers.ollama_provider import OllamaFreeProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.openrouter.provider import OpenRouterProvider
from app.providers.groq_provider import GroqProvider
from app.providers.kimi_provider import KimiProvider
from app.providers.cerebras_provider import CerebrasProvider


logger = logging.getLogger(__name__)


class ProviderRegistry:
    def __init__(self):
        self.providers: Dict[str, BaseProviderPlugin] = {}
        self._register_defaults()

    def _register_defaults(self):
        # Register all available providers securely. Kimi may be registered
        # without an API key; its health check will report unavailable until
        # KIMI_API_KEY or MOONSHOT_API_KEY is configured.
        self.register_provider(OllamaFreeProvider())
        self.register_provider(GeminiProvider())
        self.register_provider(OpenRouterProvider())
        self.register_provider(GroqProvider())
        self.register_provider(KimiProvider())
        
        # Sprint 3: Register Cerebras Provider
        self.register_provider(CerebrasProvider())

    def register_provider(self, provider: BaseProviderPlugin):
        self.providers[provider.name] = provider
        logger.info(
            "[ProviderRegistry] Registered provider plugin: %s",
            provider.name,
        )

    def get_provider(self, name: str) -> Optional[BaseProviderPlugin]:
        return self.providers.get(name)

    def get_all_providers(self) -> List[BaseProviderPlugin]:
        return list(self.providers.values())


# Global singleton
provider_registry = ProviderRegistry()