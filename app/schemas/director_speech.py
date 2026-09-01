"""
NapsterTec AI - Canonical Director Speech Schema
Module: app/schemas/director_speech.py
"""
from typing import List, Optional

from pydantic import BaseModel, Field


class DirectorTranscriptionResponse(BaseModel):
    request_id: str
    correlation_id: Optional[str] = None
    transcript: str = Field(..., description="The recognized text from the audio input.")
    confidence: Optional[float] = Field(
        default=None,
        description="Provider-reported confidence/probability when available.",
    )
    language: str = Field(default="unknown")
    language_probability: Optional[float] = None
    duration_ms: int = Field(default=0, ge=0)
    clarification_required: bool = False
    requires_confirmation: bool = False
    quality_reasons: List[str] = Field(default_factory=list)
    avg_logprob: Optional[float] = None
    no_speech_probability: Optional[float] = None
    timings: dict = Field(default_factory=dict)
