"""
NapsterTec AI - Finance Repository
Module: app/repositories/finance_repository.py
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.models.finance import FinanceOperationsBlueprint
from app.schemas.shared_artifacts import FinanceArtifact

logger = logging.getLogger(__name__)

class FinanceRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def ensure_table_exists(self):
        try:
            table_sql = text("""
            CREATE TABLE IF NOT EXISTS finance_evaluations (
                id VARCHAR PRIMARY KEY,
                lead_id VARCHAR NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                report JSON NOT NULL,
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc')
            )
            """)
            index_sql = text("CREATE INDEX IF NOT EXISTS ix_finance_lead_id ON finance_evaluations (lead_id)")
            
            await self.db.execute(table_sql)
            await self.db.execute(index_sql)
            await self.db.commit()
        except Exception as e:
            await self.db.rollback()
            logger.warning(f"[FinanceRepository] Auto-table creation warning: {e}")

    async def save_artifact(self, artifact: FinanceArtifact) -> int:
        try:
            await self.ensure_table_exists()
            
            stmt = select(FinanceOperationsBlueprint.version)\
                .where(FinanceOperationsBlueprint.lead_id == artifact.lead_id)\
                .order_by(FinanceOperationsBlueprint.version.desc())\
                .limit(1)
            
            result = await self.db.execute(stmt)
            latest_version = result.scalar_one_or_none() or 0
            new_version = latest_version + 1
            artifact.version = new_version
            
            new_blueprint = FinanceOperationsBlueprint(
                id=artifact.artifact_id,
                lead_id=artifact.lead_id,
                version=new_version,
                report=artifact.model_dump(mode="json")
            )
            
            self.db.add(new_blueprint)
            await self.db.commit()
            return new_version
        except Exception as e:
            logger.error(f"[FinanceRepository] Failed to save artifact: {e}", exc_info=True)
            await self.db.rollback()
            return -1