"""
NapsterTec AI - Canonical Director Voice Schemas
Module: app/schemas/director_voice.py
"""
from typing import Optional
from pydantic import BaseModel, Field

class DirectorVoiceRequest(BaseModel):
    # Changed from strict enum to str to accept standard briefing types AND "RAW" chat text
    briefing_type: str = Field(..., description="The type of canonical briefing to synthesize, or 'RAW' for direct conversational text.")
    target_id: Optional[str] = Field(default=None, description="Objective ID, Department ID, etc. if required.")
    text: Optional[str] = Field(default=None, description="Raw text payload to synthesize when briefing_type is 'RAW'.")