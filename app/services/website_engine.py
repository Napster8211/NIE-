"""
NapsterTec AI - Website Intelligence Engine
Module: app/services/website_engine.py
"""
import logging
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.website import WebsiteArtifact
from app.repositories.website_repository import WebsiteRepository

logger = logging.getLogger(__name__)

class WebsiteEngine:
    def __init__(self, db_session: AsyncSession):
        self.repo = WebsiteRepository(db_session)

    async def process_and_save(self, lead_id: str, raw_audit: Dict[str, Any], session_id: str) -> Dict[str, Any]:
        """Converts raw data into an Artifact and persists it."""
        try:
            # In a real environment, we map dicts to Pydantic models here.
            # Since our mocked tool already formats exactly to the schema, we can parse it directly.
            artifact = WebsiteArtifact(
                agent_run_id=session_id,
                lead_id=lead_id,
                target_url=raw_audit.get("target_url"),
                status=raw_audit.get("status", "unknown"),
                technology=raw_audit.get("technology", []),
                business_signals=raw_audit.get("business_signals", []),
                recommendations=raw_audit.get("recommendations", []),
                seo=raw_audit.get("seo", {}),
                execution_metadata={"duration_ms": 1250, "tools_used": 2}
            )
            
            version = await self.repo.save_artifact(artifact)
            
            return {
                "success": version > 0,
                "lead_id": lead_id,
                "version_saved": version,
                "artifact": artifact.model_dump(mode="json"),
                "transaction_committed": version > 0
            }
        except Exception as e:
            logger.error(f"[WebsiteEngine] Process Error: {e}", exc_info=True)
            return {"success": False, "error": str(e), "transaction_committed": False}