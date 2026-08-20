# app/providers/health.py
import time
import logging
from enum import Enum
from typing import Dict, Callable, Any, List
from app.providers.base import BaseProviderPlugin

logger = logging.getLogger("nie.health")

class CircuitState(Enum):
    CLOSED = "CLOSED"       # Normal operations (Healthy)
    OPEN = "OPEN"           # Failing, requests blocked (Unhealthy)
    HALF_OPEN = "HALF_OPEN" # Cooldown finished, testing recovery

class ProviderOfflineException(Exception):
    """Raised when a provider is in an OPEN state and cannot accept requests."""
    pass

class CircuitBreaker:
    def __init__(self, provider_name: str, failure_threshold: int = 3, recovery_timeout: int = 60):
        self.provider_name = provider_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        
        # Enterprise Analytics metrics
        self.total_requests = 0
        self.successful_requests = 0

    @property
    def is_healthy(self) -> bool:
        """Evaluates if the provider is currently ready to receive requests."""
        if self.state == CircuitState.CLOSED:
            return True
            
        if self.state == CircuitState.OPEN:
            # Check if the 60-second cooldown period has expired
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info(f"Provider [{self.provider_name}] cooldown ended. Transitioning to HALF_OPEN for recovery test.")
                return True
            return False
            
        # HALF_OPEN allows a single test request through to check recovery
        return True

    def record_success(self):
        """Records a successful request, resetting failures and closing the circuit."""
        self.total_requests += 1
        self.successful_requests += 1
        
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            logger.info(f"Provider [{self.provider_name}] successfully recovered and is now ONLINE.")
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0  # Reset on any success

    def record_failure(self):
        """Records a failure. Trips the circuit if the threshold is reached."""
        self.total_requests += 1
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitState.HALF_OPEN:
            # The recovery test failed. Immediately trip the breaker again.
            self.state = CircuitState.OPEN
            logger.warning(f"Provider [{self.provider_name}] failed recovery test. Reverting to OFFLINE.")
        elif self.state == CircuitState.CLOSED and self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.error(f"Provider [{self.provider_name}] circuit tripped! Transitioning to OFFLINE for {self.recovery_timeout}s.")


class HealthManager:
    def __init__(self):
        # Maps provider names to their dedicated circuit breakers
        self._breakers: Dict[str, CircuitBreaker] = {}

    def register_provider(self, provider_name: str, failure_threshold: int = 3, recovery_timeout: int = 60):
        if provider_name not in self._breakers:
            self._breakers[provider_name] = CircuitBreaker(
                provider_name, failure_threshold, recovery_timeout
            )
            logger.info(f"Health tracking established for [{provider_name}]")

    def get_healthy_providers(self, available_providers: List[BaseProviderPlugin]) -> List[BaseProviderPlugin]:
        """
        Filters the available providers, returning ONLY those that are ONLINE.
        This is the method the Router uses to ignore dead nodes automatically.
        """
        healthy = []
        for provider in available_providers:
            breaker = self._breakers.get(provider.provider_name)
            
            # Auto-register if we haven't seen this provider before
            if not breaker:
                self.register_provider(provider.provider_name)
                breaker = self._breakers[provider.provider_name]
                
            if breaker.is_healthy:
                healthy.append(provider)
                
        return healthy

    async def execute_with_circuit_breaker(self, provider_name: str, func: Callable, *args, **kwargs) -> Any:
        """
        A protective wrapper for asynchronous provider calls. 
        It traps connection errors and automatically adjusts the provider's health score.
        """
        breaker = self._breakers.get(provider_name)
        if not breaker:
            self.register_provider(provider_name)
            breaker = self._breakers[provider_name]

        if not breaker.is_healthy:
            raise ProviderOfflineException(f"Provider '{provider_name}' is temporarily offline.")

        try:
            # Attempt the external API call (e.g., to Ollama or Gemini)
            result = await func(*args, **kwargs)
            breaker.record_success()
            return result
        except Exception as e:
            breaker.record_failure()
            logger.error(f"Provider [{provider_name}] generated an execution error: {str(e)}")
            raise

# Global singleton to be imported by the Intelligent Router
health_manager = HealthManager()