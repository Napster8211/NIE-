import asyncio
import hashlib
import json
from typing import Any, Dict, Optional

class SearchCache:
    """Enterprise memory cache for Web Intelligence queries."""
    
    def __init__(self, ttl_seconds: int = 3600):
        self.ttl = ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def _generate_key(self, provider: str, query: str, **kwargs) -> str:
        """Creates a deterministic hash for complex search parameters."""
        payload = {"provider": provider, "query": query, **kwargs}
        payload_str = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(payload_str.encode()).hexdigest()

    async def get(self, provider: str, query: str, **kwargs) -> Optional[Any]:
        key = self._generate_key(provider, query, **kwargs)
        async with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                if asyncio.get_event_loop().time() < entry['expires_at']:
                    return entry['data']
                else:
                    del self._cache[key]
        return None

    async def set(self, provider: str, query: str, data: Any, **kwargs):
        key = self._generate_key(provider, query, **kwargs)
        async with self._lock:
            self._cache[key] = {
                'data': data,
                'expires_at': asyncio.get_event_loop().time() + self.ttl
            }