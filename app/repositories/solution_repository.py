"""
NapsterTec AI - Solution Repository
Module: app/repositories/solution_repository.py
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.models.solution import BusinessSolutionBlueprint
from app.schemas.shared_artifacts import BusinessSolutionArtifact

logger = logging.getLogger(__name__)

class SolutionRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def ensure_table_exists(self):
        try:
            table_sql = text("""
            CREATE TABLE IF NOT EXISTS business_solutions (
                id VARCHAR PRIMARY KEY,
                lead_id VARCHAR NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                solution_type VARCHAR NOT NULL,
                report JSON NOT NULL,
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc')
            )
            """)
            index_sql = text("CREATE INDEX IF NOT EXISTS ix_solution_lead_id ON business_solutions (lead_id)")
            await self.db.execute(table_sql)
            await self.db.execute(index_sql)
            await self.db.commit()
        except Exception as e:
            logger.warning(f"[SolutionRepository] Auto-table creation warning: {e}")

    async def save_artifact(self, artifact: BusinessSolutionArtifact) -> int:
        try:
            await self.ensure_table_exists()
            
            stmt = select(BusinessSolutionBlueprint.version)\
                .where(BusinessSolutionBlueprint.lead_id == artifact.lead_id)\
                .order_by(BusinessSolutionBlueprint.version.desc())\
                .limit(1)
            
            result = await self.db.execute(stmt)
            latest_version = result.scalar_one_or_none() or 0
            new_version = latest_version + 1
            artifact.version = new_version
            
            new_blueprint = BusinessSolutionBlueprint(
                id=artifact.artifact_id,
                lead_id=artifact.lead_id,
                version=new_version,
                solution_type=artifact.solution_type,
                report=artifact.model_dump(mode="json")
            )
            
            self.db.add(new_blueprint)
            await self.db.commit()
            return new_version
        except Exception as e:
            logger.error(f"[SolutionRepository] Failed to save artifact: {e}", exc_info=True)
            await self.db.rollback()
            return -1