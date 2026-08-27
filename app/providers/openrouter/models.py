from enum import Enum
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Set

from app.engine.models import Capability


class ModelTier(str, Enum):
    FREE = "free"
    UTILITY = "utility"
    SPECIALIST = "specialist"
    GENERAL = "general"
    PREMIUM = "premium"


class ModelSpec(BaseModel):
    model_id: str
    display_name: str
    context_window: int
    provider: str
    capabilities: Set[Capability]
    supports_streaming: bool = True
    supports_vision: bool = False
    supports_tools: bool = False
    supports_reasoning: bool = False
    priority: int = Field(default=1, description="Tie-break priority inside the same routing profile.")
    enabled: bool = True

    # Cost-governance metadata. Prices are USD per 1M tokens and should be
    # periodically revalidated against OpenRouter.
    tier: ModelTier = ModelTier.UTILITY
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0


# Alias for backward compatibility.
OpenRouterModelSpec = ModelSpec


# Centralized OpenRouter model registry.
#
# Design:
# - FREE is the first choice for low-cost/background traffic.
# - Flash-Lite is the reliable ultra-cheap fallback.
# - Qwen Coder is used for engineering work.
# - DeepSeek is the balanced reasoning model.
# - GPT-5 Mini is reserved for performance/executive work.
# - Gemini Flash is reserved for large-context / multimodal work.
MODEL_REGISTRY: Dict[str, ModelSpec] = {
    "openrouter-free": ModelSpec(
        model_id="openrouter/free",
        display_name="OpenRouter Free Models Router",
        context_window=200_000,
        provider="OpenRouter",
        capabilities={
            Capability.CHAT,
            Capability.RESEARCH,
            Capability.ROUTING,
            Capability.ANALYTICS,
            Capability.VISION,
        },
        supports_streaming=True,
        supports_vision=True,
        supports_tools=True,
        priority=100,
        tier=ModelTier.FREE,
        input_cost_per_million=0.0,
        output_cost_per_million=0.0,
    ),
    "gemini-flash-lite": ModelSpec(
        model_id="google/gemini-2.5-flash-lite",
        display_name="Gemini 2.5 Flash-Lite",
        context_window=1_048_576,
        provider="OpenRouter",
        capabilities={
            Capability.CHAT,
            Capability.RESEARCH,
            Capability.ROUTING,
            Capability.ANALYTICS,
            Capability.VISION,
        },
        supports_streaming=True,
        supports_vision=True,
        supports_tools=True,
        priority=90,
        tier=ModelTier.UTILITY,
        input_cost_per_million=0.10,
        output_cost_per_million=0.40,
    ),
    "qwen-coder-30b": ModelSpec(
        model_id="qwen/qwen3-coder-30b-a3b-instruct",
        display_name="Qwen3 Coder 30B A3B Instruct",
        context_window=262_144,
        provider="OpenRouter",
        capabilities={
            Capability.CODING,
            Capability.SYSTEM_INSPECTION,
        },
        supports_streaming=True,
        supports_tools=True,
        priority=95,
        tier=ModelTier.UTILITY,
        input_cost_per_million=0.07,
        output_cost_per_million=0.27,
    ),
    "qwen-coder-flash": ModelSpec(
        model_id="qwen/qwen3-coder-flash",
        display_name="Qwen3 Coder Flash",
        context_window=1_000_000,
        provider="OpenRouter",
        capabilities={
            Capability.CODING,
            Capability.SYSTEM_INSPECTION,
            Capability.RESEARCH,
        },
        supports_streaming=True,
        supports_tools=True,
        priority=90,
        tier=ModelTier.SPECIALIST,
        input_cost_per_million=0.195,
        output_cost_per_million=0.975,
    ),
    "deepseek-terminus": ModelSpec(
        model_id="deepseek/deepseek-v3.1-terminus",
        display_name="DeepSeek V3.1 Terminus",
        context_window=163_840,
        provider="OpenRouter",
        capabilities={
            Capability.CHAT,
            Capability.CODING,
            Capability.SYSTEM_INSPECTION,
            Capability.RESEARCH,
            Capability.ROUTING,
            Capability.ANALYTICS,
        },
        supports_streaming=True,
        supports_tools=True,
        supports_reasoning=True,
        priority=85,
        tier=ModelTier.GENERAL,
        input_cost_per_million=0.27,
        output_cost_per_million=1.00,
    ),
    "gpt-5-mini": ModelSpec(
        model_id="openai/gpt-5-mini",
        display_name="GPT-5 Mini",
        context_window=400_000,
        provider="OpenRouter",
        capabilities={
            Capability.CHAT,
            Capability.CODING,
            Capability.SYSTEM_INSPECTION,
            Capability.RESEARCH,
            Capability.ROUTING,
            Capability.ANALYTICS,
            Capability.VISION,
        },
        supports_streaming=True,
        supports_vision=True,
        supports_tools=True,
        supports_reasoning=True,
        priority=80,
        tier=ModelTier.PREMIUM,
        input_cost_per_million=0.25,
        output_cost_per_million=2.00,
    ),
    "gemini-flash": ModelSpec(
        model_id="google/gemini-2.5-flash",
        display_name="Gemini 2.5 Flash",
        context_window=1_048_576,
        provider="OpenRouter",
        capabilities={
            Capability.CHAT,
            Capability.CODING,
            Capability.RESEARCH,
            Capability.ROUTING,
            Capability.ANALYTICS,
            Capability.VISION,
        },
        supports_streaming=True,
        supports_vision=True,
        supports_tools=True,
        supports_reasoning=True,
        priority=75,
        tier=ModelTier.PREMIUM,
        input_cost_per_million=0.30,
        output_cost_per_million=2.50,
    ),
}


def _capability(value: str | Capability) -> Capability:
    try:
        return Capability(value) if isinstance(value, str) else value
    except Exception as exc:
        raise ValueError(f"Unsupported capability: {value}") from exc


def _eligible_models(capability: str | Capability) -> List[ModelSpec]:
    cap = _capability(capability)
    return [
        model
        for model in MODEL_REGISTRY.values()
        if model.enabled and cap in model.capabilities
    ]


def get_model_chain_for_capability(
    capability: str | Capability,
    *,
    cost_preference: str = "balanced",
    reasoning_level: str = "medium",
    require_vision: bool = False,
) -> List[str]:
    """
    Return an ordered OpenRouter model fallback chain.

    cost_preference:
      - low:         free first, then ultra-cheap reliable models.
      - balanced:    cost-aware paid model first, with free/cheap fallbacks.
      - performance: strongest practical model first.

    The returned list is safe to pass to OpenRouter's `models` fallback field.
    """
    cap = _capability(capability)
    preference = (cost_preference or "balanced").strip().casefold()
    reasoning = (reasoning_level or "medium").strip().casefold()

    candidates = _eligible_models(cap)
    if require_vision:
        candidates = [model for model in candidates if model.supports_vision]

    if not candidates:
        raise ValueError(f"No enabled models registered for capability: {cap}")

    by_id = {model.model_id: model for model in candidates}

    # Explicit capability-aware chains keep routing predictable and cheap.
    if cap in {Capability.CODING, Capability.SYSTEM_INSPECTION}:
        if preference == "low":
            preferred = [
                "qwen/qwen3-coder-30b-a3b-instruct",
                "openrouter/free",
                "qwen/qwen3-coder-flash",
            ]
        elif preference == "performance" or reasoning in {"high", "deep"}:
            preferred = [
                "qwen/qwen3-coder-flash",
                "deepseek/deepseek-v3.1-terminus",
                "openai/gpt-5-mini",
                "qwen/qwen3-coder-30b-a3b-instruct",
            ]
        else:
            preferred = [
                "qwen/qwen3-coder-30b-a3b-instruct",
                "qwen/qwen3-coder-flash",
                "deepseek/deepseek-v3.1-terminus",
                "openrouter/free",
            ]
    elif cap == Capability.VISION or require_vision:
        if preference == "low":
            preferred = [
                "openrouter/free",
                "google/gemini-2.5-flash-lite",
                "google/gemini-2.5-flash",
            ]
        elif preference == "performance":
            preferred = [
                "google/gemini-2.5-flash",
                "openai/gpt-5-mini",
                "google/gemini-2.5-flash-lite",
            ]
        else:
            preferred = [
                "google/gemini-2.5-flash-lite",
                "google/gemini-2.5-flash",
                "openrouter/free",
            ]
    else:
        if preference == "low":
            preferred = [
                "openrouter/free",
                "google/gemini-2.5-flash-lite",
                "deepseek/deepseek-v3.1-terminus",
            ]
        elif preference == "performance":
            preferred = [
                "openai/gpt-5-mini",
                "deepseek/deepseek-v3.1-terminus",
                "google/gemini-2.5-flash",
                "google/gemini-2.5-flash-lite",
            ]
        elif reasoning in {"high", "deep"}:
            preferred = [
                "deepseek/deepseek-v3.1-terminus",
                "openai/gpt-5-mini",
                "google/gemini-2.5-flash-lite",
                "openrouter/free",
            ]
        else:
            preferred = [
                "google/gemini-2.5-flash-lite",
                "deepseek/deepseek-v3.1-terminus",
                "openrouter/free",
                "openai/gpt-5-mini",
            ]

    chain = [model_id for model_id in preferred if model_id in by_id]

    # Preserve any future eligible registry additions as final fallbacks.
    remaining = sorted(
        (model for model in candidates if model.model_id not in chain),
        key=lambda model: (
            model.input_cost_per_million + model.output_cost_per_million,
            -model.priority,
        ),
    )
    chain.extend(model.model_id for model in remaining)
    return chain


def get_model_for_capability(
    capability: str | Capability,
    provider: Optional[str] = None,
    exclude_provider: Optional[str] = None,
    *,
    cost_preference: str = "balanced",
    reasoning_level: str = "medium",
) -> str:
    """Backward-compatible single-model selector."""
    chain = get_model_chain_for_capability(
        capability,
        cost_preference=cost_preference,
        reasoning_level=reasoning_level,
    )

    if provider or exclude_provider:
        provider_key = provider.casefold() if provider else None
        excluded_key = exclude_provider.casefold() if exclude_provider else None

        filtered = []
        for model_id in chain:
            spec = next(
                (m for m in MODEL_REGISTRY.values() if m.model_id == model_id),
                None,
            )
            if spec is None:
                continue
            if provider_key and spec.provider.casefold() != provider_key:
                continue
            if excluded_key and spec.provider.casefold() == excluded_key:
                continue
            filtered.append(model_id)

        if not filtered:
            raise ValueError(
                f"No enabled models registered for capability {capability} "
                f"with provider filters."
            )
        return filtered[0]

    return chain[0]
