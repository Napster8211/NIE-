"""
NapsterTec AI - Lead Intelligence Schemas (Hardened)
Module: app/schemas/lead.py
"""
from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_validator

class LeadBusiness(BaseModel):
    name: str = Field(..., description="Canonical business name.")
    category: Optional[str] = Field(default=None)
    description: Optional[str] = None

class LeadLocation(BaseModel):
    address: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class LeadContact(BaseModel):
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None

class LeadSource(BaseModel):
    provider: str = Field(..., description="E.g., apify, apollo, google_places")
    provider_mode: str = Field(default="live", description="'live' or 'mock'")
    source_type: str = Field(..., description="E.g., google_maps, business_directory")
    external_id: Optional[str] = None
    place_id: Optional[str] = None
    source_url: Optional[str] = None

class LeadReputation(BaseModel):
    rating: Optional[float] = None
    review_count: Optional[int] = None

class LeadQualification(BaseModel):
    status: str = Field(default="unqualified", description="qualified, needs_review, or unqualified")
    score: Optional[int] = Field(default=None)
    signals: List[str] = Field(default_factory=list)


class BusinessEntityQualificationStatus(str, Enum):
    """Deterministic confidence that discovery evidence identifies one business."""

    VERIFIED_BUSINESS = "VERIFIED_BUSINESS"
    LIKELY_BUSINESS = "LIKELY_BUSINESS"
    UNVERIFIED = "UNVERIFIED"
    NON_BUSINESS_SOURCE = "NON_BUSINESS_SOURCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class BusinessSourceType(str, Enum):
    """The kind of page supplying evidence about a candidate business."""

    OFFICIAL_BUSINESS_SITE = "OFFICIAL_BUSINESS_SITE"
    BUSINESS_PROFILE = "BUSINESS_PROFILE"
    DIRECTORY = "DIRECTORY"
    AGGREGATOR = "AGGREGATOR"
    ARTICLE = "ARTICLE"
    REPORT = "REPORT"
    MARKETPLACE = "MARKETPLACE"
    SOCIAL_PROFILE = "SOCIAL_PROFILE"
    SEARCH_PAGE = "SEARCH_PAGE"
    UNKNOWN = "UNKNOWN"


class BusinessDiscoveryQueryMode(str, Enum):
    """Deterministic intent applied when constructing one provider query."""

    GENERIC_DISCOVERY = "GENERIC_DISCOVERY"
    QUALIFIED_ENTITY_SEARCH = "QUALIFIED_ENTITY_SEARCH"


class BusinessEntityQualification(BaseModel):
    """Fail-closed entity proof kept separately from retrieval provenance."""

    status: BusinessEntityQualificationStatus = BusinessEntityQualificationStatus.UNVERIFIED
    source_type: BusinessSourceType = BusinessSourceType.UNKNOWN
    qualified: bool = False
    business_name: Optional[str] = None
    business_category: Optional[str] = None
    business_location: Optional[str] = None
    business_domain: Optional[str] = None
    source_url: Optional[str] = None
    qualification_reasons: List[str] = Field(default_factory=list)


class BusinessDiscoveryScope(BaseModel):
    """Structured provider input kept separate from orchestration action text."""

    category: str = Field(..., min_length=1)
    location: str = Field(..., min_length=1)
    max_results: int = Field(default=1, ge=1, le=100)
    candidate_scan_limit: int = Field(default=1, ge=1, le=20)
    query_mode: BusinessDiscoveryQueryMode = BusinessDiscoveryQueryMode.GENERIC_DISCOVERY

    @field_validator("category", "location")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = " ".join(str(value).split())
        if not normalized:
            raise ValueError("discovery scope values must be nonempty")
        return normalized

class LeadProvenance(BaseModel):
    """Tracks exactly where data came from to prevent hallucination assumptions."""
    business_name: str = "unknown"
    phone: str = "unknown"
    website: str = "unknown"
    location: str = "unknown"
    rating: str = "unknown"

class LeadMetadata(BaseModel):
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_run_id: str = Field(..., description="Traceability link")
    provenance: LeadProvenance = Field(default_factory=LeadProvenance)

class LeadCreate(BaseModel):
    business: LeadBusiness
    location: LeadLocation
    contact: LeadContact
    source: LeadSource
    reputation: LeadReputation
    qualification: LeadQualification
    metadata: LeadMetadata

class LeadResponse(LeadCreate):
    id: str
