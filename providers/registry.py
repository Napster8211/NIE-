from concurrent.futures import ThreadPoolExecutor
from providers.provider_manager import ProviderManager
from providers.ollama_provider import OllamaProvider

def register_all_providers(manager: ProviderManager, executor: ThreadPoolExecutor):
    """
    Registers all AI providers and logical model aliases into the gateway.
    """
    # 1. Register OllamaFreeAPI Provider
    ollama_provider = OllamaProvider(executor=executor)
    manager.register(
        name="ollama",
        provider=ollama_provider,
        priority=1, # Primary Priority
        enabled=True,
        capabilities=["chat", "stream", "models"]
    )

    # 2. Register Logical Model Map Aliases
    manager.register_model_mapping(
        logical_name="coding-agent",
        provider_name="ollama",
        target_model="deepseek-r1:latest"
    )
    
    manager.register_model_mapping(
        logical_name="gemini-3.5-flash",
        provider_name="ollama", # Mapped to Ollama until Gemini provider key is added
        target_model="llama3.2:3b"
    )

    manager.register_model_mapping(
        logical_name="napstertec-default",
        provider_name="ollama",
        target_model="llama3.2:3b"
    )