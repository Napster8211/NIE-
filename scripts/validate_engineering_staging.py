"""Static fail-closed validation for the proposed Engineering staging files."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_TEMPLATE = ROOT / ".env.staging.example"
BLUEPRINT = ROOT / "render.staging.yaml"

REQUIRED_NAMES = {
    "NIE_ENV",
    "DATABASE_URL",
    "NIE_TRUSTED_FRONTEND_ORIGINS",
    "NIE_ENGINEERING_MODE_ENABLED",
    "NIE_ENGINEERING_OWNER_ONLY",
    "NIE_ENGINEERING_WORKSPACE_ROOT",
    "NIE_ENGINEERING_RUNNER",
    "NIE_ENGINEERING_MAX_CONCURRENT_EXECUTIONS",
    "NIE_ENGINEERING_MAX_PROCESSES",
    "VERCEL_TOKEN",
    "VERCEL_TEAM_ID",
    "VERCEL_PROJECT_ID",
    "NIE_ENGINEERING_SANDBOX_IMAGE",
    "NIE_ENGINEERING_SANDBOX_TIMEOUT_SECONDS",
    "NIE_ENGINEERING_SYNCHRONIZATION_TIMEOUT_SECONDS",
    "NIE_ENGINEERING_CLEANUP_TIMEOUT_SECONDS",
    "NIE_ENGINEERING_NETWORK_POLICY",
    "NIE_ENGINEERING_NETWORK_ALLOWLIST",
}


def _environment_names(text: str) -> set[str]:
    return {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }


def validate() -> None:
    template = ENV_TEMPLATE.read_text(encoding="utf-8")
    blueprint = BLUEPRINT.read_text(encoding="utf-8")
    missing_template = REQUIRED_NAMES - _environment_names(template)
    missing_blueprint = {name for name in REQUIRED_NAMES if f"key: {name}" not in blueprint}
    if missing_template:
        raise RuntimeError(f"STAGING_TEMPLATE_VARIABLES_MISSING:{','.join(sorted(missing_template))}")
    if missing_blueprint:
        raise RuntimeError(f"STAGING_BLUEPRINT_VARIABLES_MISSING:{','.join(sorted(missing_blueprint))}")
    required_fragments = {
        "NIE_ENGINEERING_RUNNER=vercel_sandbox": template,
        "NIE_ENGINEERING_MODE_ENABLED=false": template,
        "NIE_ENGINEERING_OWNER_ONLY=true": template,
        "NIE_ENGINEERING_NETWORK_POLICY=deny_all": template,
        "startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1": blueprint,
        "healthCheckPath: /health": blueprint,
        "preDeployCommand: python scripts/apply_engineering_migration.py": blueprint,
    }
    missing_safety = [fragment for fragment, text in required_fragments.items() if fragment not in text]
    if missing_safety:
        raise RuntimeError(f"STAGING_SAFETY_CONFIGURATION_MISSING:{','.join(missing_safety)}")
    if "NIE_ENGINEERING_MODE_ENABLED=true" in template or "NIE_ENGINEERING_OWNER_ONLY=false" in template:
        raise RuntimeError("STAGING_UNSAFE_DEFAULT")


if __name__ == "__main__":
    validate()
    print("ENGINEERING_STAGING_CONFIGURATION_OK")
