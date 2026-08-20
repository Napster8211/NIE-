import time
import asyncio
from typing import Dict, Any, Optional, List, Tuple
from providers.base_provider import BaseProvider
from utils.config import settings

class ProviderManager:
    def __init__(self):
        self.providers: Dict[str, Dict[str, Any]] = {}
        self.model_map: Dict[str, Dict[str, str]] = {}
        self._health_cache: Dict[str, Dict[str, Any]] = {}
        self._last_health_check: float = 0

    def register_model_mapping(self, logical_name: str, provider_name: str, target_model: str):
        """Map logical model aliases to target provider and provider model."""
        self.model_map[logical_name] = {
            "provider": provider_name,
            "model": target_model
        }

    def resolve_model(self, model_alias: str) -> Tuple[str, str]:
        """Resolve a model name to (provider_name, target_model). Returns defaults if unmapped."""
        if model_alias in self.model_map:
            mapping = self.model_map[model_alias]
            return mapping["provider"], mapping["model"]
        return settings.DEFAULT_PROVIDER, model_alias

    def register(self, name: str, provider: BaseProvider, priority: int = 10, enabled: bool = True, capabilities: List[str] = None):
        """Register a provider along with its metadata and metrics container."""
        self.providers[name] = {
            "provider": provider,
            "priority": priority,  # Lower number = Higher priority
            "enabled": enabled,
            "healthy": True,
            "latency": 0.0,
            "average_latency": 0.0,
            "request_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "last_error": None,
            "last_health_check": 0.0,
            "capabilities": capabilities or ["chat", "stream"]
        }

    async def initialize_all(self):
        """Initialize all registered and enabled providers."""
        for name, meta in self.providers.items():
            if meta["enabled"]:
                await meta["provider"].initialize()

    def get_ordered_providers(self) -> List[Tuple[str, BaseProvider]]:
        """Return enabled and healthy providers sorted by priority."""
        sorted_meta = sorted(
            [item for item in self.providers.items() if item[1]["enabled"] and item[1]["healthy"]],
            key=lambda x: x[1]["priority"]
        )
        return [(name, meta["provider"]) for name, meta in sorted_meta]

    def record_success(self, name: str, latency_ms: float):
        """Record successful execution metrics for a provider."""
        if name in self.providers:
            p = self.providers[name]
            p["request_count"] += 1
            p["success_count"] += 1
            p["latency"] = latency_ms
            # Moving average latency calculation
            p["average_latency"] = round((p["average_latency"] * 0.7) + (latency_ms * 0.3), 2)

    def record_failure(self, name: str, error_msg: str):
        """Record failed execution metrics for a provider."""
        if name in self.providers:
            p = self.providers[name]
            p["request_count"] += 1
            p["failure_count"] += 1
            p["last_error"] = error_msg

    async def execute_with_failover(self, messages: List[Dict[str, Any]], requested_model: str, stream: bool = False, **kwargs):
        """
        Attempts execution on target provider/model.
        If it fails, automatically iterates down the priority chain.
        """
        target_provider_name, mapped_model = self.resolve_model(requested_model)
        
        # Build execution list starting with target, followed by priority failovers
        ordered = self.get_ordered_providers()
        
        # Pull target provider to the front if enabled and healthy
        candidates = []
        if target_provider_name in self.providers and self.providers[target_provider_name]["healthy"]:
            candidates.append((target_provider_name, self.providers[target_provider_name]["provider"]))
            
        for name, instance in ordered:
            if name != target_provider_name:
                candidates.append((name, instance))

        if not candidates:
            raise RuntimeError("No healthy AI providers are currently available.")

        last_exception = None
        for name, provider_instance in candidates:
            start_time = time.time()
            try:
                if stream:
                    response = provider_instance.stream_chat(messages, mapped_model, **kwargs)
                else:
                    response = await provider_instance.chat(messages, mapped_model, **kwargs)
                
                latency = round((time.time() - start_time) * 1000, 2)
                self.record_success(name, latency)
                return response, name
            except Exception as e:
                err_msg = str(e)
                self.record_failure(name, err_msg)
                last_exception = e
                # Continue loop to attempt failover provider

        raise RuntimeError(f"All providers failed. Last error: {str(last_exception)}")

    async def check_health(self, force: bool = False) -> Dict[str, Any]:
        """Periodic health check across providers."""
        current_time = time.time()
        if not force and (current_time - self._last_health_check) < settings.HEALTH_CACHE_SECONDS:
            return self._health_cache

        results = {}
        for name, meta in self.providers.items():
            if not meta["enabled"]:
                continue

            start_time = time.time()
            try:
                status = await meta["provider"].health_check()
                latency = round((time.time() - start_time) * 1000, 2)
                is_healthy = (status.get("status") == "healthy")
            except Exception:
                latency = 0.0
                is_healthy = False

            meta["healthy"] = is_healthy
            meta["latency"] = latency
            meta["last_health_check"] = current_time

            results[name] = {
                "healthy": is_healthy,
                "latency_ms": latency,
                "priority": meta["priority"],
                "capabilities": meta["capabilities"]
            }

        self._health_cache = results
        self._last_health_check = current_time
        return results

    def get_metrics(self) -> Dict[str, Any]:
        """Expose operational telemetry for NapsterTec AI Admin Dashboard."""
        metrics = {}
        for name, meta in self.providers.items():
            metrics[name] = {
                "enabled": meta["enabled"],
                "healthy": meta["healthy"],
                "priority": meta["priority"],
                "request_count": meta["request_count"],
                "success_count": meta["success_count"],
                "failure_count": meta["failure_count"],
                "latency_ms": meta["latency"],
                "average_latency_ms": meta["average_latency"],
                "last_error": meta["last_error"]
            }
        return metrics