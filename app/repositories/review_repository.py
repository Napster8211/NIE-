"""
NapsterTec AI - Review Repository
Module: app/repositories/review_repository.py
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.models.review import EngineeringReviewBlueprint
from app.schemas.shared_artifacts import ReviewArtifact

logger = logging.getLogger(__name__)

class ReviewRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def ensure_table_exists(self):
        try:
            table_sql = text("""
            CREATE TABLE IF NOT EXISTS engineering_reviews (
                id VARCHAR PRIMARY KEY,
                lead_id VARCHAR NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                approval_status VARCHAR NOT NULL,
                report JSON NOT NULL,
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc')
            )
            """)
            index_sql = text("CREATE INDEX IF NOT EXISTS ix_review_lead_id ON engineering_reviews (lead_id)")
            
            await self.db.execute(table_sql)
            await self.db.execute(index_sql)
        except Exception as e:
            logger.warning(f"[ReviewRepository] Auto-table creation warning: {e}")

    async def save_artifact(self, artifact: ReviewArtifact) -> int:
        try:
            await self.ensure_table_exists()
            
            stmt = select(EngineeringReviewBlueprint.version)\
                .where(EngineeringReviewBlueprint.lead_id == artifact.lead_id)\
                .order_by(EngineeringReviewBlueprint.version.desc())\
                .limit(1)
            
            result = await self.db.execute(stmt)
            latest_version = result.scalar_one_or_none() or 0
            new_version = latest_version + 1
            artifact.version = new_version
            
            new_blueprint = EngineeringReviewBlueprint(
                id=artifact.artifact_id,
                lead_id=artifact.lead_id,
                version=new_version,
                approval_status=artifact.approval_status,
                report=artifact.model_dump(mode="json")
            )
            
            self.db.add(new_blueprint)
            await self.db.commit()
            return new_version
        except Exception as e:
            logger.error(f"[ReviewRepository] Failed to save artifact: {e}", exc_info=True)
            await self.db.rollback()
            return -1