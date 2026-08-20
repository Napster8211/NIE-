"""
NapsterTec AI - Opportunity Repository
Module: app/repositories/opportunity_repository.py
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.models.opportunity import OpportunityIntelligence
from app.schemas.shared_artifacts import OpportunityArtifact

logger = logging.getLogger(__name__)

class OpportunityRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def ensure_table_exists(self):
        """Auto-healing failsafe split into single executions for asyncpg compatibility."""
        try:
            table_sql = text("""
            CREATE TABLE IF NOT EXISTS opportunity_intelligence (
                id VARCHAR PRIMARY KEY,
                lead_id VARCHAR NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                level VARCHAR NOT NULL,
                report JSON NOT NULL,
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc')
            )
            """)
            index_sql = text("""
            CREATE INDEX IF NOT EXISTS ix_opportunity_lead_id ON opportunity_intelligence (lead_id)
            """)
            await self.db.execute(table_sql)
            await self.db.execute(index_sql)
            await self.db.commit()
        except Exception as e:
            logger.warning(f"[OpportunityRepository] Auto-table creation warning: {e}")

    async def save_artifact(self, artifact: OpportunityArtifact) -> int:
        try:
            await self.ensure_table_exists()
            
            stmt = select(OpportunityIntelligence.version)\
                .where(OpportunityIntelligence.lead_id == artifact.lead_id)\
                .order_by(OpportunityIntelligence.version.desc())\
                .limit(1)
            
            result = await self.db.execute(stmt)
            latest_version = result.scalar_one_or_none() or 0
            new_version = latest_version + 1
            
            artifact.version = new_version
            
            new_intel = OpportunityIntelligence(
                id=artifact.artifact_id,
                lead_id=artifact.lead_id,
                version=new_version,
                level=artifact.opportunity_level,
                report=artifact.model_dump(mode="json")
            )
            
            self.db.add(new_intel)
            await self.db.commit()
            return new_version
            
        except Exception as e:
            logger.error(f"[OpportunityRepository] Failed to save artifact: {e}", exc_info=True)
            await self.db.rollback()
            return -1