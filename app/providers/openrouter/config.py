import os
from pydantic_settings import BaseSettings
from pydantic import SecretStr, Field

class OpenRouterConfig(BaseSettings):
    """Configuration strictly for the OpenRouter integration."""
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(os.getenv("OPENROUTER_API_KEY", "")))
    base_url: str = Field(default="https://openrouter.ai/api/v1")
    site_url: str = Field(default="https://napstertec.com", description="For OpenRouter rankings")
    site_name: str = Field(default="NapsterTec Intelligence Engine")
    
    # Performance / Retry defaults
    timeout_seconds: float = 30.0
    max_retries: int = 3

    class Config:
        env_file = ".env"
        extra = "ignore"

# Singleton instance
openrouter_config = OpenRouterConfig()