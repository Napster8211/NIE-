from dataclasses import dataclass
from typing import Optional


@dataclass
class ProviderTraceContext:
    provider_name: str = "Unknown"
    model_id: str = "Unknown"
    capability_used: str = "N/A"
    skill_used: str = "N/A"
    conversation_id: str = "N/A"
    request_id: str = "N/A"
    streaming_enabled: bool = False
    fallback_used: bool = False
    fallback_provider: Optional[str] = None
    fallback_model: Optional[str] = None
    retry_count: int = 0
    provider_health_status: str = "Healthy"


class ProviderMetadataProvider:
    """Common interface for providers/routers to supply metadata to the profiler."""
    
    @staticmethod
    def extract_metadata(source_dict: dict) -> ProviderTraceContext:
        return ProviderTraceContext(
            provider_name=source_dict.get("provider_name", "Unknown"),
            model_id=source_dict.get("model_id", "Unknown"),
            capability_used=source_dict.get("capability_used", "N/A"),
            skill_used=source_dict.get("skill_used", "N/A"),
            conversation_id=source_dict.get("conversation_id", "N/A"),
            request_id=source_dict.get("request_id", "N/A"),
            streaming_enabled=source_dict.get("streaming_enabled", False),
            fallback_used=source_dict.get("fallback_used", False),
            fallback_provider=source_dict.get("fallback_provider"),
            fallback_model=source_dict.get("fallback_model"),
            retry_count=source_dict.get("retry_count", 0),
            provider_health_status=source_dict.get("provider_health_status", "Healthy")
        )