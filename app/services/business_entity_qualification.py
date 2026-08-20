"""Deterministic business-entity qualification for externally discovered pages.

This module intentionally performs no network calls. It classifies only the
evidence already returned by the configured discovery provider and fails closed
when that evidence does not identify one reasonably specific business.
"""
import ipaddress
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

from app.schemas.lead import (
    BusinessEntityQualification,
    BusinessEntityQualificationStatus,
    BusinessSourceType,
)


_GENERIC_TITLE_PATTERNS = (
    re.compile(r"^(?:the\s+)?\d+\s+best\b", re.IGNORECASE),
    re.compile(
        r"^(?:the\s+)?\d+\s+(?:(?:must[-\s]+try|recommended)\s+)?(?:restaurants?|places?\s+to\s+eat)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:the\s+)?(?:top|best)\s+(?:\d+\s+)?(?:restaurants?|hotels?|companies|businesses|services|providers|agencies|firms)\b", re.IGNORECASE),
    re.compile(r"^(?:a\s+)?guide\s+to\b", re.IGNORECASE),
    re.compile(r"^(?:restaurants?|hotels?|companies|businesses|services|providers|agencies|firms)\s+(?:in|near|for)\b", re.IGNORECASE),
    re.compile(r"\b(?:market|industry|research)\s+(?:analysis|report|forecast|outlook)\b", re.IGNORECASE),
    re.compile(r"\bmarket\s+size\b", re.IGNORECASE),
)
_REPORT_PATH = re.compile(r"/(?:reports?|research)(?:/|$)", re.IGNORECASE)
_ARTICLE_PATH = re.compile(r"/(?:articles?|blog|news|insights?)(?:/|$)", re.IGNORECASE)
_SEARCH_PATH = re.compile(r"/(?:search|results?)(?:/|$)", re.IGNORECASE)
_DIRECTORY_PATH = re.compile(r"/(?:directory|directories|listings?)(?:/|$)", re.IGNORECASE)
_PROFILE_PATH = re.compile(r"/(?:business|company|companies|profile|place|places|listing|store|restaurant|hotel)s?(?:/|$)", re.IGNORECASE)
_MARKETPLACE_PATH = re.compile(r"/(?:marketplace|vendors?|sellers?|shops?)(?:/|$)", re.IGNORECASE)
_SOCIAL_HOST = re.compile(r"(?:^|\.)(?:facebook|instagram|linkedin|x|twitter|tiktok)\.com$", re.IGNORECASE)
_BUSINESS_SIGNAL = re.compile(
    r"\b(?:restaurant|hotel|company|business|services?|products?|shop|store|agency|firm|"
    r"manufacturer|supplier|contractor|studio|clinic|salon|cafe|bar|located|serving|contact)\b",
    re.IGNORECASE,
)
_NAME_STOPWORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "of", "official", "site",
    "the", "to", "with", "home", "welcome", "restaurant", "restaurants", "hotel", "hotels",
    "company", "business", "services", "service", "ghana", "accra",
}
_DOMAIN_STOPWORDS = {
    "www", "com", "org", "net", "co", "io", "ai", "biz", "info", "app", "site",
    "gh", "uk", "us", "ca", "au", "de", "fr", "ng", "za",
}


def _validated_http_url(value: Any) -> Tuple[Optional[str], Optional[str]]:
    raw = str(value or "").strip()
    if not raw or raw != unquote(raw) and unquote(raw).lower().startswith(("javascript:", "data:", "file:")):
        return None, None
    try:
        parsed = urlparse(raw)
        hostname = parsed.hostname
        _ = parsed.port
    except (TypeError, ValueError):
        return None, None
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return None, None
    if parsed.username or parsed.password or any(char.isspace() for char in raw):
        return None, None
    host = hostname.rstrip(".").lower()
    if not host:
        return None, None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None, None
        labels = ascii_host.split(".")
        if len(labels) < 2 or any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not re.fullmatch(r"[a-z0-9-]+", label, re.IGNORECASE)
            for label in labels
        ):
            return None, None
        host = ascii_host.lower()
    return raw, host


def _generic_title(title: str) -> bool:
    return any(pattern.search(title) for pattern in _GENERIC_TITLE_PATTERNS)


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2 and token not in _NAME_STOPWORDS
    }


def _domain_matches_name(hostname: str, title: str) -> bool:
    name_tokens = _tokens(title)
    domain_labels = {
        re.sub(r"[^a-z0-9]", "", label.lower())
        for label in hostname.split(".")
        if label.lower() not in _DOMAIN_STOPWORDS
    }
    domain_labels.discard("")
    if not domain_labels or not name_tokens:
        return False
    return any(
        token in label or label in token
        for token in name_tokens
        for label in domain_labels
    )


def qualify_business_entity(candidate: Dict[str, Any]) -> BusinessEntityQualification:
    """Classify one normalized discovery result without enriching or inventing data."""
    title = str(candidate.get("name") or candidate.get("title") or "").strip()
    category = str(candidate.get("category") or "").strip() or None
    location = str(candidate.get("city") or candidate.get("location") or "").strip() or None
    description = str(candidate.get("description") or candidate.get("snippet") or "").strip()
    source_url, hostname = _validated_http_url(
        candidate.get("source_url") or candidate.get("website") or candidate.get("url")
    )
    reasons = []

    def result(status, source_type, qualified=False):
        return BusinessEntityQualification(
            status=status,
            source_type=source_type,
            qualified=qualified,
            business_name=title or None,
            business_category=category,
            business_location=location,
            business_domain=hostname,
            source_url=source_url,
            qualification_reasons=reasons,
        )

    if not title:
        reasons.append("BUSINESS_NAME_MISSING")
        return result(BusinessEntityQualificationStatus.INSUFFICIENT_EVIDENCE, BusinessSourceType.UNKNOWN)
    if not source_url or not hostname:
        reasons.append("SOURCE_URL_INVALID")
        return result(BusinessEntityQualificationStatus.INSUFFICIENT_EVIDENCE, BusinessSourceType.UNKNOWN)

    parsed = urlparse(source_url)
    path = parsed.path or "/"
    if _REPORT_PATH.search(path) or re.search(r"\b(?:market|industry|research)\s+report\b", title, re.IGNORECASE):
        reasons.append("REPORT_OR_RESEARCH_PAGE")
        return result(BusinessEntityQualificationStatus.NON_BUSINESS_SOURCE, BusinessSourceType.REPORT)
    if _ARTICLE_PATH.search(path):
        reasons.append("ARTICLE_OR_INFORMATIONAL_PAGE")
        return result(BusinessEntityQualificationStatus.NON_BUSINESS_SOURCE, BusinessSourceType.ARTICLE)
    if _SEARCH_PATH.search(path) or parsed.query.lower().startswith(("q=", "query=", "search=")):
        reasons.append("SEARCH_RESULTS_PAGE")
        return result(BusinessEntityQualificationStatus.NON_BUSINESS_SOURCE, BusinessSourceType.SEARCH_PAGE)
    if _generic_title(title):
        reasons.append("GENERIC_LIST_OR_INFORMATIONAL_TITLE")
        return result(BusinessEntityQualificationStatus.UNVERIFIED, BusinessSourceType.AGGREGATOR)

    name_tokens = _tokens(title)
    if not name_tokens:
        reasons.append("BUSINESS_NAME_NOT_SPECIFIC")
        return result(BusinessEntityQualificationStatus.UNVERIFIED, BusinessSourceType.UNKNOWN)
    has_business_signal = bool(category or _BUSINESS_SIGNAL.search(description) or _BUSINESS_SIGNAL.search(title))
    if not has_business_signal:
        reasons.append("BUSINESS_RELEVANCE_SIGNAL_MISSING")
        return result(BusinessEntityQualificationStatus.INSUFFICIENT_EVIDENCE, BusinessSourceType.UNKNOWN)

    if _SOCIAL_HOST.search(hostname):
        reasons.append("BUSINESS_SPECIFIC_SOCIAL_PROFILE")
        return result(BusinessEntityQualificationStatus.LIKELY_BUSINESS, BusinessSourceType.SOCIAL_PROFILE, True)

    official = _domain_matches_name(hostname, title)
    if official:
        reasons.extend(("SPECIFIC_BUSINESS_NAME", "DOMAIN_MATCHES_BUSINESS_NAME", "BUSINESS_RELEVANCE_PRESENT"))
        return result(BusinessEntityQualificationStatus.VERIFIED_BUSINESS, BusinessSourceType.OFFICIAL_BUSINESS_SITE, True)

    individual_profile = bool(_PROFILE_PATH.search(path)) and len([part for part in path.split("/") if part]) >= 2
    if _DIRECTORY_PATH.search(path):
        if individual_profile and description:
            reasons.extend(("SPECIFIC_BUSINESS_NAME", "INDIVIDUAL_DIRECTORY_PROFILE", "BUSINESS_RELEVANCE_PRESENT"))
            return result(BusinessEntityQualificationStatus.LIKELY_BUSINESS, BusinessSourceType.DIRECTORY, True)
        reasons.append("GENERIC_DIRECTORY_PAGE")
        return result(BusinessEntityQualificationStatus.UNVERIFIED, BusinessSourceType.DIRECTORY)
    if _MARKETPLACE_PATH.search(path):
        if individual_profile and description:
            reasons.extend(("SPECIFIC_BUSINESS_NAME", "INDIVIDUAL_MARKETPLACE_PROFILE", "BUSINESS_RELEVANCE_PRESENT"))
            return result(BusinessEntityQualificationStatus.LIKELY_BUSINESS, BusinessSourceType.MARKETPLACE, True)
        reasons.append("GENERIC_MARKETPLACE_PAGE")
        return result(BusinessEntityQualificationStatus.UNVERIFIED, BusinessSourceType.MARKETPLACE)
    if individual_profile and description:
        reasons.extend(("SPECIFIC_BUSINESS_NAME", "INDIVIDUAL_BUSINESS_PROFILE", "BUSINESS_RELEVANCE_PRESENT"))
        return result(BusinessEntityQualificationStatus.LIKELY_BUSINESS, BusinessSourceType.BUSINESS_PROFILE, True)

    reasons.append("SOURCE_ENTITY_CORRESPONDENCE_UNPROVEN")
    return result(BusinessEntityQualificationStatus.UNVERIFIED, BusinessSourceType.UNKNOWN)


def is_entity_qualified(value: Any) -> bool:
    """Accept only explicitly qualified deterministic qualification records."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, dict) or value.get("qualified") is not True:
        return False
    return value.get("status") in {
        BusinessEntityQualificationStatus.VERIFIED_BUSINESS.value,
        BusinessEntityQualificationStatus.LIKELY_BUSINESS.value,
    }
