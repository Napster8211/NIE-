"""Evidence-source classifications shared by tools, agents, and Mission Intelligence."""

from enum import Enum
from typing import Any


class EvidenceSource(str, Enum):
    LIVE_EXTERNAL = "LIVE_EXTERNAL"
    MOCK_FALLBACK = "MOCK_FALLBACK"
    INTERNAL_FIXTURE = "INTERNAL_FIXTURE"
    USER_PROVIDED = "USER_PROVIDED"
    DATABASE_EXISTING = "DATABASE_EXISTING"
    UNKNOWN = "UNKNOWN"


_EVIDENCE_SOURCE_ALIASES = {
    "LIVE": EvidenceSource.LIVE_EXTERNAL,
    "EXTERNAL": EvidenceSource.LIVE_EXTERNAL,
    "MOCK": EvidenceSource.MOCK_FALLBACK,
    "FALLBACK": EvidenceSource.MOCK_FALLBACK,
    "FIXTURE": EvidenceSource.INTERNAL_FIXTURE,
    "USER": EvidenceSource.USER_PROVIDED,
    "DATABASE": EvidenceSource.DATABASE_EXISTING,
    "DB": EvidenceSource.DATABASE_EXISTING,
}

PRODUCTION_SUCCESS_SOURCES = frozenset({
    EvidenceSource.LIVE_EXTERNAL,
    EvidenceSource.USER_PROVIDED,
    EvidenceSource.DATABASE_EXISTING,
})


def normalize_evidence_source(value: Any) -> EvidenceSource:
    if isinstance(value, EvidenceSource):
        return value
    normalized = str(value or "").strip().upper()
    if not normalized:
        return EvidenceSource.UNKNOWN
    try:
        return EvidenceSource(normalized)
    except ValueError:
        return _EVIDENCE_SOURCE_ALIASES.get(normalized, EvidenceSource.UNKNOWN)


def is_simulation_evidence(value: Any) -> bool:
    return normalize_evidence_source(value) in {
        EvidenceSource.MOCK_FALLBACK,
        EvidenceSource.INTERNAL_FIXTURE,
    }


def qualifies_for_production_success(value: Any) -> bool:
    return normalize_evidence_source(value) in PRODUCTION_SUCCESS_SOURCES
