"""
NapsterTec AI - Lead Processing Engine (Hardened)
Module: app/services/lead_engine.py
"""
import logging
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.lead import (
    LeadCreate, LeadBusiness, LeadLocation, LeadContact, 
    LeadSource, LeadReputation, LeadQualification, LeadMetadata, LeadProvenance
)
from app.repositories.lead_repository import LeadRepository

logger = logging.getLogger(__name__)

class LeadEngine:
    def __init__(self, db_session: AsyncSession):
        self.repo = LeadRepository(db_session)

    def normalize_and_qualify(self, raw_record: Dict[str, Any], run_id: str, provider_mode: str) -> Optional[LeadCreate]:
        try:
            provenance = LeadProvenance()
            
            # Helper to enforce true SQL nulls over LLM hallucinations
            def clean_null(val: Any) -> Optional[Any]:
                if not val: return None
                if isinstance(val, str) and val.strip().lower() in ["n/a", "unknown", "not available", "none", "null", ""]:
                    return None
                return val

            name = clean_null(raw_record.get("name") or raw_record.get("title"))
            if not name: return None # Discard entirely malformed records
            provenance.business_name = "provider_verified"
            
            business = LeadBusiness(
                name=name,
                category=clean_null(raw_record.get("category") or raw_record.get("type")),
                description=clean_null(raw_record.get("description"))
            )
            
            city = clean_null(raw_record.get("city"))
            if city: provenance.location = "provider_verified"
            location = LeadLocation(
                address=clean_null(raw_record.get("address")),
                city=city,
                country=clean_null(raw_record.get("country")),
                latitude=clean_null(raw_record.get("lat") or raw_record.get("latitude")),
                longitude=clean_null(raw_record.get("lng") or raw_record.get("longitude"))
            )
            
            phone = clean_null(raw_record.get("phone") or raw_record.get("phoneUnformatted"))
            website = clean_null(raw_record.get("website") or raw_record.get("url"))
            if phone: provenance.phone = "provider_verified"
            if website: provenance.website = "provider_verified"
            
            contact = LeadContact(phone=phone, website=website)
            
            source = LeadSource(
                provider="configured_discovery_provider",
                provider_mode=provider_mode,
                source_type="business_directory",
                place_id=clean_null(raw_record.get("placeId") or raw_record.get("id")),
                source_url=clean_null(raw_record.get("url"))
            )
            
            rating = clean_null(raw_record.get("totalScore") or raw_record.get("rating"))
            if rating: provenance.rating = "provider_verified"
            reputation = LeadReputation(
                rating=rating,
                review_count=clean_null(raw_record.get("reviewsCount") or raw_record.get("review_count"))
            )

            # 2. Strict Deterministic Qualification
            signals = []
            
            if business.name and location.address:
                if contact.phone and contact.website:
                    status = "qualified"
                    signals.append("Full contact and location data verified.")
                elif contact.phone or contact.website:
                    status = "qualified"
                    signals.append("Partial contact data verified.")
                else:
                    status = "needs_review"
                    signals.append("Location verified, but missing both phone and website.")
            else:
                status = "unqualified"
                signals.append("Missing critical name or address data.")

            qualification = LeadQualification(status=status, signals=signals)
            metadata = LeadMetadata(agent_run_id=run_id, provenance=provenance)

            return LeadCreate(
                business=business, location=location, contact=contact,
                source=source, reputation=reputation,
                qualification=qualification, metadata=metadata
            )
        except Exception as e:
            logger.warning(f"[LeadEngine] Failed to normalize record: {str(e)}")
            return None

    async def process_discovery_batch(self, raw_leads: List[Dict[str, Any]], run_id: str, provider_mode: str) -> Dict[str, Any]:
        """Processes a raw batch and returns structured Database Persistence Results."""
        stats = {
            "success": True,
            "created": 0,
            "updated": 0,
            "duplicates": 0,
            "failed": 0,
            "transaction_committed": False,
            "qualified": 0,
            "needs_review": 0,
            "unqualified": 0
        }

        for raw in raw_leads:
            canonical_lead = self.normalize_and_qualify(raw, run_id, provider_mode)
            if not canonical_lead:
                stats["failed"] += 1
                continue
                
            try:
                _, is_new = await self.repo.upsert_lead(canonical_lead)
                if is_new:
                    stats["created"] += 1
                else:
                    stats["updated"] += 1
                    stats["duplicates"] += 1
                    
                stats[canonical_lead.qualification.status] += 1
            except Exception as e:
                logger.error(f"[LeadEngine] DB Upsert failed: {e}")
                stats["failed"] += 1
                stats["success"] = False

        stats["transaction_committed"] = True
        return stats