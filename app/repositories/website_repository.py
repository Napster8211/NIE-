"""
NapsterTec AI - Website Repository
Module: app/repositories/website_repository.py
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.models.website import WebsiteIntelligence
from app.schemas.website import WebsiteArtifact

logger = logging.getLogger(__name__)

class WebsiteRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def save_artifact(self, artifact: WebsiteArtifact) -> int:
        """Appends a new version of the WebsiteArtifact for historical tracking."""
        try:
            # 1. Determine next version number (with Auto-Heal for schema drift)
            try:
                stmt = select(WebsiteIntelligence.version)\
                    .where(WebsiteIntelligence.lead_id == artifact.lead_id)\
                    .order_by(WebsiteIntelligence.version.desc())\
                    .limit(1)
                
                result = await self.db.execute(stmt)
                latest_version = result.scalar_one_or_none() or 0
                
            except Exception as e:
                # SPRINT 3.1 Auto-Heal: If the version column is missing in Postgres, patch it automatically
                if "version does not exist" in str(e) or "UndefinedColumnError" in str(e):
                    logger.warning("[WebsiteRepository] Schema drift detected. Auto-patching table to add 'version' column...")
                    await self.db.rollback() # Clear the failed transaction
                    
                    # Execute raw SQL to alter the table safely
                    await self.db.execute(text("ALTER TABLE website_intelligence ADD COLUMN version INTEGER DEFAULT 1 NOT NULL;"))
                    await self.db.commit()
                    
                    latest_version = 0 # Default since it's newly patched
                else:
                    raise e # Reraise if it's a different database error
            
            new_version = latest_version + 1
            artifact.version = new_version
            
            # 2. Insert immutable historical record
            new_intel = WebsiteIntelligence(
                lead_id=artifact.lead_id,
                version=new_version,
                status=artifact.status,
                report=artifact.model_dump(mode="json")
            )
            
            self.db.add(new_intel)
            await self.db.commit()
            return new_version
            
        except Exception as e:
            logger.error(f"[WebsiteRepository] Failed to save artifact for {artifact.lead_id}: {e}", exc_info=True)
            await self.db.rollback()
            return -1