from pydantic import BaseModel, Field
from typing import Set, Dict

from app.engine.models import Capability


class ModelSpec(BaseModel):
    model_id: str
    display_name: str
    context_window: int
    provider: str
    capabilities: Set[Capability]
    supports_streaming: bool = True
    supports_vision: bool = False
    priority: int = Field(default=1, description="Higher priority models selected first")
    enabled: bool = True


# Alias for backward compatibility
OpenRouterModelSpec = ModelSpec


# Centralized model registry
# NOTE:
# This registry intentionally uses only capabilities that exist in the shared
# app.engine.models.Capability enum right now.
MODEL_REGISTRY: Dict[str, ModelSpec] = {
    "kimi-k2.5": ModelSpec(
        model_id="kimi-k2.5",
        display_name="Kimi K2.5 (Moonshot)",
        context_window=262144,
        provider="Kimi",
        capabilities={
            Capability.CHAT,
            Capability.CODING,
            Capability.SYSTEM_INSPECTION,
            Capability.RESEARCH,
            Capability.ROUTING,
            Capability.ANALYTICS,
        },
        supports_streaming=True,
        # K2.5 can be multimodal, but NIE's first Kimi provider sprint sends
        # text chat-completion messages only. Enable this after adding the
        # provider's image-input normalization and testing the vision path.
        supports_vision=False,
        priority=30,
    ),
    "groq-chat": ModelSpec(
        model_id="llama-3.1-8b-instant",
        display_name="Llama 3.1 8B (Groq Chat)",
        context_window=131072,
        provider="Groq",
        capabilities={Capability.CHAT},
        priority=20,
    ),
    "openrouter-chat": ModelSpec(
        model_id="openrouter/free",
        display_name="OpenRouter Free Chat",
        context_window=8192,
        provider="OpenRouter",
        capabilities={Capability.CHAT},
        priority=10,
    ),
    "laguna": ModelSpec(
        model_id="poolside/laguna-xs-2.1:free",
        display_name="Laguna XS",
        context_window=8192,
        provider="Poolside",
        capabilities={Capability.CODING},
        priority=10,
    ),
    "gemma": ModelSpec(
        model_id="google/gemma-4-31b-it:free",
        display_name="Gemma 4 31B",
        context_window=8192,
        provider="Google",
        capabilities={Capability.VISION},
        supports_vision=True,
        priority=10,
    ),
}


def get_model_for_capability(
    capability: str | Capability,
    provider: str = None,
    exclude_provider: str = None,
) -> str:
    """Select the highest-priority enabled model matching the filters."""
    try:
        cap_enum = Capability(capability) if isinstance(capability, str) else capability
    except Exception as e:
        raise ValueError(f"Unsupported capability: {capability}") from e

    capable_models = [
        model
        for model in MODEL_REGISTRY.values()
        if cap_enum in model.capabilities and model.enabled
    ]

    if provider:
        provider_key = provider.casefold()
        capable_models = [
            model for model in capable_models
            if model.provider.casefold() == provider_key
        ]

    if exclude_provider:
        excluded_provider_key = exclude_provider.casefold()
        capable_models = [
            model for model in capable_models
            if model.provider.casefold() != excluded_provider_key
        ]

    if not capable_models:
        filters = []
        if provider:
            filters.append(f"provider={provider}")
        if exclude_provider:
            filters.append(f"exclude_provider={exclude_provider}")
        filter_text = f" ({', '.join(filters)})" if filters else ""
        raise ValueError(
            f"No enabled models registered for capability: {cap_enum}{filter_text}"
        )

    capable_models.sort(key=lambda model: model.priority, reverse=True)
    return capable_models[0].model_id
