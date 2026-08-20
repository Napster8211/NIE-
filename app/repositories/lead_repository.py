"""
NapsterTec AI - Lead Repository
Module: app/repositories/lead_repository.py
"""
import logging
from typing import Optional, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.lead import Lead
from app.schemas.lead import LeadCreate

logger = logging.getLogger(__name__)

class LeadRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    def _extract_domain(self, url: Optional[str]) -> Optional[str]:
        if not url: return None
        return url.replace("http://", "").replace("https://", "").replace("www.", "").split("/")[0].lower()

    async def find_duplicate(self, lead_data: LeadCreate) -> Optional[Lead]:
        """Deterministic deduplication strategy."""
        # Priority 1: Google Place ID
        if lead_data.source.place_id:
            stmt = select(Lead).where(Lead.place_id == lead_data.source.place_id)
            result = await self.db.execute(stmt)
            existing = result.scalar_one_or_none()
            if existing: return existing

        # Priority 2: Normalized Website Domain
        domain = self._extract_domain(lead_data.contact.website)
        if domain:
            stmt = select(Lead).where(Lead.website_domain == domain)
            result = await self.db.execute(stmt)
            existing = result.scalar_one_or_none()
            if existing: return existing
            
        return None

    async def upsert_lead(self, lead_data: LeadCreate) -> Tuple[Lead, bool]:
        """Returns (Lead, is_new_record). Preserves provenance on updates."""
        existing_lead = await self.find_duplicate(lead_data)
        
        domain = self._extract_domain(lead_data.contact.website)

        if existing_lead:
            # Update deterministic fields but preserve original discovery timestamp
            existing_lead.business = lead_data.business.model_dump()
            existing_lead.location = lead_data.location.model_dump()
            existing_lead.contact = lead_data.contact.model_dump()
            existing_lead.reputation = lead_data.reputation.model_dump()
            existing_lead.qualification = lead_data.qualification.model_dump()
            
            # Merge metadata
            current_meta = existing_lead.metadata_blob
            current_meta["updated_at"] = lead_data.metadata.updated_at.isoformat()
            current_meta["last_agent_run_id"] = lead_data.metadata.agent_run_id
            existing_lead.metadata_blob = current_meta
            
            self.db.add(existing_lead)
            await self.db.commit()
            await self.db.refresh(existing_lead)
            return existing_lead, False
        else:
            # Insert new lead
            new_lead = Lead(
                business_name=lead_data.business.name,
                place_id=lead_data.source.place_id,
                website_domain=domain,
                business=lead_data.business.model_dump(),
                location=lead_data.location.model_dump(),
                contact=lead_data.contact.model_dump(),
                source=lead_data.source.model_dump(),
                reputation=lead_data.reputation.model_dump(),
                qualification=lead_data.qualification.model_dump(),
                metadata_blob=lead_data.metadata.model_dump(mode='json')
            )
            self.db.add(new_lead)
            await self.db.commit()
            await self.db.refresh(new_lead)
            return new_lead, True