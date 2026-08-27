"""
NapsterTec AI - Canonical Executive Briefing Schemas
Module: app/schemas/executive_briefing.py
"""
from typing import Dict, Any, List, Optional
from enum import Enum
from datetime import datetime, timezone
from pydantic import BaseModel, Field
import uuid

class ExecutiveBriefingType(str, Enum):
    DAILY = "DAILY"
    COMPANY_STATUS = "COMPANY_STATUS"
    OBJECTIVE = "OBJECTIVE"
    MISSION = "MISSION"
    DEPARTMENT = "DEPARTMENT"
    FINANCE = "FINANCE"
    RISK = "RISK"
    APPROVAL = "APPROVAL"
    OWNER_ACTION = "OWNER_ACTION"
    EXECUTIVE_DECISION = "EXECUTIVE_DECISION"

class ExecutiveBriefingFact(BaseModel):
    fact_type: str = Field(description="e.g., ACTIVE_OBJECTIVES, PENDING_APPROVALS")
    label: str
    value: Any
    source_type: str = Field(description="e.g., REPOSITORY, FINANCE_ENGINE")
    source_id: Optional[str] = None
    verified: bool = Field(default=True)

class ExecutiveBriefingSection(BaseModel):
    section_id: str = Field(default_factory=lambda: f"sec_{uuid.uuid4().hex[:8]}")
    heading: str
    content: str
    priority: str = Field(default="NORMAL")
    severity: str = Field(default="NORMAL")
    facts: List[ExecutiveBriefingFact] = Field(default_factory=list)
    references: List[str] = Field(default_factory=list)

class ExecutiveBriefing(BaseModel):
    briefing_id: str = Field(default_factory=lambda: f"brf_{uuid.uuid4().hex[:12]}")
    briefing_type: ExecutiveBriefingType
    title: str
    summary: str
    speech_text: str = Field(..., description="Prepared safe text intended for TTS.")
    sections: List[ExecutiveBriefingSection] = Field(default_factory=list)
    priority: str = Field(default="NORMAL")
    severity: str = Field(default="NORMAL")
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Lineage / Context Targets
    objective_id: Optional[str] = None
    mission_id: Optional[str] = None
    department_id: Optional[str] = None

    requires_owner_attention: bool = Field(default=False)
    speakable: bool = Field(default=True)
    stale: bool = Field(default=False)
    
    metadata: Dict[str, Any] = Field(default_factory=dict)