"""
NapsterTec AI - Canonical Director Speech Schema
Module: app/schemas/director_speech.py
"""
from typing import Optional

from pydantic import BaseModel, Field


class DirectorTranscriptionResponse(BaseModel):
    request_id: str
    transcript: str = Field(..., description="The recognized text from the audio input.")
    confidence: Optional[float] = Field(
        default=None,
        description="Provider-reported confidence/probability when available.",
    )
    language: str = Field(default="unknown")
    duration_ms: int = Field(default=0, ge=0)