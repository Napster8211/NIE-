import asyncio
import os
from dotenv import load_dotenv

# Load env variables
load_dotenv()

from app.router.engine import CapabilityRouter
from app.engine.models import Capability
from app.providers.openrouter.provider import OpenRouterProvider

# ==========================================
# 1. Create a Fallback that matches your Interface
# ==========================================
class DummyFallbackProvider:
    @property
    def name(self):
        return "dummy_fallback"
        
    @property
    def capabilities(self):
        return []
        
    # The router checks health before streaming!
    async def check_health(self):
        return "HEALTHY" # Bypasses the ProviderHealth.UNHEALTHY check

    async def generate_stream(self, prompt, *args, **kwargs):
        yield "🚀 [DUMMY FALLBACK] OpenRouter crashed! "
        await asyncio.sleep(0.5)
        yield "Taking over the request... "
        await asyncio.sleep(0.5)
        yield "Here is your reversed string: "
        yield "'.gnirts desrever'"

async def run_failover_test():
    print("\n==========================================")
    print("🔥 SIMULATING OPENROUTER OUTAGE & FAILOVER")
    print("==========================================\n")

    router = CapabilityRouter()
    openrouter_instance = OpenRouterProvider()
    
    # We don't need to set the name, it's already a property!
    
    # Bypass health check to ensure we reach the stream execution
    async def mock_health(): 
        return "HEALTHY"
    openrouter_instance.check_health = mock_health

    # ==========================================
    # 2. Inject using the correct private variable
    # ==========================================
    router._providers = {
        "openrouter": openrouter_instance,
        "dummy_fallback": DummyFallbackProvider()
    }
    print("✅ Successfully injected OpenRouter and Dummy Fallback into self._providers.")

    # ==========================================
    # 3. Monkeypatch OpenRouter to crash (Class-level to avoid bound method issues)
    # ==========================================
    original_generate_stream = OpenRouterProvider.generate_stream

    async def simulated_failing_stream(self, *args, **kwargs):
        print("\n💥 [SIMULATION] OpenRouter experienced a critical 503 Service Outage!")
        raise Exception("ProviderException: Simulated 503")

    OpenRouterProvider.generate_stream = simulated_failing_stream

    try:
        print("\n📡 Sending prompt through router with OpenRouter set as primary choice...")
        
        # Notice we are passing preferences that match exactly what's in _providers
        stream_generator = router.route_skill_execution(
            prompt="Write a quick Python function to reverse a string.",
            required_capabilities=[], 
            preferences=["openrouter", "dummy_fallback"]
        )

        print("🔄 Waiting for stream... (Failover should trigger here)")
        print("\n--- Output Stream ---")
        
        async for chunk in stream_generator:
            print(chunk, end="", flush=True)
            
        print("\n\n------------------------------")
        print("✅ Failover Test Complete.")

    except Exception as e:
        import traceback
        print(f"\n❌ Test Failed: Uncaught exception - {e}")
        traceback.print_exc()
    finally:
        # Restore original method to clean up
        OpenRouterProvider.generate_stream = original_generate_stream

if __name__ == "__main__":
    asyncio.run(run_failover_test())