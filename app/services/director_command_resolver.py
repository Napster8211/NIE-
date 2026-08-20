"""Deterministic Director command intent and authority resolution.

This module is the single source of truth for Director command semantics. It
uses a deliberately small clause parser: negated constraint clauses are masked
first, then affirmative intents are resolved using explicit precedence.
"""
from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Any, Dict, Iterable, Match, Optional, Tuple


class DirectorCommandClass(str, Enum):
    AUDIT = "AUDIT"
    MISSION_CREATE = "MISSION_CREATE"
    MISSION_CREATE_EXECUTE = "MISSION_CREATE_EXECUTE"
    MISSION_EXECUTE = "MISSION_EXECUTE"
    MISSION_INSPECT = "MISSION_INSPECT"
    MISSION_STATUS = "MISSION_STATUS"
    MISSION_CONTROL = "MISSION_CONTROL"
    OBJECTIVE_CREATE = "OBJECTIVE_CREATE"
    OBJECTIVE_INSPECT = "OBJECTIVE_INSPECT"
    EXECUTIVE_ACTION = "EXECUTIVE_ACTION"
    AGENT_SESSION_START = "AGENT_SESSION_START"
    STRATEGIC_DECISION = "STRATEGIC_DECISION"
    STATUS = "STATUS"
    APPROVAL_INSPECT = "APPROVAL_INSPECT"
    APPROVAL_APPROVE = "APPROVAL_APPROVE"
    APPROVAL_REJECT = "APPROVAL_REJECT"
    APPROVAL_REVOKE = "APPROVAL_REVOKE"
    UNKNOWN = "UNKNOWN"


DIRECTOR_RESOLUTION_VERSION = "director-command-intent-v4"
DIRECTOR_COMMAND_UNRESOLVED = "DIRECTOR_COMMAND_UNRESOLVED"

MISSION_MUTATION_CLASSES = frozenset({
    DirectorCommandClass.MISSION_CREATE.value,
    DirectorCommandClass.MISSION_CREATE_EXECUTE.value,
    DirectorCommandClass.MISSION_EXECUTE.value,
    DirectorCommandClass.MISSION_CONTROL.value,
})

MISSION_COMMAND_CLASSES = frozenset({
    *MISSION_MUTATION_CLASSES,
    DirectorCommandClass.MISSION_INSPECT.value,
    DirectorCommandClass.MISSION_STATUS.value,
})

OBJECTIVE_MUTATION_CLASSES = frozenset({
    DirectorCommandClass.OBJECTIVE_CREATE.value,
})

OBJECTIVE_COMMAND_CLASSES = frozenset({
    *OBJECTIVE_MUTATION_CLASSES,
    DirectorCommandClass.OBJECTIVE_INSPECT.value,
})

APPROVAL_MUTATION_CLASSES = frozenset({
    DirectorCommandClass.APPROVAL_APPROVE.value,
    DirectorCommandClass.APPROVAL_REJECT.value,
    DirectorCommandClass.APPROVAL_REVOKE.value,
})

APPROVAL_COMMAND_CLASSES = frozenset({
    *APPROVAL_MUTATION_CLASSES,
    DirectorCommandClass.APPROVAL_INSPECT.value,
})

_MISSION_ID_RE = re.compile(r"\bmis_[a-z0-9]{3,64}\b", re.IGNORECASE)
_OBJECTIVE_ID_RE = re.compile(r"\bobj_[a-z0-9]{8,64}\b", re.IGNORECASE)
_APPROVAL_ID_RE = re.compile(r"\bapp_[a-z0-9]{8,64}\b", re.IGNORECASE)
_MISSION_NOUN_RE = re.compile(r"\b(?:mission|canary)\b", re.IGNORECASE)

_EXPLICIT_APPROVAL_ACTION_RE = re.compile(
    r"\b(?:approve|authorize|reject|decline|deny|revoke|withdraw|rescind|list|show|inspect)\s+(?:pending\s+)?approvals?\b",
    re.IGNORECASE
)
_APPROVE_RE = re.compile(r"\b(?:approve|authorize|consent)\b", re.IGNORECASE)
_REJECT_RE = re.compile(r"\b(?:reject|decline|deny)\b", re.IGNORECASE)
_REVOKE_RE = re.compile(r"\b(?:revoke|withdraw|rescind)\b", re.IGNORECASE)
_REASON_RE = re.compile(r"\bbecause\s+(.+)$", re.IGNORECASE)

_CREATE_RE = re.compile(
    r"\b(?:create|start|launch|initialize|initiate|bootstrap|establish)\b",
    re.IGNORECASE,
)
_EXECUTE_RE = re.compile(
    r"\b(?:execute|run|continue|resume(?:\s+execution)?)\b",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(r"\b(?:pause|resume|cancel|stop)\b", re.IGNORECASE)
_AUDIT_RE = re.compile(
    r"\b(?:audit|inspect|inspection|review|diagnose|analy[sz]e|validate)\b",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(
    r"\b(?:status|progress|history|lineage|provenance|show\s+me|how\s+is|what\s+is)\b",
    re.IGNORECASE,
)
_EXECUTIVE_ACTION_RE = re.compile(
    r"\b(?:increase|move|find|deploy|resolve|build|prepare|publish|send)\b",
    re.IGNORECASE,
)
_SESSION_RE = re.compile(
    r"\b(?:bring|talk\s+to|speak\s+with|open|let\s+me|back\s+to)\b",
    re.IGNORECASE,
)
_BRIEFING_RE = re.compile(
    r"\b(?:briefing|update|performing|current\s+state|summary|happening|health)\b",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(
    r"\b(?:should\s+we|should\s+i|evaluate\s+whether|ask)\b|\?",
    re.IGNORECASE,
)
_DIRECTOR_ADDRESS_RE = re.compile(
    r"^\s*(?:director|ceo)\b|\b(?:back\s+to\s+director|director\s+intelligence)\b",
    re.IGNORECASE,
)
_EXPLICIT_OBJECTIVE_CREATE_RE = re.compile(
    r"\b(?:create|set|establish|define)\s+(?:a\s+)?(?:company\s+|business\s+)?objective\b",
    re.IGNORECASE,
)
_MEASURABLE_OBJECTIVE_CREATE_RE = re.compile(
    r"^\s*(?:(?:director|ceo)\s*[,\-:]?\s*)?"
    r"(?:acquire|secure|obtain|generate|gain|increase|reduce|grow)\s+\d+\b",
    re.IGNORECASE,
)

# Negation is scoped to a compact clause and stops at punctuation. This makes
# "do not deploy or send anything" one constraint while allowing a later
# sentence or comma-delimited imperative to remain affirmative.
_NEGATED_SPAN_PATTERNS = (
    re.compile(
        r"\b(?:do\s+not|don't|never|must\s+not|shall\s+not|cannot|can't|not\s+authorized\s+to)\b"
        r"[^.!?;,\r\n]*",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwithout\s+(?:deploying|sending|publishing|executing|creating|starting|launching|"
        r"modifying|changing|writing)\b[^.!?;,\r\n]*",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:this\s+is\s+)?not\s+(?:an?\s+|another\s+)?(?:audit|review|inspection)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bno\s+(?:external\s+)?(?:communication|communications|outreach|deployment|"
        r"deployments|publishing|publication)\b",
        re.IGNORECASE,
    ),
)

_READ_ONLY_CONSTRAINT_RE = re.compile(
    r"\b(?:read[ -]?only|do\s+not\s+(?:modify|change)|without\s+(?:modifying|changing))\b",
    re.IGNORECASE,
)
_HYPOTHETICAL_PREFIX_RE = re.compile(
    r"\b(?:should|could|would|might|whether)\b[^.!?;\r\n]{0,100}$",
    re.IGNORECASE,
)


def _query_digest(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _mask_spans(text: str, spans: Iterable[Tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _affirmative_text(query: str) -> str:
    spans = []
    for pattern in _NEGATED_SPAN_PATTERNS:
        spans.extend(match.span() for match in pattern.finditer(query))
    return _mask_spans(query.lower(), spans)


def _iter_clauses(text: str) -> Iterable[Tuple[int, str]]:
    for match in re.finditer(r"[^.!?;\r\n]+", text):
        yield match.start(), match.group(0)


def _first_match(pattern: re.Pattern[str], text: str) -> Optional[Match[str]]:
    return pattern.search(text)


def _find_mission_creation(text: str) -> Optional[Tuple[int, Match[str]]]:
    """Find an affirmative imperative mission creation, excluding hypotheticals."""
    for offset, clause in _iter_clauses(text):
        if not _MISSION_NOUN_RE.search(clause):
            continue
        for match in _CREATE_RE.finditer(clause):
            if _HYPOTHETICAL_PREFIX_RE.search(clause[:match.start()]):
                continue
            return offset + match.start(), match
    return None


def _find_mission_execution(text: str, mission_id: Optional[str]) -> Optional[Match[str]]:
    if not (mission_id or _MISSION_NOUN_RE.search(text)):
        return None
    for _, clause in _iter_clauses(text):
        for match in _EXECUTE_RE.finditer(clause):
            if _HYPOTHETICAL_PREFIX_RE.search(clause[:match.start()]):
                continue
            return match
    return None


def _mission_result_audit_follows_creation(text: str, create_position: int) -> bool:
    audit = _AUDIT_RE.search(text, create_position + 1)
    if not audit:
        return False
    sequence = text[create_position:audit.end() + 100]
    return bool(re.search(
        r"\b(?:then|after(?:ward|wards)?|after\s+that|result|once)\b",
        sequence,
        re.IGNORECASE,
    ))


def _base_route(
    query: str, mission_id: Optional[str], objective_id: Optional[str]
) -> Dict[str, Any]:
    return {
        "resolution_version": DIRECTOR_RESOLUTION_VERSION,
        "query_digest": _query_digest(query),
        "director_addressed": bool(_DIRECTOR_ADDRESS_RE.search(query)),
        "mission_intent": False,
        "intent_category": DirectorCommandClass.UNKNOWN.value,
        "operating_mode": "UNKNOWN",
        "command_class": DirectorCommandClass.UNKNOWN.value,
        "authority_mode": "READ_ONLY",
        "execution_context": "STANDARD",
        "mission_id": mission_id,
        "objective_id": objective_id,
        "mission_action": None,
        "mission_read_only": True,
        "mutation_requested": False,
        "mission_creation_requested": False,
        "mission_execution_requested": False,
        "objective_intent": False,
        "objective_action": None,
        "objective_read_only": True,
        "objective_creation_requested": False,
        "constraint_only": False,
        "error_code": DIRECTOR_COMMAND_UNRESOLVED,
    }


def _classify_director_intent(query: str) -> Dict[str, Any]:
    clean_query = query.lower().strip()
    affirmative = _affirmative_text(clean_query)
    has_negated_constraint = affirmative != clean_query
    
    mission_match = _MISSION_ID_RE.search(clean_query)
    mission_id = mission_match.group(0).lower() if mission_match else None
    
    objective_match = _OBJECTIVE_ID_RE.search(clean_query)
    objective_id = objective_match.group(0).lower() if objective_match else None
    
    approval_match = _APPROVAL_ID_RE.search(clean_query)
    approval_id = approval_match.group(0).lower() if approval_match else None
    approval_context = bool(approval_id or _EXPLICIT_APPROVAL_ACTION_RE.search(affirmative))
    
    route = _base_route(query, mission_id, objective_id)

    create = _find_mission_creation(affirmative)
    execute = _find_mission_execution(affirmative, mission_id)
    control = _CONTROL_RE.search(affirmative) if (mission_id or _MISSION_NOUN_RE.search(affirmative)) else None
    audit = _AUDIT_RE.search(affirmative)
    status = _STATUS_RE.search(affirmative)
    mission_context = bool(mission_id or _MISSION_NOUN_RE.search(affirmative))
    objective_create = bool(
        _EXPLICIT_OBJECTIVE_CREATE_RE.search(affirmative)
        or _MEASURABLE_OBJECTIVE_CREATE_RE.search(affirmative)
    )
    objective_context = bool(
        objective_id or re.search(r"\b(?:company\s+|business\s+)?objectives?\b", affirmative)
    )

    # 1. Explicit Approval Control Plane Action
    if approval_context:
        approve_match = _APPROVE_RE.search(affirmative)
        reject_match = _REJECT_RE.search(affirmative)
        revoke_match = _REVOKE_RE.search(affirmative)
        audit_match = audit or status or "list" in affirmative

        reason_match = _REASON_RE.search(clean_query)
        approval_reason = reason_match.group(1).strip() if reason_match else None

        if approve_match:
            route.update(
                operating_mode="HUMAN DECISION CONTROL MODE",
                command_class=DirectorCommandClass.APPROVAL_APPROVE.value,
                intent_category="APPROVAL_MUTATION",
                authority_mode="APPROVAL_MUTATION",
                execution_context="HUMAN_APPROVAL",
                mission_read_only=True,
                mutation_requested=True,
                error_code=None,
            )
        elif reject_match:
            route.update(
                operating_mode="HUMAN DECISION CONTROL MODE",
                command_class=DirectorCommandClass.APPROVAL_REJECT.value,
                intent_category="APPROVAL_MUTATION",
                authority_mode="APPROVAL_MUTATION",
                execution_context="HUMAN_APPROVAL",
                mission_read_only=True,
                mutation_requested=True,
                error_code=None,
            )
        elif revoke_match:
            route.update(
                operating_mode="HUMAN DECISION CONTROL MODE",
                command_class=DirectorCommandClass.APPROVAL_REVOKE.value,
                intent_category="APPROVAL_MUTATION",
                authority_mode="APPROVAL_MUTATION",
                execution_context="HUMAN_APPROVAL",
                mission_read_only=True,
                mutation_requested=True,
                error_code=None,
            )
        else:
            route.update(
                operating_mode="APPROVAL STATUS MODE",
                command_class=DirectorCommandClass.APPROVAL_INSPECT.value,
                intent_category="APPROVAL_INSPECT",
                authority_mode="READ_ONLY",
                execution_context="HUMAN_APPROVAL",
                mission_read_only=True,
                error_code=None,
            )
        route["approval_id"] = approval_id
        route["approval_reason"] = approval_reason

    # 2. Explicit mission creation/execution always owns the primary intent.
    elif create:
        create_position, _ = create
        execute_requested = bool(execute) or _mission_result_audit_follows_creation(
            affirmative, create_position
        )
        command_class = (
            DirectorCommandClass.MISSION_CREATE_EXECUTE.value
            if execute_requested
            else DirectorCommandClass.MISSION_CREATE.value
        )
        route.update(
            operating_mode="MISSION CREATION MODE",
            command_class=command_class,
            intent_category="MISSION_EXECUTION" if execute_requested else "MISSION_CREATE",
            authority_mode="MISSION_MUTATION",
            mission_read_only=False,
            mutation_requested=True,
            mission_creation_requested=True,
            mission_execution_requested=execute_requested,
            mission_intent=True,
            error_code=None,
        )
    elif execute:
        route.update(
            operating_mode="EXECUTIVE COMMAND MODE",
            command_class=DirectorCommandClass.MISSION_EXECUTE.value,
            intent_category="MISSION_EXECUTION",
            authority_mode="EXECUTION_AUTHORIZED",
            execution_context="MISSION",
            mission_action="CONTINUE",
            mission_read_only=False,
            mutation_requested=True,
            mission_execution_requested=True,
            mission_intent=True,
            error_code=None,
        )

    # 3. First-class company-objective intake and read-only inspection. This
    # creates control-plane state only; it never launches mission work.
    elif objective_create:
        route.update(
            operating_mode="OBJECTIVE CREATION MODE",
            command_class=DirectorCommandClass.OBJECTIVE_CREATE.value,
            intent_category=DirectorCommandClass.OBJECTIVE_CREATE.value,
            authority_mode="OBJECTIVE_MUTATION",
            execution_context="COMPANY_OBJECTIVE",
            mission_read_only=False,
            mutation_requested=True,
            objective_intent=True,
            objective_action="CREATE",
            objective_read_only=False,
            objective_creation_requested=True,
            error_code=None,
        )
    elif objective_context and (audit or status):
        route.update(
            operating_mode="OBJECTIVE STATUS MODE",
            command_class=DirectorCommandClass.OBJECTIVE_INSPECT.value,
            intent_category=DirectorCommandClass.OBJECTIVE_INSPECT.value,
            execution_context="COMPANY_OBJECTIVE",
            objective_intent=True,
            objective_action="STATUS" if objective_id else "LIST",
            error_code=None,
        )

    # 4. Explicit mission control.
    elif control:
        action = control.group(0).upper()
        route.update(
            operating_mode="MISSION CONTROL MODE",
            command_class=DirectorCommandClass.MISSION_CONTROL.value,
            intent_category=DirectorCommandClass.MISSION_CONTROL.value,
            authority_mode="MISSION_MUTATION",
            execution_context="MISSION",
            mission_action=action,
            mission_read_only=False,
            mutation_requested=True,
            mission_execution_requested=action == "RESUME",
            mission_intent=True,
            error_code=None,
        )

    # 5. Explicit inspection/audit. A mission-specific inspection remains an
    # AUDIT primary class (and READ_ONLY) while retaining its mission target.
    elif audit:
        route.update(
            operating_mode="AUDIT MODE",
            command_class=DirectorCommandClass.AUDIT.value,
            intent_category=(
                DirectorCommandClass.MISSION_INSPECT.value
                if mission_context
                else DirectorCommandClass.AUDIT.value
            ),
            execution_context="MISSION" if mission_context else "STANDARD",
            mission_action="INSPECT_EXECUTION" if mission_id else "AUDIT",
            mission_intent=mission_context,
            error_code=None,
        )
    elif status and mission_context:
        route.update(
            operating_mode="EXECUTIVE COMMAND MODE",
            command_class=DirectorCommandClass.MISSION_STATUS.value,
            intent_category=DirectorCommandClass.MISSION_INSPECT.value,
            execution_context="MISSION",
            mission_action="STATUS",
            mission_intent=True,
            error_code=None,
        )

    # 6. Other Director actions retain their existing scoped classification.
    elif _SESSION_RE.search(affirmative):
        route.update(
            operating_mode=(
                "COLLABORATIVE AGENT SESSION MODE"
                if " and " in affirmative
                else "INTERACTIVE AGENT SESSION MODE"
            ),
            command_class=DirectorCommandClass.AGENT_SESSION_START.value,
            intent_category=DirectorCommandClass.EXECUTIVE_ACTION.value,
            authority_mode="SESSION_MUTATION",
            mission_read_only=False,
            mutation_requested=True,
            error_code=None,
        )
    elif _QUESTION_RE.search(affirmative):
        route.update(
            operating_mode="STRATEGIC DECISION MODE",
            command_class=DirectorCommandClass.STRATEGIC_DECISION.value,
            intent_category=DirectorCommandClass.STRATEGIC_DECISION.value,
            authority_mode="ANALYSIS_ONLY",
            error_code=None,
        )
    elif _BRIEFING_RE.search(affirmative):
        route.update(
            operating_mode="EXECUTIVE BRIEFING MODE",
            command_class=DirectorCommandClass.STATUS.value,
            intent_category=DirectorCommandClass.STATUS.value,
            error_code=None,
        )
    elif _EXECUTIVE_ACTION_RE.search(affirmative):
        route.update(
            operating_mode="EXECUTIVE COMMAND MODE",
            command_class=DirectorCommandClass.EXECUTIVE_ACTION.value,
            intent_category=DirectorCommandClass.EXECUTIVE_ACTION.value,
            authority_mode="EXECUTION_AUTHORIZED",
            mission_read_only=False,
            mutation_requested=True,
            error_code=None,
        )
    else:
        route["constraint_only"] = has_negated_constraint
        if _READ_ONLY_CONSTRAINT_RE.search(clean_query):
            route["constraint_only"] = True

    return route


def resolve_director_command(query: str) -> Dict[str, Any]:
    """Resolve command class and its deterministic runtime authority once."""
    route = _classify_director_intent(query)
    mission_mutation_allowed = route["command_class"] in MISSION_MUTATION_CLASSES
    objective_mutation_allowed = route["command_class"] in OBJECTIVE_MUTATION_CLASSES
    approval_mutation_allowed = route["command_class"] in APPROVAL_MUTATION_CLASSES
    internal_mutation_allowed = mission_mutation_allowed or objective_mutation_allowed or approval_mutation_allowed
    
    authority_scope = (
        "INTERNAL_MISSION_STATE"
        if mission_mutation_allowed
        else "INTERNAL_COMPANY_OBJECTIVE_STATE"
        if objective_mutation_allowed
        else "HUMAN_APPROVAL_STATE"
        if approval_mutation_allowed
        else "NONE"
    )
    
    route.update({
        "read_only": not internal_mutation_allowed,
        "mutation_allowed": internal_mutation_allowed,
        "write_allowed": internal_mutation_allowed,
        "internal_mission_mutation_allowed": mission_mutation_allowed,
        "internal_objective_mutation_allowed": objective_mutation_allowed,
        "internal_approval_mutation_allowed": approval_mutation_allowed,
        "access_mode": (
            "MISSION_INTERNAL_MUTATION"
            if mission_mutation_allowed
            else "OBJECTIVE_INTERNAL_MUTATION"
            if objective_mutation_allowed
            else "APPROVAL_INTERNAL_MUTATION"
            if approval_mutation_allowed
            else "READ_ONLY"
        ),
        "authority_scope": authority_scope,
        "authority_mode": route["authority_mode"] if internal_mutation_allowed else "READ_ONLY",
        "mission_creation_allowed": bool(
            mission_mutation_allowed and route["mission_creation_requested"]
        ),
        "mission_execution_allowed": bool(
            mission_mutation_allowed and route["mission_execution_requested"]
        ),
        "objective_creation_allowed": bool(
            objective_mutation_allowed and route["objective_creation_requested"]
        ),
        "external_api_allowed": False,
        "external_side_effect_allowed": False,
    })
    return route


def is_canonical_director_resolution(value: Dict[str, Any], query: str) -> bool:
    """Return whether ``value`` is the canonical resolution for this query."""
    return bool(
        value.get("resolution_version") == DIRECTOR_RESOLUTION_VERSION
        and value.get("query_digest") == _query_digest(query)
        and value.get("command_class")
        and "authority_scope" in value
    )


# Compatibility entry points. Both delegate to the one resolver above.
def classify_director_command(query: str) -> Dict[str, Any]:
    return resolve_director_command(query)


def resolve_director_runtime_authority(query: str) -> Dict[str, Any]:
    return resolve_director_command(query)