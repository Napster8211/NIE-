"""
NapsterTec AI - Proposal Repository
Module: app/repositories/proposal_repository.py
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.models.proposal import ProposalIntelligenceBlueprint
from app.schemas.shared_artifacts import ProposalArtifact

logger = logging.getLogger(__name__)

class ProposalRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def ensure_table_exists(self):
        try:
            table_sql = text("""
            CREATE TABLE IF NOT EXISTS business_proposals (
                id VARCHAR PRIMARY KEY,
                lead_id VARCHAR NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                proposal_type VARCHAR NOT NULL,
                report JSON NOT NULL,
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc')
            )
            """)
            index_sql = text("CREATE INDEX IF NOT EXISTS ix_proposal_lead_id ON business_proposals (lead_id)")
            
            await self.db.execute(table_sql)
            await self.db.execute(index_sql)
            # FIXED: Removed mid-transaction commit that causes asyncpg deadlocks
            
        except Exception as e:
            logger.warning(f"[ProposalRepository] Auto-table creation warning: {e}")

    async def save_artifact(self, artifact: ProposalArtifact) -> int:
        try:
            await self.ensure_table_exists()
            
            stmt = select(ProposalIntelligenceBlueprint.version)\
                .where(ProposalIntelligenceBlueprint.lead_id == artifact.lead_id)\
                .order_by(ProposalIntelligenceBlueprint.version.desc())\
                .limit(1)
            
            result = await self.db.execute(stmt)
            latest_version = result.scalar_one_or_none() or 0
            new_version = latest_version + 1
            artifact.version = new_version
            
            new_blueprint = ProposalIntelligenceBlueprint(
                id=artifact.artifact_id,
                lead_id=artifact.lead_id,
                version=new_version,
                proposal_type=artifact.proposal_type,
                report=artifact.model_dump(mode="json")
            )
            
            self.db.add(new_blueprint)
            await self.db.commit()
            return new_version
        except Exception as e:
            logger.error(f"[ProposalRepository] Failed to save artifact: {e}", exc_info=True)
            await self.db.rollback()
            return -1