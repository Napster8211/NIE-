"""
NapsterTec AI - Session Vault
Module: app/browser/session_vault.py

Securely stores and retrieves persistent Playwright browser sessions (cookies, 
localStorage, tokens) with strict path traversal protection, permission restrictions,
and optional Fernet encryption at rest.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_SESSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "storage" / "sessions"
_PROFILE_NAME_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]{1,128}$")


def validate_profile_name(profile_name: str) -> str:
    """Validate profile name format and reject directory traversal or path separators."""
    if not profile_name or not isinstance(profile_name, str):
        raise ValueError("Profile name must be a non-empty string.")
    clean = profile_name.strip()
    if "/" in clean or "\\" in clean or ".." in clean:
        raise ValueError(
            f"Invalid profile name '{profile_name}': directory traversal and path separators are prohibited."
        )
    stem = clean[:-5] if clean.lower().endswith(".json") else clean
    if not stem or not _PROFILE_NAME_REGEX.match(stem):
        raise ValueError(
            f"Invalid profile name '{profile_name}'. Must be 1-128 alphanumeric, dash, dot, or underscore characters."
        )
    return clean


class SessionVault:
    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = (Path(storage_dir) if storage_dir else DEFAULT_SESSIONS_DIR).resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.storage_dir.chmod(0o700)
        except (OSError, NotImplementedError):
            pass

    def _normalize_name(self, profile_name: str) -> str:
        clean = validate_profile_name(profile_name).lower()
        if not clean.endswith(".json"):
            clean = f"{clean}.json"
        return clean

    def get_storage_path(self, profile_name: str) -> Path:
        filename = self._normalize_name(profile_name)
        target = (self.storage_dir / filename).resolve()
        # Verify resolved path is strictly inside storage_dir
        if not target.is_relative_to(self.storage_dir) or target == self.storage_dir:
            raise ValueError(f"Path traversal detected: '{profile_name}' resolves outside session vault storage.")
        return target

    def _get_fernet(self):
        vault_key = os.getenv("NIE_SESSION_VAULT_KEY", "").strip()
        if not vault_key:
            return None
        try:
            from cryptography.fernet import Fernet
            return Fernet(vault_key.encode("utf-8") if isinstance(vault_key, str) else vault_key)
        except Exception as e:
            logger.warning("[SessionVault] Invalid NIE_SESSION_VAULT_KEY: %s", e)
            return None

    def has_session(self, profile_name: str) -> bool:
        try:
            path = self.get_storage_path(profile_name)
            if not path.exists():
                return False
            return path.stat().st_size > 20
        except (ValueError, OSError):
            return False

    def load_session(self, profile_name: str) -> Optional[Dict[str, Any]]:
        path = self.get_storage_path(profile_name)
        if not path.exists():
            return None
        try:
            with open(path, "rb") as f:
                raw_bytes = f.read()
            fernet = self._get_fernet()
            if fernet:
                try:
                    decrypted = fernet.decrypt(raw_bytes)
                    return json.loads(decrypted.decode("utf-8"))
                except Exception:
                    pass
            return json.loads(raw_bytes.decode("utf-8"))
        except Exception as e:
            logger.error("[SessionVault] Error loading session '%s': %s", profile_name, e)
            return None

    def save_session(self, profile_name: str, state_data: Dict[str, Any]) -> Path:
        path = self.get_storage_path(profile_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        json_bytes = json.dumps(state_data, indent=2).encode("utf-8")
        fernet = self._get_fernet()
        data_to_write = fernet.encrypt(json_bytes) if fernet else json_bytes

        temp_path = path.with_suffix(".tmp")
        with open(temp_path, "wb") as f:
            f.write(data_to_write)
        try:
            temp_path.chmod(0o600)
        except (OSError, NotImplementedError):
            pass
        temp_path.replace(path)
        try:
            path.chmod(0o600)
        except (OSError, NotImplementedError):
            pass
        logger.info(
            "[SessionVault] Successfully saved session profile '%s' to %s (encrypted=%s)",
            profile_name,
            path,
            fernet is not None,
        )
        return path

    def delete_session(self, profile_name: str) -> bool:
        path = self.get_storage_path(profile_name)
        if path.exists():
            try:
                path.unlink()
                logger.info("[SessionVault] Deleted session profile '%s'", profile_name)
                return True
            except OSError as e:
                logger.error("[SessionVault] Error deleting session '%s': %s", profile_name, e)
                return False
        return False

    def list_active_sessions(self) -> List[Dict[str, Any]]:
        results = []
        for file in self.storage_dir.glob("*.json"):
            try:
                stat = file.stat()
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
                results.append({
                    "profile_name": file.stem,
                    "filename": file.name,
                    "file_size_bytes": stat.st_size,
                    "last_modified": mtime,
                    "is_valid": stat.st_size > 20,
                })
            except Exception as e:
                logger.warning(f"[SessionVault] Error stat on '{file}': {e}")
        return results


# Singleton instance
session_vault = SessionVault()
