"""
NapsterTec AI - Session Vault
Module: app/browser/session_vault.py

Securely stores and retrieves persistent Playwright browser sessions (cookies, 
localStorage, tokens) for zero-cost automation across WhatsApp Web, TikTok, 
YouTube, and Instagram.
"""
import os
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DEFAULT_SESSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "storage" / "sessions"

class SessionVault:
    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = Path(storage_dir) if storage_dir else DEFAULT_SESSIONS_DIR
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _normalize_name(self, profile_name: str) -> str:
        clean = profile_name.strip().lower()
        if not clean.endswith(".json"):
            clean = f"{clean}.json"
        return clean

    def get_storage_path(self, profile_name: str) -> Path:
        filename = self._normalize_name(profile_name)
        return self.storage_dir / filename

    def has_session(self, profile_name: str) -> bool:
        path = self.get_storage_path(profile_name)
        if not path.exists():
            return False
        try:
            return path.stat().st_size > 20
        except OSError:
            return False

    def load_session(self, profile_name: str) -> Optional[Dict[str, Any]]:
        path = self.get_storage_path(profile_name)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except Exception as e:
            logger.error(f"[SessionVault] Error loading session '{profile_name}': {e}")
            return None

    def save_session(self, profile_name: str, state_data: Dict[str, Any]) -> Path:
        path = self.get_storage_path(profile_name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2)
        logger.info(f"[SessionVault] Successfully saved session profile '{profile_name}' to {path}")
        return path

    def delete_session(self, profile_name: str) -> bool:
        path = self.get_storage_path(profile_name)
        if path.exists():
            try:
                path.unlink()
                logger.info(f"[SessionVault] Deleted session profile '{profile_name}'")
                return True
            except OSError as e:
                logger.error(f"[SessionVault] Error deleting session '{profile_name}': {e}")
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
                    "is_valid": stat.st_size > 20
                })
            except Exception as e:
                logger.warning(f"[SessionVault] Error stat on '{file}': {e}")
        return results

# Singleton instance
session_vault = SessionVault()
