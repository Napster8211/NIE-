from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Capability(str, Enum):
    CHAT = "chat"
    CODING = "coding"
    SYSTEM_INSPECTION = "system_inspection"
    VISION = "vision"
    RESEARCH = "research"
    ROUTING = "routing"
    ANALYTICS = "analytics"


class ProviderHealth(str, Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class IntentType(str, Enum):
    CHAT = "chat"
    CODING = "coding"
    SYSTEM_INSPECTION = "system_inspection"
    VISION = "vision"
    RESEARCH = "research"
    OTHER = "other"


@dataclass
class Intent:
    original_prompt: str
    primary_capability: Capability
    secondary_capabilities: List[Capability] = field(default_factory=list)
    confidence_score: float = 0.0
    context: Dict[str, Any] = field(default_factory=dict)
    intent_type: Optional[IntentType] = None

    def __post_init__(self) -> None:
        if self.intent_type is None:
            mapping = {
                Capability.CHAT: IntentType.CHAT,
                Capability.CODING: IntentType.CODING,
                Capability.SYSTEM_INSPECTION: IntentType.SYSTEM_INSPECTION,
                Capability.VISION: IntentType.VISION,
                Capability.RESEARCH: IntentType.RESEARCH,
            }
            self.intent_type = mapping.get(self.primary_capability, IntentType.OTHER)

    @property
    def all_capabilities(self) -> List[Capability]:
        """Primary capability first, followed by secondary capabilities."""
        return [self.primary_capability, *self.secondary_capabilities]