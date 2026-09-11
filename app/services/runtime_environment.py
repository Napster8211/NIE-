"""Small dependency-free runtime environment helpers for infrastructure services."""

import os


def runtime_environment() -> str:
    configured = os.getenv("NIE_ENV") or os.getenv("ENVIRONMENT")
    if configured:
        return configured.strip().lower()
    if os.getenv("RENDER", "").strip().lower() in {"1", "true", "yes"}:
        return "production"
    return "development"


def is_production() -> bool:
    return runtime_environment() in {"production", "prod"}
