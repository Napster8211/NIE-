"""Conservative, provider-neutral quality gates for Director voice commands."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional, Sequence


HIGH_IMPACT_PATTERNS = (
    r"\b(?:approve|authorize|reject|cancel|delete|destroy|deploy|publish)\b",
    r"\b(?:pay|purchase|spend|transfer|refund|budget)\b",
    r"\b(?:send|email|message|outreach|contact)\b",
    r"\b(?:permission|credential|password|api key|security)\b",
)

# Conservative initial gate. This value is intentionally named and observable;
# changing it requires recorded-audio evidence rather than UI-driven tuning.
MIN_AVERAGE_LOGPROB = -1.0
MAX_NO_SPEECH_PROBABILITY = 0.65


@dataclass(frozen=True)
class TranscriptQualityAssessment:
    confidence: Optional[float]
    clarification_required: bool
    requires_confirmation: bool
    reasons: tuple[str, ...]


def _severe_repetition(tokens: Sequence[str]) -> bool:
    if len(tokens) < 6:
        return False
    if len(set(tokens)) / len(tokens) < 0.35:
        return True
    return any(tokens[index:index + 3] == [tokens[index]] * 3 for index in range(len(tokens) - 2))


def assess_transcript_quality(
    text: str,
    *,
    duration_seconds: float,
    avg_logprob: Optional[float],
    no_speech_probability: Optional[float],
    language_probability: Optional[float] = None,
) -> TranscriptQualityAssessment:
    normalized = " ".join((text or "").split()).strip()
    tokens = re.findall(r"[a-z0-9']+", normalized.casefold())
    reasons: list[str] = []

    if not normalized:
        reasons.append("EMPTY_TRANSCRIPT")
    if avg_logprob is not None and avg_logprob < MIN_AVERAGE_LOGPROB:
        reasons.append("LOW_AVERAGE_LOGPROB")
    if (
        no_speech_probability is not None
        and no_speech_probability > MAX_NO_SPEECH_PROBABILITY
    ):
        reasons.append("HIGH_NO_SPEECH_PROBABILITY")
    if duration_seconds >= 1.5 and len(tokens) < 2:
        reasons.append("IMPLAUSIBLY_SHORT_TRANSCRIPT")
    if duration_seconds > 0 and len(tokens) / duration_seconds > 7.0:
        reasons.append("IMPLAUSIBLE_TRANSCRIPT_DENSITY")
    if _severe_repetition(tokens):
        reasons.append("EXCESSIVE_REPETITION")

    confidence: Optional[float]
    if avg_logprob is not None:
        confidence = max(0.0, min(1.0, math.exp(avg_logprob)))
        if no_speech_probability is not None:
            confidence *= max(0.0, 1.0 - no_speech_probability)
    elif language_probability is not None:
        # Language probability is only a fallback signal, not semantic confidence.
        confidence = max(0.0, min(1.0, float(language_probability)))
    else:
        confidence = None

    requires_confirmation = any(
        re.search(pattern, normalized, flags=re.IGNORECASE)
        for pattern in HIGH_IMPACT_PATTERNS
    )
    return TranscriptQualityAssessment(
        confidence=confidence,
        clarification_required=bool(reasons),
        requires_confirmation=requires_confirmation,
        reasons=tuple(reasons),
    )
