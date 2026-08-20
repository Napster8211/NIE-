"""
NapsterTec AI - Lead Persistence Tool
Module: app/tools/plugins/lead_upsert.py
"""
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.services.lead_engine import LeadEngine
from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

class LeadUpsertInput(BaseModel):
    raw_leads: List[Dict[str, Any]] = Field(..., description="List of raw business dictionaries discovered by the discovery tool.")
    provider_mode: str = Field(default="mock", description="The provider mode returned by discovery.")

class LeadUpsertOutput(BaseModel):
    success: bool
    created: int
    updated: int
    duplicates: int
    failed: int
    transaction_committed: bool
    qualified: int = 0
    needs_review: int = 0
    unqualified: int = 0
    error: Optional[str] = None

class LeadUpsertTool(BaseTool):
    name: str = "lead_upsert"
    description: str = "Persists canonical business leads to the database. MUST be used instead of generating files."
    input_schema = LeadUpsertInput
    output_schema = LeadUpsertOutput  # <-- FIXED: Now uses Pydantic BaseModel instead of dict
    capabilities = ["lead_generation"]
    permissions = ["write", "database"]

    async def execute(self, raw_leads: List[Dict[str, Any]], provider_mode: str = "mock", **kwargs) -> dict:
        try:
            async with AsyncSessionLocal() as db_session:
                engine = LeadEngine(db_session)
                stats = await engine.process_discovery_batch(raw_leads, "react_session", provider_mode)
                return stats
        except Exception as e:
            logger.error(f"[LeadUpsertTool] Transaction failed: {e}")
            return {
                "success": False,
                "created": 0, "updated": 0, "duplicates": 0, "failed": len(raw_leads),
                "transaction_committed": False,
                "error": str(e)
            }