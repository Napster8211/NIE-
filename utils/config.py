import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # Core Server Settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False
    ENVIRONMENT: str = "production"
    WORKERS: int = 4
    
    # AI Gateway Defaults
    DEFAULT_PROVIDER: str = "ollama"
    DEFAULT_MODEL: str = "llama3.2:3b"
    
    # Security & Limits
    API_KEY: Optional[str] = None
    ENABLE_AUTH: bool = True
    ENABLE_RATE_LIMIT: bool = False
    CORS_ORIGINS: str = "*"
    MAX_REQUEST_SIZE: int = 5242880  # 5MB Limit
    MAX_MESSAGE_COUNT: int = 50      # Max messages in context
    REQUEST_TIMEOUT: int = 60
    
    # System & Execution Tuning
    THREAD_POOL_SIZE: int = 20        # Reusable global pool
    HEALTH_CACHE_SECONDS: int = 30
    LOG_LEVEL: str = "INFO"
    LOG_RETENTION_DAYS: int = 30

    # Configure Pydantic to read from the .env file and ignore extra variables
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="ignore"
    )

settings = Settings()