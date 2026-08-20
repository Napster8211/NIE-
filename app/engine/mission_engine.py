"""
NapsterTec AI - Enterprise Mission Engine
Module: app/engine/mission_engine.py
"""
import uuid
import logging
import json
import os
import asyncio
import ctypes
import hashlib
import tempfile
import threading
import re
from contextlib import contextmanager
from typing import Dict, Any, List, Optional, Tuple
from pydantic import BaseModel, Field
from datetime import datetime, timezone, timedelta
from app.schemas.shared_artifacts import (
    MissionArtifact, MissionMilestone, ApprovalRequest, ApprovalStatus, ApprovalType
)
from app.engine.event_bus import event_bus, BusinessEvent
from app.schemas.evidence import (
    EvidenceSource,
    is_simulation_evidence,
    normalize_evidence_source,
    qualifies_for_production_success,
)
from app.repositories.approval_repository import approval_repository
from app.services.business_entity_qualification import is_entity_qualified
from app.schemas.lead import BusinessDiscoveryQueryMode, BusinessDiscoveryScope

logger = logging.getLogger(__name__)

MISSION_FILE = ".napstertec_missions.json"
MAX_PLAN_LOOPS = 5
MAX_EXECUTION_RETRIES = 3
MAX_REPEATED_STRATEGIES = 2
MAX_OUTREACH_BATCHES = 3
MAX_ZERO_PROGRESS_PLANS = 3
MAX_STALL_RECOVERIES = 2
STALL_TIMEOUT_SECONDS = 300

STRUCTURAL_CANARY = "STRUCTURAL_CANARY"
LIVE_EVIDENCE_CANARY = "LIVE_EVIDENCE_CANARY"
QUALIFIED_LEAD_CANARY = "QUALIFIED_LEAD_CANARY"
PRODUCTION_BUSINESS_SUCCESS = "PRODUCTION_BUSINESS_SUCCESS"

_SAFE_SOURCE_METADATA_FIELDS = {
    "provider", "endpoint", "retrieval_type", "request_succeeded",
    "request_count", "requested_at", "result_count", "query", "max_results",
    "raw_result_count", "normalized_result_count", "usable_result_count",
    "qualified_artifact_target", "candidate_scan_limit", "query_mode", "candidate_count_examined",
    "qualified_candidate_index", "candidate_diagnostics",
}
_TERMINAL_ENTITY_STATUSES = {
    "UNVERIFIED", "NON_BUSINESS_SOURCE", "INSUFFICIENT_EVIDENCE",
}
_TERMINAL_ENTITY_SOURCE_TYPES = {
    "AGGREGATOR", "ARTICLE", "REPORT", "SEARCH_PAGE",
}
_TRANSIENT_RETRYABLE_MARKERS = {
    "TIMEOUT", "CONNECTION_FAILURE", "CONNECTIONERROR", "HTTP_429", "HTTP 429",
    "RATE_LIMIT", "TEMPORARY_PROVIDER_FAILURE", "TRANSIENT_WORKER_FAILURE",
}


def _sanitize_failure_evidence(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, dict):
        return {}
    source = normalize_evidence_source(value.get("evidence_source"))
    entity = dict(value.get("entity_qualification") or {})
    safe_entity = {
        key: entity.get(key)
        for key in {
            "status", "source_type", "qualified", "business_name",
            "business_category", "business_location", "business_domain",
            "source_url", "qualification_reasons",
        }
        if key in entity
    }
    return {
        "evidence_source": source.value,
        "simulation_evidence": bool(
            value.get("simulation_evidence", is_simulation_evidence(source))
        ),
        "source_provider": value.get("source_provider"),
        "source_metadata": {
            key: item
            for key, item in dict(value.get("source_metadata") or {}).items()
            if key in _SAFE_SOURCE_METADATA_FIELDS
        },
        "source_url": value.get("source_url"),
        "source_type": value.get("source_type") or safe_entity.get("source_type"),
        "business_name": value.get("business_name") or safe_entity.get("business_name"),
        "business_category": value.get("business_category") or safe_entity.get("business_category"),
        "business_location": value.get("business_location") or safe_entity.get("business_location"),
        "business_domain": value.get("business_domain") or safe_entity.get("business_domain"),
        "entity_qualification": safe_entity,
        "qualification_reasons": list(
            value.get("qualification_reasons")
            or safe_entity.get("qualification_reasons")
            or []
        ),
    }

ARTIFACT_MISSION_TARGETS = {
    "LeadArtifact": ("lead_intelligence", "Lead Research"),
    "WebsiteArtifact": ("website_intelligence", "Website Analysis"),
    "OpportunityArtifact": ("opportunity_intelligence", "Opportunity Analysis"),
    "BusinessSolutionArtifact": ("business_solution_architect", "Business Solution"),
    "ProposalArtifact": ("proposal_intelligence", "Proposal"),
    "VisualizationArtifact": ("solution_visualization_architect", "Solution Visualization"),
    "TechnicalArchitectureArtifact": ("technical_solution_architect", "Technical Architecture"),
    "ImplementationArtifact": ("coding_intelligence", "Implementation"),
    "ReviewArtifact": ("engineering_review", "Engineering Review"),
    "DeploymentArtifact": ("deployment_intelligence", "Deployment"),
    "ClientAcquisitionArtifact": ("client_acquisition", "Client Acquisition"),
    "ContentArtifact": ("content_intelligence", "Content"),
    "SocialArtifact": ("social_intelligence", "Social"),
    "CampaignArtifact": ("campaign_intelligence", "Campaign"),
    "MarketingAnalyticsArtifact": ("marketing_analytics", "Marketing Analytics"),
    "PublishingArtifact": ("publishing_intelligence", "Publishing"),
    "SalesArtifact": ("sales_intelligence", "Sales"),
    "RevenueArtifact": ("revenue_intelligence", "Revenue"),
    "CommunicationArtifact": ("communication_intelligence", "Communication"),
    "CustomerSuccessArtifact": ("customer_success_intelligence", "Customer Success"),
    "BusinessOperationsArtifact": ("business_operations_intelligence", "Business Operations"),
    "FinanceArtifact": ("finance_intelligence", "Finance"),
}

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}
_NUMBER_TOKEN = r"\d{1,3}|" + "|".join(_NUMBER_WORDS)


def _parse_requested_count(text: str, subject_pattern: str, default: int) -> int:
    match = re.search(
        rf"\b(?:exactly\s+)?(?P<count>{_NUMBER_TOKEN})\s+(?:new\s+|valid\s+|verified\s+)?(?:{subject_pattern})s?\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return default
    token = match.group("count").lower()
    return max(1, int(token) if token.isdigit() else _NUMBER_WORDS[token])


def _is_structural_canary(raw_query: str, objective: str) -> bool:
    combined = f"{raw_query}\n{objective}"
    return bool(re.search(
        r"\b(?:canary|simulation|simulated|test\s+mission|controlled\s+test|test\s+business\s+prospect)\b",
        combined,
        re.IGNORECASE,
    ))


def _is_live_evidence_canary(raw_query: str, objective: str) -> bool:
    combined = f"{raw_query}\n{objective}"
    return bool(
        re.search(r"\bcanary\b", combined, re.IGNORECASE)
        and re.search(
            r"\b(?:live[-\s]+evidence|live[-\s]+external|LIVE_EXTERNAL|genuine[-\s]+external|real[-\s]+business[-\s]+evidence)\b",
            combined,
            re.IGNORECASE,
        )
    )


def _is_qualified_lead_canary(raw_query: str, objective: str) -> bool:
    combined = f"{raw_query}\n{objective}"
    return bool(
        re.search(r"\bcanary\b", combined, re.IGNORECASE)
        and re.search(
            r"\b(?:qualified[-\s]+lead|qualified[-\s]+prospect|business[-\s]+entity[-\s]+qualification|verified[-\s]+business[-\s]+entity)\b",
            combined,
            re.IGNORECASE,
        )
    )


def _qualified_lead_canary_discovery_scope() -> Dict[str, Any]:
    """Return the bounded, test-only provider scope for this canary mode."""
    return BusinessDiscoveryScope(
        category="restaurant",
        location="Accra Ghana",
        max_results=1,
        candidate_scan_limit=5,
        query_mode=BusinessDiscoveryQueryMode.QUALIFIED_ENTITY_SEARCH,
    ).model_dump(mode="json")


def _artifact_type_from_objective(objective: str) -> Optional[str]:
    mentioned = re.findall(r"\b[A-Za-z][A-Za-z0-9]*Artifact\b", objective)
    by_lower = {name.lower(): name for name in ARTIFACT_MISSION_TARGETS}
    for candidate in mentioned:
        canonical = by_lower.get(candidate.lower())
        if canonical:
            return canonical
    return None


def derive_mission_specification(raw_query: str, objective: str) -> Dict[str, Any]:
    """Derive a bounded typed mission plan from the requested outcome."""
    artifact_type = _artifact_type_from_objective(objective)
    qualified_lead_canary = bool(
        artifact_type == "LeadArtifact" and _is_qualified_lead_canary(raw_query, objective)
    )
    live_evidence_canary = bool(
        not qualified_lead_canary and _is_live_evidence_canary(raw_query, objective)
    )
    structural_canary = bool(
        _is_structural_canary(raw_query, objective)
        and not live_evidence_canary
        and not qualified_lead_canary
    )

    if artifact_type:
        target_agent, title_stem = ARTIFACT_MISSION_TARGETS[artifact_type]
        required = _parse_requested_count(objective, re.escape(artifact_type), default=1)
        if artifact_type == "LeadArtifact":
            required = _parse_requested_count(
                objective,
                r"(?:test\s+)?business\s+prospect|prospect|" + re.escape(artifact_type),
                default=required,
            )
            if live_evidence_canary or qualified_lead_canary:
                required = 1
            subject = (
                "qualified live business prospect"
                if qualified_lead_canary
                else "live business prospect"
                if live_evidence_canary
                else "test business prospect"
                if structural_canary
                else "business prospect"
            )
            action = f"Research {required} {subject}{'' if required == 1 else 's'} for {artifact_type} production"
        else:
            action = f"Produce {required} verified {artifact_type}{'' if required == 1 else 's'}"
        verification_mode = (
            QUALIFIED_LEAD_CANARY
            if qualified_lead_canary
            else LIVE_EVIDENCE_CANARY
            if live_evidence_canary
            else STRUCTURAL_CANARY
            if structural_canary
            else PRODUCTION_BUSINESS_SUCCESS
        )
        discovery_scope = (
            _qualified_lead_canary_discovery_scope()
            if qualified_lead_canary
            else None
        )
        milestone = {
            "milestone_id": "m1",
            "plan_version": "v1",
            "name": f"{title_stem} & Artifact Verification",
            "status": "Pending",
            "progress": 0,
            "phase": "Artifact Production & Verification",
            "action": action,
            "target_intelligence": target_agent,
            "expected_artifact": artifact_type,
            "target_count": required,
            "success_criteria": f"{required} verified {artifact_type}{'' if required == 1 else 's'} with valid mission provenance.",
        }
        if discovery_scope:
            milestone["discovery_scope"] = dict(discovery_scope)
        return {
            "mission_type": "ARTIFACT_PRODUCTION",
            "title": f"{title_stem} {'Qualified-Lead Canary' if qualified_lead_canary else 'Live-Evidence Canary' if live_evidence_canary else 'Canary' if structural_canary else 'Mission'}",
            "verification_mode": verification_mode,
            "simulation_mode": structural_canary,
            "success_criteria": {
                "criterion": "verified_artifacts",
                "artifact_type": artifact_type,
                "required": required,
                "verification_mode": verification_mode,
                "required_evidence_source": (
                    EvidenceSource.LIVE_EXTERNAL.value
                    if live_evidence_canary or qualified_lead_canary
                    else None
                ),
                "require_simulation_evidence": (
                    False if live_evidence_canary or qualified_lead_canary else None
                ),
                "require_entity_qualification": True if qualified_lead_canary else None,
            },
            "milestones": [milestone],
            "current_phase": milestone["phase"],
            "discovery_scope": discovery_scope,
        }

    acquisition_requested = bool(re.search(
        r"\b(?:acquire|win|land|sign|onboard)\b.*\b(?:clients?|customers?)\b",
        objective,
        re.IGNORECASE,
    ))
    if not acquisition_requested:
        raise ValueError("MissionObjectiveInterpretationFailed: no supported typed outcome was found.")

    required_clients = _parse_requested_count(objective, r"(?:restaurant\s+)?(?:clients?|customers?)", default=10)
    milestones = [
        {"milestone_id": "m1", "plan_version": "v1", "name": "Discovery & Qualification", "status": "Pending", "progress": 0, "phase": "Discovery & Qualification", "action": "Discover qualified restaurant prospects", "target_intelligence": "lead_intelligence", "expected_artifact": "LeadArtifact", "target_count": required_clients},
        {"milestone_id": "m2", "plan_version": "v1", "name": "Website & Opportunity Analysis", "status": "Pending", "progress": 0, "phase": "Website & Opportunity Analysis", "action": "Analyze website and evaluate opportunities", "target_intelligence": "website_intelligence", "expected_artifact": "WebsiteArtifact", "target_count": required_clients},
        {"milestone_id": "m3", "plan_version": "v1", "name": "Opportunity Evaluation", "status": "Pending", "progress": 0, "phase": "Solution Preparation", "action": "Evaluate business opportunity & map services", "target_intelligence": "opportunity_intelligence", "expected_artifact": "OpportunityArtifact", "target_count": required_clients},
        {"milestone_id": "m4", "plan_version": "v1", "name": "Business Solution Design", "status": "Pending", "progress": 0, "phase": "Solution Preparation", "action": "Design business solution architecture", "target_intelligence": "business_solution_architect", "expected_artifact": "BusinessSolutionArtifact", "target_count": required_clients},
    ]
    return {
        "mission_type": "CLIENT_ACQUISITION",
        "title": "Strategic Business Acquisition",
        "verification_mode": PRODUCTION_BUSINESS_SUCCESS,
        "simulation_mode": False,
        "success_criteria": {
            "criterion": "verified_won_clients",
            "required": required_clients,
            "verification_mode": PRODUCTION_BUSINESS_SUCCESS,
        },
        "milestones": milestones,
        "current_phase": "Discovery & Qualification",
    }

class PersistentMission(BaseModel):
    mission_id: str
    objective_id: Optional[str] = None
    original_request: str
    title: str
    objective: str
    status: str
    priority: str
    autonomy_level: str
    progress: int
    health: str
    success_criteria_progress: str
    mission_type: str = "CLIENT_ACQUISITION"
    verification_mode: str = PRODUCTION_BUSINESS_SUCCESS
    simulation_mode: bool = False
    discovery_scope: Optional[Dict[str, Any]] = None
    mission_objective_achieved: bool = False
    current_phase: str
    current_milestone: str = "None"
    next_eligible_action: str = "None"
    auto_continue_status: str = "STOPPED"
    last_completed_delegation: str = "None"
    last_result_artifact: str = "None"
    plan_version: str = "v1"
    plan_status: str = "ACTIVE"
    historical_plans: List[Dict[str, Any]] = Field(default_factory=list)
    progression_state: str = "RUNNING"
    execution_state: str = "READY"
    dispatch_state: str = "NONE"
    stall_detector_status: str = "Clear"
    loop_safety_status: str = "Safe"
    milestones: List[Dict[str, Any]] = Field(default_factory=list)
    active_delegations: List[Dict[str, Any]] = Field(default_factory=list)
    delegation_history: List[Dict[str, Any]] = Field(default_factory=list)
    execution_requests: List[Dict[str, Any]] = Field(default_factory=list)
    worker_claims: List[Dict[str, Any]] = Field(default_factory=list)
    external_operations: List[Dict[str, Any]] = Field(default_factory=list)
    action_history: List[str] = Field(default_factory=list)
    strategy_history: List[str] = Field(default_factory=list)
    progression_decisions: List[Dict[str, Any]] = Field(default_factory=list)
    progression_materializations: List[Dict[str, Any]] = Field(default_factory=list)
    artifact_lineage: List[Dict[str, Any]] = Field(default_factory=list)
    success_criteria: Dict[str, Any] = Field(default_factory=lambda: {
        "criterion": "verified_won_clients", "required": 10
    })
    success_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    replan_count: int = 0
    retry_count: int = 0
    repeated_strategy_count: int = 0
    outreach_batch_count: int = 0
    zero_progress_count: int = 0
    stall_recovery_count: int = 0
    last_verified_success_count: int = 0
    state_revision: int = 0
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_progress_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    escalation_reason: str = "None"
    terminal_reason: str = "None"
    last_error: str = "None"
    events: List[str] = Field(default_factory=list)
    pending_approvals: List[str] = Field(default_factory=list)


_REGISTRY_MUTEX_TIMEOUT_MS = 30_000


@contextmanager
def _cross_process_registry_lock(path: str):
    """Serialize registry file access across Windows processes without polling."""
    if os.name != "nt":
        yield
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    create_mutex.restype = ctypes.c_void_p
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    wait_for_single_object.restype = ctypes.c_uint32
    release_mutex = kernel32.ReleaseMutex
    release_mutex.argtypes = (ctypes.c_void_p,)
    release_mutex.restype = ctypes.c_bool
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_bool

    normalized_path = os.path.normcase(os.path.abspath(path))
    lock_id = hashlib.sha256(normalized_path.encode("utf-8")).hexdigest()
    handle = create_mutex(None, False, f"Local\\NapsterTecMissionRegistry_{lock_id}")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    wait_result = wait_for_single_object(handle, _REGISTRY_MUTEX_TIMEOUT_MS)
    if wait_result not in {0x00000000, 0x00000080}:  # WAIT_OBJECT_0 / WAIT_ABANDONED
        close_handle(handle)
        if wait_result == 0x00000102:  # WAIT_TIMEOUT
            raise TimeoutError("MissionRegistryCrossProcessLockTimeout")
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        release_mutex(handle)
        close_handle(handle)


class MissionRegistry:
    _instance = None
    missions: Dict[str, PersistentMission]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MissionRegistry, cls).__new__(cls)
            cls._instance.missions = {}
            cls._instance.mission_file = MISSION_FILE
            cls._instance._lock = threading.RLock()
            cls._instance._load_state()
        return cls._instance

    @contextmanager
    def locked(self, reload: bool = True):
        """Serialize state transitions within and across Windows processes."""
        with self._lock:
            with _cross_process_registry_lock(self.mission_file):
                if reload:
                    self._load_state_unlocked()
                yield

    def _read_models_from_disk(self) -> Dict[str, PersistentMission]:
        if os.path.exists(self.mission_file):
            try:
                with open(self.mission_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {k: PersistentMission(**v) for k, v in data.items()}
            except Exception as e:
                logger.error(f"Failed to load mission state: {e}")
        return {}

    def _load_state_unlocked(self):
        loaded = self._read_models_from_disk()
        if loaded or os.path.exists(self.mission_file):
            self.missions = loaded

    def _load_state(self):
        with self.locked(reload=False):
            self._load_state_unlocked()

    def _save_state(self):
        """Atomically persist the registry; callers mutate while holding ``locked``."""
        with self.locked(reload=False):
            try:
                directory = os.path.dirname(os.path.abspath(self.mission_file)) or "."
                os.makedirs(directory, exist_ok=True)
                payload = {k: v.model_dump() for k, v in self.missions.items()}
                fd, temp_path = tempfile.mkstemp(prefix=".missions-", suffix=".tmp", dir=directory)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump(payload, handle, separators=(",", ":"))
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_path, self.mission_file)
                finally:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
            except Exception as e:
                logger.error(f"Failed to save mission state: {e}")
                raise

    def save_mission(self, mission: PersistentMission):
        with self.locked():
            mission.updated_at = datetime.now(timezone.utc).isoformat()
            mission.state_revision += 1
            self.missions[mission.mission_id] = mission
            try:
                self._save_state()
            except Exception:
                self.missions = self._read_models_from_disk()
                raise
        self._schedule_terminal_objective_evaluation(mission)

    @staticmethod
    def _schedule_terminal_objective_evaluation(mission: PersistentMission) -> None:
        if not mission.objective_id or str(mission.status).upper() not in {
            "COMPLETED", "FAILED", "BLOCKED", "WAITING_DIRECTOR",
            "EXHAUSTED", "ESCALATED", "CANCELLED",
        }:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug(
                "No running event loop; objective terminal evaluation was not scheduled."
            )
            return

        from app.services.post_mission_evaluation import MissionTerminalEvent

        terminal_event = MissionTerminalEvent.from_mission(mission)
        task = loop.create_task(
            event_bus.publish_and_wait(terminal_event.to_business_event())
        )

        def report_failure(completed_task):
            try:
                completed_task.result()
            except Exception as exc:
                logger.error(
                    "Mission terminal Director evaluation failed closed: %s", exc
                )

        task.add_done_callback(report_failure)

    def snapshot(self) -> Dict[str, PersistentMission]:
        """Return detached persisted models without changing runtime state."""
        with self.locked():
            return {
                key: PersistentMission.model_validate(value.model_dump())
                for key, value in self.missions.items()
            }

    def persisted_digest(self) -> str:
        with self.locked(reload=False):
            if not os.path.exists(self.mission_file):
                return hashlib.sha256(b"").hexdigest()
            with open(self.mission_file, "rb") as handle:
                return hashlib.sha256(handle.read()).hexdigest()

    def get_mission(self, mission_id: str) -> Optional[PersistentMission]:
        with self.locked():
            return self.missions.get(mission_id)

    def _normalize_objective(self, query: str) -> str:
        explicit = re.search(
            r"MISSION\s+OBJECTIVE\s*:\s*(.+?)(?=(?:\s{2,}|\n)\s*(?:AUTHORIZED\s+INTERNAL\s+ACTIONS|NOT\s+AUTHORIZED|RETURN|REPORT|CONSTRAINTS?)\s*:|\Z)",
            query,
            re.IGNORECASE | re.DOTALL,
        )
        if explicit:
            objective = explicit.group(1)
        else:
            objective = query.strip()
            prefixes = [
                "director, create a mission to ",
                "create a mission to ",
                "i want to ",
                "director, i need a mission to ",
            ]
            lower = objective.lower()
            for prefix in prefixes:
                if lower.startswith(prefix):
                    objective = objective[len(prefix):]
                    break
        objective = re.sub(r"\s+", " ", objective).strip(" .")
        return f"{objective[:1].upper()}{objective[1:]}." if objective else objective

    def create_mission(
        self, raw_query: str, objective_id: Optional[str] = None
    ) -> PersistentMission:
        with self.locked():
            mission_id = f"mis_{uuid.uuid4().hex[:8]}"
            normalized_obj = self._normalize_objective(raw_query)
            spec = derive_mission_specification(raw_query, normalized_obj)
            criteria = spec["success_criteria"]
            required = int(criteria["required"])
            progress_label = (
                f"0 / {required} verified won clients"
                if criteria["criterion"] == "verified_won_clients"
                else f"0 / {required} verified {criteria['artifact_type']}{'' if required == 1 else 's'}"
            )

            now = datetime.now(timezone.utc).isoformat()
            mission = PersistentMission(
                mission_id=mission_id, objective_id=objective_id,
                original_request=raw_query, title=spec["title"],
                objective=normalized_obj, status="ACTIVE", priority="HIGH", autonomy_level="SEMI-AUTONOMOUS (Level 2)",
                progress=0, health="HEALTHY", success_criteria_progress=progress_label,
                mission_type=spec["mission_type"], verification_mode=spec["verification_mode"],
                simulation_mode=spec["simulation_mode"], success_criteria=criteria,
                discovery_scope=spec.get("discovery_scope"),
                mission_objective_achieved=False, current_phase=spec["current_phase"], milestones=spec["milestones"],
                plan_version="v1", plan_status="ACTIVE", progression_state="READY", execution_state="READY",
                dispatch_state="NONE", stall_detector_status="Clear", loop_safety_status="Safe",
                created_at=now, updated_at=now, last_progress_at=now,
                events=[f"[{now}] Mission Created."]
            )
            self.save_mission(mission)
            return mission

    def update_mission_status(self, mission_id: str, new_status: str) -> Optional[PersistentMission]:
        with self.locked():
            if mission_id not in self.missions:
                return None
            mission = self.missions[mission_id]
            mission.status = new_status
            if new_status == "PAUSED":
                mission.auto_continue_status = "STOPPED"
                mission.next_eligible_action = "None"
                mission.progression_state = "PAUSED"
            elif new_status == "ACTIVE":
                mission.progression_state = "READY"
                mission.auto_continue_status = "RUNNING"
            mission.events.append(f"[{datetime.now(timezone.utc).isoformat()}] Status changed to {new_status}.")
            self.save_mission(mission)
            return mission

    def get_mission_by_hint(self, hint: str) -> Optional[PersistentMission]:
        with self.locked():
            hint_lower = hint.lower()
            for m in self.missions.values():
                if m.mission_id in hint_lower: return m
                if "restaurant" in hint_lower and "restaurant" in m.objective.lower(): return m
            return list(self.missions.values())[-1] if self.missions else None

mission_registry = MissionRegistry()

class MissionCompletionGuard:
    @staticmethod
    def verified_success_evidence_ids(mission: PersistentMission) -> List[str]:
        criterion = mission.success_criteria.get("criterion", "verified_won_clients")
        if criterion == "verified_artifacts":
            expected_type = mission.success_criteria.get("artifact_type")
            verification_mode = mission.success_criteria.get("verification_mode", mission.verification_mode)
            identities: List[str] = []
            required_provenance = {
                "mission_id", "plan_version", "milestone_id", "decision_id",
                "materialization_id", "execution_request_id", "delegation_id",
                "worker_claim_id", "specialist", "artifact_type", "evidence_source",
                "simulation_evidence", "created_at",
            }
            for artifact in mission.artifact_lineage:
                if artifact.get("artifact_type") != expected_type or artifact.get("verified") is not True:
                    continue
                if artifact.get("mission_id") != mission.mission_id:
                    continue
                if any(
                    field not in artifact
                    or (field != "simulation_evidence" and not artifact.get(field))
                    for field in required_provenance
                ):
                    continue
                evidence_source = normalize_evidence_source(artifact.get("evidence_source"))
                if evidence_source == EvidenceSource.UNKNOWN:
                    continue
                if verification_mode == PRODUCTION_BUSINESS_SUCCESS:
                    if expected_type == "LeadArtifact":
                        source_metadata = artifact.get("source_metadata") or {}
                        if not (
                            evidence_source == EvidenceSource.LIVE_EXTERNAL
                            and artifact.get("simulation_evidence") is False
                            and artifact.get("source_provider")
                            and source_metadata.get("request_succeeded") is True
                            and is_entity_qualified(artifact.get("entity_qualification"))
                        ):
                            continue
                    elif not qualifies_for_production_success(evidence_source):
                        continue
                if verification_mode in {LIVE_EVIDENCE_CANARY, QUALIFIED_LEAD_CANARY}:
                    if evidence_source != EvidenceSource.LIVE_EXTERNAL:
                        continue
                    if artifact.get("simulation_evidence") is not False:
                        continue
                    source_metadata = artifact.get("source_metadata") or {}
                    if not artifact.get("source_provider") or source_metadata.get("request_succeeded") is not True:
                        continue
                    if verification_mode == QUALIFIED_LEAD_CANARY and not is_entity_qualified(
                        artifact.get("entity_qualification")
                    ):
                        continue
                artifact_id = artifact.get("artifact_id")
                if artifact_id and str(artifact_id) not in identities:
                    identities.append(str(artifact_id))
            return identities

        identities: List[str] = []
        for evidence in mission.success_evidence:
            if evidence.get("event_type") != "DEAL_WON" or not evidence.get("verified"):
                continue
            if not qualifies_for_production_success(evidence.get("evidence_source")):
                continue
            identity = evidence.get("client_id") or evidence.get("lead_id") or evidence.get("evidence_id")
            if identity and str(identity) not in identities:
                identities.append(str(identity))
        return identities

    @staticmethod
    def verified_success_count(mission: PersistentMission) -> int:
        return len(MissionCompletionGuard.verified_success_evidence_ids(mission))

    def evaluate_completion(self, mission: PersistentMission) -> Tuple[str, bool]:
        verified_count = self.verified_success_count(mission)
        required_count = max(1, int(mission.success_criteria.get("required", 10)))
        criterion = mission.success_criteria.get("criterion", "verified_won_clients")
        if verified_count >= required_count:
            return "PASSED", True
        if criterion == "verified_artifacts":
            artifact_type = mission.success_criteria.get("artifact_type", "Artifact")
            return f"FAILED (Success Criteria Unmet: {verified_count}/{required_count} Verified {artifact_type})", False
        return f"FAILED (Success Criteria Unmet: {verified_count}/{required_count} Verified Won Clients)", False

    def refresh_progress(self, mission: PersistentMission) -> int:
        verified_count = self.verified_success_count(mission)
        required_count = max(1, int(mission.success_criteria.get("required", 10)))
        if mission.success_criteria.get("criterion") == "verified_artifacts":
            artifact_type = mission.success_criteria.get("artifact_type", "Artifact")
            suffix = "" if required_count == 1 else "s"
            mission.success_criteria_progress = f"{verified_count} / {required_count} verified {artifact_type}{suffix}"
        else:
            mission.success_criteria_progress = f"{verified_count} / {required_count} verified won clients"
        return verified_count

class MissionPlanActivationService:
    def activate_new_plan(self, mission: PersistentMission, new_plan_version: str, new_milestones: list):
        if any(plan.get("version") == mission.plan_version for plan in mission.historical_plans):
            return
        mission.historical_plans.append({
            "plan_id": f"plan_{mission.mission_id}_{mission.plan_version}",
            "version": mission.plan_version,
            "status": "COMPLETED",
            "progress": 100,
            "outcome": "Objective unmet",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        mission.plan_version = new_plan_version
        mission.plan_status = "ACTIVE"
        for m in new_milestones:
            m.setdefault("plan_version", new_plan_version)
            mission.milestones.append(m)
        mission.replan_count += 1
        mission.progression_state = "READY"
        mission.execution_state = "READY"
        mission.dispatch_state = "NONE"
        mission.auto_continue_status = "RUNNING"
        mission.events.append(f"[{datetime.now(timezone.utc).isoformat()}] MISSION_PLAN_ACTIVATED: Plan {new_plan_version} bound to mission execution stream.")


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _schedule_event(event: BusinessEvent):
    try:
        asyncio.get_running_loop().create_task(event_bus.publish(event))
    except RuntimeError:
        logger.debug("No running event loop; mission event publication deferred.")


class MissionInvariantValidator:
    """Pure validation of persisted Mission Intelligence state."""

    ACTIVE_REQUEST_STATES = {"READY", "CLAIMED", "DISPATCHED", "RUNNING"}
    ACTIVE_DELEGATION_STATES = {"Pending", "Claimed", "Running"}
    TERMINAL_MISSION_STATES = {"COMPLETED", "CANCELLED", "FAILED", "WAITING_DIRECTOR"}

    def validate(self, mission: PersistentMission) -> List[Dict[str, str]]:
        findings: List[Dict[str, str]] = []

        def add(severity: str, code: str, detail: str):
            findings.append({"severity": severity, "code": code, "detail": detail})

        decisions = {item.get("decision_id"): item for item in mission.progression_decisions}
        mats = {item.get("materialization_id"): item for item in mission.progression_materializations}
        requests = {item.get("execution_request_id"): item for item in mission.execution_requests}
        delegations = {
            item.get("delegation_id"): item
            for item in [*mission.active_delegations, *mission.delegation_history]
        }
        claims = {item.get("worker_claim_id"): item for item in mission.worker_claims}

        if not 0 <= mission.progress <= 100:
            add("CRITICAL", "MISSION_PROGRESS_OUT_OF_RANGE", f"Progress is {mission.progress}; expected 0..100.")
        if mission.status == "COMPLETED":
            status, achieved = MissionCompletionGuard().evaluate_completion(mission)
            if not achieved or not mission.mission_objective_achieved:
                add("CRITICAL", "UNVERIFIED_MISSION_COMPLETION", status)
        if mission.plan_status == "COMPLETED" and mission.status == "COMPLETED" and not mission.mission_objective_achieved:
            add("CRITICAL", "PLAN_COMPLETION_CONFUSED_WITH_MISSION_COMPLETION", "Completed plan has no verified mission outcome.")
        if mission.status in self.TERMINAL_MISSION_STATES:
            if any(r.get("status") in self.ACTIVE_REQUEST_STATES for r in mission.execution_requests):
                add("HIGH", "TERMINAL_MISSION_HAS_ACTIVE_REQUEST", "Terminal mission retains executable work.")
            if any(d.get("status") in self.ACTIVE_DELEGATION_STATES for d in mission.active_delegations):
                add("HIGH", "TERMINAL_MISSION_HAS_ACTIVE_DELEGATION", "Terminal mission retains an active delegation.")

        materialized_decisions = set()
        for mat in mission.progression_materializations:
            decision_id = mat.get("decision_id")
            if decision_id not in decisions:
                add("HIGH", "ORPHAN_MATERIALIZATION", f"{mat.get('materialization_id')} references {decision_id}.")
            if decision_id in materialized_decisions:
                add("HIGH", "DUPLICATE_DECISION_MATERIALIZATION", f"Decision {decision_id} was materialized more than once.")
            materialized_decisions.add(decision_id)

        request_keys = set()
        for req in mission.execution_requests:
            mat_id = req.get("materialization_id")
            if mat_id not in mats:
                add("HIGH", "ORPHAN_EXECUTION_REQUEST", f"{req.get('execution_request_id')} references {mat_id}.")
            key = req.get("idempotency_key")
            if key and key in request_keys:
                add("CRITICAL", "DUPLICATE_EXECUTION_REQUEST", f"Duplicate idempotency key {key}.")
            if key:
                request_keys.add(key)

        delegated_requests = set()
        for delegation in delegations.values():
            req_id = delegation.get("execution_request_id")
            if req_id not in requests:
                add("HIGH", "ORPHAN_DELEGATION", f"{delegation.get('delegation_id')} references {req_id}.")
            if req_id in delegated_requests:
                add("CRITICAL", "DUPLICATE_DELEGATION", f"Request {req_id} has multiple delegations.")
            delegated_requests.add(req_id)

        active_claims_by_delegation: Dict[str, int] = {}
        for claim in mission.worker_claims:
            delegation_id = claim.get("delegation_id")
            if delegation_id not in delegations:
                add("HIGH", "ORPHAN_WORKER_CLAIM", f"{claim.get('worker_claim_id')} has no delegation correlation.")
            if claim.get("status") == "ACTIVE":
                active_claims_by_delegation[delegation_id] = active_claims_by_delegation.get(delegation_id, 0) + 1
                delegation = delegations.get(delegation_id, {})
                if delegation.get("status") not in self.ACTIVE_DELEGATION_STATES:
                    add("HIGH", "ACTIVE_CLAIM_ON_INACTIVE_DELEGATION", f"Claim for {delegation_id} remains ACTIVE.")
        for delegation_id, count in active_claims_by_delegation.items():
            if count > 1:
                add("CRITICAL", "DUPLICATE_ACTIVE_WORKER_CLAIM", f"Delegation {delegation_id} has {count} active claims.")

        for artifact in mission.artifact_lineage:
            chain = {
                "decision_id": decisions,
                "materialization_id": mats,
                "execution_request_id": requests,
                "delegation_id": delegations,
                "worker_claim_id": claims,
            }
            for field_name, collection in chain.items():
                if artifact.get(field_name) not in collection:
                    add("HIGH", "BROKEN_ARTIFACT_LINEAGE", f"Artifact {artifact.get('artifact_id')} has invalid {field_name}.")
            if not artifact.get("verified"):
                add("HIGH", "UNVERIFIED_ARTIFACT_LINEAGE", f"Artifact {artifact.get('artifact_id')} is not verified.")

        active_request = any(r.get("status") in self.ACTIVE_REQUEST_STATES for r in mission.execution_requests)
        active_delegation = any(d.get("status") in self.ACTIVE_DELEGATION_STATES for d in mission.active_delegations)
        if mission.progression_state == "RUNNING" and not (active_request and active_delegation):
            add("HIGH", "GHOST_RUNNING_STATE", "Mission reports RUNNING without correlated request and delegation.")
        if mission.replan_count > MAX_PLAN_LOOPS or self._plan_number(mission.plan_version) > MAX_PLAN_LOOPS + 1:
            add("CRITICAL", "REPLAN_LIMIT_EXCEEDED", f"Plan {mission.plan_version}, replan_count={mission.replan_count}.")
        if mission.outreach_batch_count > MAX_OUTREACH_BATCHES:
            add("CRITICAL", "OUTREACH_BATCH_LIMIT_EXCEEDED", f"Outreach batches={mission.outreach_batch_count}.")
        return findings

    @staticmethod
    def _plan_number(version: str) -> int:
        try:
            return int(str(version).lstrip("v"))
        except ValueError:
            return 0


class MissionSafetyReconciler:
    """Fail closed before execution when persisted state violates invariants."""

    def quarantine_unsafe_missions(self, mission_id: Optional[str] = None) -> List[str]:
        quarantined: List[str] = []
        with mission_registry.locked():
            candidates = [mission_registry.missions.get(mission_id)] if mission_id else list(mission_registry.missions.values())
            for mission in candidates:
                if not mission or mission.status != "ACTIVE":
                    continue
                findings = MissionInvariantValidator().validate(mission)
                unsafe = [f for f in findings if f["severity"] in {"CRITICAL", "HIGH"}]
                if not unsafe:
                    continue
                now = datetime.now(timezone.utc).isoformat()
                codes = sorted({f["code"] for f in unsafe})
                for request in mission.execution_requests:
                    if request.get("status") in MissionInvariantValidator.ACTIVE_REQUEST_STATES:
                        request["status"] = "BLOCKED"
                        request["blocked_at"] = now
                for claim in mission.worker_claims:
                    if claim.get("status") == "ACTIVE":
                        claim["status"] = "INVALIDATED"
                        claim["completed_at"] = now
                        claim["health"] = "INVALID"
                for delegation in mission.active_delegations:
                    if delegation.get("status") in MissionInvariantValidator.ACTIVE_DELEGATION_STATES:
                        delegation["status"] = "INVALIDATED"
                    delegation["quarantined_at"] = now
                    delegation["quarantine_reason"] = "PERSISTED_STATE_INVARIANT_VIOLATION"
                    mission.delegation_history.append(dict(delegation))
                mission.active_delegations = []
                MissionEngine._escalate(mission, "PERSISTED_STATE_INVARIANT_VIOLATION")
                mission.last_error = ",".join(codes)
                mission.events.append(f"[{now}] MISSION_EXECUTION_QUARANTINED: {', '.join(codes)}.")
                mission_registry.save_mission(mission)
                quarantined.append(mission.mission_id)
        return quarantined


class MissionExecutionStateReconciler:
    """Keep auto-continue state aligned with actually runnable correlated work."""

    RUNNABLE_REQUEST_STATES = {"READY", "DISPATCHED", "RUNNING"}
    RUNNABLE_DELEGATION_STATES = {"Pending", "Claimed", "Running"}

    @classmethod
    def has_runnable_work(cls, mission: PersistentMission) -> bool:
        active_delegations = {
            item.get("execution_request_id")
            for item in mission.active_delegations
            if item.get("status") in cls.RUNNABLE_DELEGATION_STATES
        }
        for request in mission.execution_requests:
            status = request.get("status")
            if status == "READY":
                return True
            if status in {"DISPATCHED", "RUNNING"} and request.get("execution_request_id") in active_delegations:
                return True
        return bool(active_delegations)

    @classmethod
    def block(cls, mission: PersistentMission, reason: str) -> bool:
        if mission.status in MissionInvariantValidator.TERMINAL_MISSION_STATES:
            return False
        now = datetime.now(timezone.utc).isoformat()
        mission.progression_state = "BLOCKED"
        mission.execution_state = "BLOCKED"
        mission.dispatch_state = "BLOCKED"
        mission.auto_continue_status = "STOPPED"
        mission.health = "AT_RISK"
        mission.last_error = reason
        mission.escalation_reason = reason
        mission.stall_detector_status = "STALLED"
        mission.events.append(f"[{now}] MISSION_EXECUTION_BLOCKED: {reason}.")
        return True

    @classmethod
    def ensure_truthful(cls, mission: PersistentMission) -> bool:
        if mission.auto_continue_status != "RUNNING" or cls.has_runnable_work(mission):
            return False
        return cls.block(mission, "AUTO_CONTINUE_NO_RUNNABLE_WORK")


class MissionWorkCoordinator:
    """Atomic idempotent transitions for request, delegation, and worker claims."""

    def claim_ready_request(self, mission_id: str) -> Optional[Dict[str, Any]]:
        with mission_registry.locked():
            mission = mission_registry.missions.get(mission_id)
            if not mission or mission.status != "ACTIVE":
                return None
            request = next((r for r in mission.execution_requests if r.get("status") == "READY"), None)
            if not request:
                return None
            request["status"] = "CLAIMED"
            request["claimed_at"] = datetime.now(timezone.utc).isoformat()
            mission.dispatch_state = "CLAIMING"
            mission.progression_state = "DISPATCHING"
            mission.execution_state = "READY"
            mission_registry.save_mission(mission)
            return dict(request)

    def create_delegation(self, mission_id: str, execution_request_id: str) -> Optional[Dict[str, Any]]:
        with mission_registry.locked():
            mission = mission_registry.missions.get(mission_id)
            if not mission or mission.status != "ACTIVE":
                return None
            request = next((r for r in mission.execution_requests if r.get("execution_request_id") == execution_request_id), None)
            if not request:
                return None
            existing = next((
                d for d in [*mission.active_delegations, *mission.delegation_history]
                if d.get("execution_request_id") == execution_request_id
            ), None)
            if existing:
                return dict(existing)
            if request.get("status") != "CLAIMED":
                return None
            delegation = {
                "delegation_id": f"del_{uuid.uuid4().hex[:8]}",
                "execution_request_id": execution_request_id,
                "materialization_id": request.get("materialization_id"),
                "decision_id": request.get("decision_id"),
                "target_agent": request.get("target_intelligence"),
                "objective": request.get("selected_action", "Execute Task"),
                "milestone_id": request.get("milestone_id", "m1"),
                "plan_version": request.get("plan_version", mission.plan_version),
                "expected_artifact": request.get("expected_artifact", "UnknownArtifact"),
                "target_count": request.get("target_count", 1),
                "verification_mode": request.get("verification_mode", mission.verification_mode),
                "simulation_mode": request.get("simulation_mode", mission.simulation_mode),
                "status": "Pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "idempotency_key": f"delegation:{execution_request_id}",
            }
            if request.get("discovery_scope"):
                delegation["discovery_scope"] = dict(request["discovery_scope"])
            mission.active_delegations.append(delegation)
            request["status"] = "DISPATCHED"
            request["delegation_id"] = delegation["delegation_id"]
            mission.dispatch_state = "DISPATCHED"
            mission.execution_state = "DISPATCHED"
            mission.progression_state = "QUEUED"
            mission.auto_continue_status = "RUNNING"
            mission_registry.save_mission(mission)
            return dict(delegation)

    def claim_pending_delegation(self, mission_id: str, worker_id: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        with mission_registry.locked():
            mission = mission_registry.missions.get(mission_id)
            if not mission or mission.status != "ACTIVE":
                return None
            delegation = next((d for d in mission.active_delegations if d.get("status") == "Pending"), None)
            if not delegation:
                return None
            delegation_id = delegation.get("delegation_id")
            existing = next((
                claim for claim in mission.worker_claims
                if claim.get("delegation_id") == delegation_id and claim.get("status") == "ACTIVE"
            ), None)
            if existing:
                return dict(delegation), dict(existing)
            now = datetime.now(timezone.utc).isoformat()
            claim = {
                "worker_claim_id": f"wcl_{uuid.uuid4().hex[:8]}",
                "delegation_id": delegation_id,
                "execution_request_id": delegation.get("execution_request_id"),
                "worker_id": worker_id,
                "status": "ACTIVE",
                "health": "HEALTHY",
                "claimed_at": now,
            }
            if delegation.get("discovery_scope"):
                claim["discovery_scope"] = dict(delegation["discovery_scope"])
            delegation.update({"status": "Claimed", "worker_id": worker_id, "worker_claim_id": claim["worker_claim_id"], "claimed_at": now})
            request = next((r for r in mission.execution_requests if r.get("execution_request_id") == delegation.get("execution_request_id")), None)
            if request:
                request["status"] = "RUNNING"
            mission.worker_claims.append(claim)
            mission.progression_state = "RUNNING"
            mission.execution_state = "RUNNING"
            mission.dispatch_state = "CLAIMED"
            mission_registry.save_mission(mission)
            return dict(delegation), dict(claim)

    def mark_delegation_running(self, mission_id: str, delegation_id: str) -> bool:
        with mission_registry.locked():
            mission = mission_registry.missions.get(mission_id)
            if not mission:
                return False
            delegation = next((d for d in mission.active_delegations if d.get("delegation_id") == delegation_id), None)
            if not delegation or delegation.get("status") not in {"Claimed", "Running"}:
                return False
            delegation["status"] = "Running"
            delegation.setdefault("started_at", datetime.now(timezone.utc).isoformat())
            mission_registry.save_mission(mission)
            return True

class MissionExecutionEvidenceResolver:
    def resolve_execution_truth(self, mission: PersistentMission) -> Dict[str, Any]:
        truth = {
            "ghost_running": "No", "execution_state_integrity": "PASSED", "delegation_execution_integrity": "PASSED",
            "worker_claim_integrity": "NOT_APPLICABLE", "dispatch_integrity": "PASSED", "active_request": "NONE",
            "active_delegation": "NONE", "active_worker": "NONE"
        }
        req = next((r for r in reversed(mission.execution_requests) if r.get("status") in ["READY", "CLAIMED", "DISPATCHED", "RUNNING"]), None)
        dele = next((d for d in reversed(mission.active_delegations) if d.get("status") in ["Pending", "Claimed", "Running"]), None)
        work = next((
            w for w in reversed(mission.worker_claims)
            if w.get("status") == "ACTIVE" and dele and w.get("delegation_id") == dele.get("delegation_id")
        ), None)

        if mission.progression_state == "RUNNING" or mission.execution_state == "RUNNING":
            if not req:
                truth["ghost_running"] = "Yes"
                truth["execution_state_integrity"] = "FAILED"
            elif not dele:
                truth["ghost_running"] = "Yes"
                truth["execution_state_integrity"] = "FAILED"
                truth["delegation_execution_integrity"] = "FAILED"
            else:
                truth["active_request"] = req.get("execution_request_id", "NONE")
                truth["active_delegation"] = dele.get("delegation_id", "NONE")
                if dele.get("status") in ["Claimed", "Running"]:
                    if not work:
                        truth["worker_claim_integrity"] = "FAILED"
                        truth["execution_state_integrity"] = "FAILED"
                    else:
                        truth["worker_claim_integrity"] = "PASSED"
                        truth["active_worker"] = work.get("worker_claim_id", "NONE")
        elif mission.progression_state == "READY":
            if req and req.get("status") == "READY": pass 
        return truth

class MissionExecutionDispatcher:
    async def process_ready_requests(self, mission_id: Optional[str] = None) -> int:
        MissionSafetyReconciler().quarantine_unsafe_missions(mission_id)
        mission_ids = [mission_id] if mission_id else list(mission_registry.snapshot().keys())
        coordinator = MissionWorkCoordinator()
        dispatched_count = 0
        for current_mission_id in mission_ids:
            while True:
                req = coordinator.claim_ready_request(current_mission_id)
                if not req:
                    break
                try:
                    delegation = coordinator.create_delegation(
                        current_mission_id, req["execution_request_id"]
                    )
                    if not delegation:
                        raise RuntimeError("DELEGATION_CREATION_REJECTED")
                    dispatched_count += 1
                except Exception as exc:
                    logger.error("Mission request dispatch failed: %s", exc, exc_info=True)
                    with mission_registry.locked():
                        mission = mission_registry.missions.get(current_mission_id)
                        if mission:
                            persisted_request = next((
                                item for item in mission.execution_requests
                                if item.get("execution_request_id") == req["execution_request_id"]
                            ), None)
                            if persisted_request:
                                persisted_request["status"] = "FAILED"
                                persisted_request["failed_at"] = datetime.now(timezone.utc).isoformat()
                                persisted_request["error"] = f"EXECUTION_REQUEST_UNDISPATCHED: {exc}"
                            MissionExecutionStateReconciler.block(
                                mission, "EXECUTION_REQUEST_UNDISPATCHED"
                            )
                            mission_registry.save_mission(mission)
                    continue

                try:
                    await event_bus.publish(BusinessEvent(
                        event_id=f"evt_{uuid.uuid4().hex[:8]}",
                        event_type="MISSION_DELEGATION_CREATED",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        lead_id="internal_napstertec",
                        business_name="NapsterTec",
                        communication_id="",
                        conversation_id="",
                        correlation_id=current_mission_id,
                        workflow_id=delegation["delegation_id"],
                        channel="",
                        evidence=f"Delegation {delegation['delegation_id']} pending.",
                        confidence=1.0,
                        execution_metadata={
                            "mission_id": current_mission_id,
                            "execution_request_id": req["execution_request_id"],
                            "delegation_id": delegation["delegation_id"],
                        },
                    ))
                except Exception as exc:
                    logger.warning("Delegation event publication failed: %s", exc, exc_info=True)
        return dispatched_count

class MissionProgressionMaterializer:
    def materialize(self, mission: PersistentMission, decision: Dict[str, Any]):
        mat_id = f"mat_{decision['decision_id']}"
        existing = next((m for m in mission.progression_materializations if m.get("materialization_id") == mat_id), None)
        if existing:
            return existing
            
        target_agent = decision.get("target_intelligence", "Unknown")
        action_desc = decision.get("selected_action", "Unknown")
        capability = "client_outreach" if target_agent == "communication_intelligence" else \
                     "lead_discovery" if target_agent == "lead_intelligence" else \
                     "website_analysis" if target_agent == "website_intelligence" else \
                     "opportunity_analysis" if target_agent == "opportunity_intelligence" else \
                     "business_solution" if target_agent == "business_solution_architect" else "general_task"
        
        if target_agent == "Unknown":
            MissionExecutionStateReconciler.block(mission, "SPECIALIST_UNRESOLVED")
            return None

        status = "APPROVAL_REQUIRED" if "approval" in action_desc.lower() or target_agent == "communication_intelligence" else "EXECUTION_READY"
        mission.progression_state = "WAITING" if status == "APPROVAL_REQUIRED" else "READY"
        mission.auto_continue_status = "WAITING_APPROVAL" if status == "APPROVAL_REQUIRED" else "RUNNING"
        
        mat_record = {
            "materialization_id": mat_id, "decision_id": decision["decision_id"],
            "status": status, "created_at": datetime.now(timezone.utc).isoformat(),
            "idempotency_key": f"materialization:{decision['decision_id']}"
        }
        if decision.get("discovery_scope"):
            mat_record["discovery_scope"] = dict(decision["discovery_scope"])
        
        if status == "APPROVAL_REQUIRED":
            app_id = f"app_{uuid.uuid4().hex[:8]}"
            app_req = ApprovalRequest(
                approval_id=app_id,
                mission_id=mission.mission_id,
                decision_id=decision["decision_id"],
                materialization_id=mat_id,
                action=action_desc,
                status=ApprovalStatus.PENDING,
                approval_type=ApprovalType.MATERIALIZATION,
                risk_level="HIGH" if target_agent == "communication_intelligence" else "LOW",
                requester="mission_engine"
            )
            approval_repository.create(app_req)
            mat_record["approval_id"] = app_id
            mission.events.append(f"[{datetime.now(timezone.utc).isoformat()}] MATERIALIZATION_APPROVAL_REQUIRED: {mat_id} requires Director approval ({app_id}).")
            mission.pending_approvals.append(app_id)

        mission.progression_materializations.append(mat_record)

        if status == "EXECUTION_READY":
            req_id = f"mer_{uuid.uuid4().hex[:8]}"
            request_record = {
                "execution_request_id": req_id, "materialization_id": mat_id, "decision_id": decision["decision_id"],
                "status": "READY", "attempt": 1, "idempotency_key": f"request:{mat_id}:1",
                "capability": capability, "resolution_strategy": "dynamic_resolution", "target_intelligence": target_agent,
                "selected_action": action_desc, "milestone_id": decision.get("milestone_id", "m1"),
                "plan_version": decision.get("plan_version", mission.plan_version),
                "expected_artifact": decision.get("expected_artifact", "UnknownArtifact"),
                "target_count": decision.get("target_count", 1),
                "verification_mode": mission.verification_mode,
                "simulation_mode": mission.simulation_mode,
                "read_only_external_discovery": mission.verification_mode in {
                    LIVE_EVIDENCE_CANARY, QUALIFIED_LEAD_CANARY
                },
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            if decision.get("discovery_scope"):
                request_record["discovery_scope"] = dict(decision["discovery_scope"])
            mission.execution_requests.append(request_record)
            mission.dispatch_state = "AWAITING_DISPATCH"
            mission.events.append(f"[{datetime.now(timezone.utc).isoformat()}] MATERIALIZATION: Execution Request {req_id} generated. Awaiting Dispatcher.")
            _schedule_event(BusinessEvent(
                event_id=f"evt_{uuid.uuid4().hex[:8]}", event_type="MISSION_EXECUTION_REQUEST_READY", timestamp=datetime.now(timezone.utc).isoformat(),
                lead_id="internal_napstertec", business_name="NapsterTec", communication_id="", conversation_id="", correlation_id="", workflow_id="", channel="", evidence=f"Execution Request {req_id} Ready", confidence=1.0
            ))
        return mat_record

class MissionStallDetector:
    def evaluate_and_recover(self, mission: PersistentMission, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if mission.status == "ACTIVE" and not mission.mission_objective_achieved and mission.plan_status == "ACTIVE":
            active_delegations = any(d.get("status") in {"Pending", "Claimed", "Running"} for d in mission.active_delegations)
            active_requests = any(r.get("status") in MissionInvariantValidator.ACTIVE_REQUEST_STATES for r in mission.execution_requests)
            last_progress = _parse_timestamp(mission.last_progress_at) or now
            stale = now - last_progress >= timedelta(seconds=STALL_TIMEOUT_SECONDS)
            if not stale:
                mission.stall_detector_status = "Clear"
                return False
            mission.stall_detector_status = "STALLED"
            if not active_delegations and not active_requests:
                if mission.progression_decisions:
                    latest_decision = mission.progression_decisions[-1]
                    mat_id = f"mat_{latest_decision['decision_id']}"
                    if not any(m.get("materialization_id") == mat_id for m in mission.progression_materializations):
                        mission.events.append(f"[{datetime.now(timezone.utc).isoformat()}] MISSION_STALL_DETECTED: Orphaned decision {latest_decision['decision_id']}.")
                        materializer = MissionProgressionMaterializer()
                        materializer.materialize(mission, latest_decision)
                        mission.stall_detector_status = "Clear"
                        mission.stall_recovery_count += 1
                        return True
            req = next((r for r in mission.execution_requests if r.get("status") == "READY"), None)
            if req:
                mission.events.append(f"[{datetime.now(timezone.utc).isoformat()}] STALL_RECOVERY: Recovering undispatched request {req['execution_request_id']}.")
                mission.stall_recovery_count += 1
                try:
                    asyncio.get_running_loop().create_task(MissionExecutionDispatcher().process_ready_requests())
                except RuntimeError:
                    pass
                mission.stall_detector_status = "Clear"
                return True
            mission.stall_recovery_count += 1
            if mission.stall_recovery_count >= MAX_STALL_RECOVERIES:
                mission.status = "WAITING_DIRECTOR"
                mission.progression_state = "ESCALATION_REQUIRED"
                mission.execution_state = "BLOCKED"
                mission.auto_continue_status = "STOPPED"
                mission.health = "AT_RISK"
                mission.escalation_reason = "STALL_RECOVERY_LIMIT_REACHED"
                mission.loop_safety_status = "STALL_LIMIT_REACHED"
                mission.events.append(f"[{now.isoformat()}] MISSION_STALL_LIMIT_REACHED: Director intervention required.")
                return True
        return False

class MissionAuditService:
    def run_execution_audit(self, mission_id: str) -> Dict[str, Any]:
        mission = mission_registry.snapshot().get(mission_id)
        if not mission: return {"mission_state_validation": "Failed (Mission Not Found)"}
            
        resolver = MissionExecutionEvidenceResolver()
        truth = resolver.resolve_execution_truth(mission)
        
        dec = mission.progression_decisions[-1] if mission.progression_decisions else {}
        mat = mission.progression_materializations[-1] if mission.progression_materializations else {}
        req = next((r for r in reversed(mission.execution_requests) if r.get("status") in ["READY", "CLAIMED", "DISPATCHED", "RUNNING"]), mission.execution_requests[-1] if mission.execution_requests else {})
        dele = next((d for d in reversed(mission.active_delegations) if d.get("status") in ["Pending", "Running"]), mission.active_delegations[-1] if mission.active_delegations else {})
        work = next((w for w in reversed(mission.worker_claims) if w.get("status") == "ACTIVE"), mission.worker_claims[-1] if mission.worker_claims else {})
        ext = mission.external_operations[-1] if mission.external_operations else {}
        
        findings = MissionInvariantValidator().validate(mission)
        overall = "PASSED" if truth["execution_state_integrity"] == "PASSED" and not any(f["severity"] in {"CRITICAL", "HIGH"} for f in findings) else "FAILED"
        guard_status, _ = MissionCompletionGuard().evaluate_completion(mission)
        
        return {
            "mission_id": mission.mission_id, "mission_action": "INSPECT_EXECUTION", "read_only": "Yes", "state_mutation_from_query": "None",
            "mission_status": mission.status, "mission_objective": mission.objective, "success_criteria": mission.success_criteria_progress,
            "plan_version": mission.plan_version, "plan_status": mission.plan_status, "current_phase": mission.current_phase, "current_milestone": mission.current_milestone,
            "dec_id": dec.get("decision_id", "NONE"), "dec_action": dec.get("selected_action", "NONE"),
            "mat_id": mat.get("materialization_id", "NONE"), "mat_status": mat.get("status", "NONE"), "mat_cap": req.get("capability", "NONE"), "mat_intel": req.get("target_intelligence", "NONE"),
            "req_id": req.get("execution_request_id", "NONE"), "req_status": req.get("status", "NONE"), "req_cap": req.get("capability", "NONE"), "req_target": req.get("target_intelligence", "NONE"),
            "disp_state": mission.dispatch_state, "disp_created": "Yes" if dele else "No",
            "del_id": dele.get("delegation_id", "NONE"), "del_target": dele.get("target_agent", "NONE"), "del_status": dele.get("status", "NONE"), "del_expected": dele.get("expected_artifact", "NONE"),
            "app_required": "Yes" if mat.get("status") == "APPROVAL_REQUIRED" else "No",
            "work_id": work.get("worker_claim_id", "NONE"), "work_status": work.get("status", "NONE"), "work_health": work.get("health", "NOT_APPLICABLE"), "work_started": work.get("claimed_at", "NONE"),
            "ext_mech": "EXTERNAL_ASYNC" if ext else "NOT_STARTED", "ext_op": ext.get("operation_id", "NONE"), "ext_prov": ext.get("provider", "NONE"), "ext_status": ext.get("status", "NONE"),
            "ev_mech": "AUTONOMOUS_WORKER" if work else "NOT_STARTED", "ev_complete": "Yes" if truth["execution_state_integrity"] == "PASSED" else "No", "ev_ghost": truth["ghost_running"],
            "val_exec": truth["execution_state_integrity"], "val_dele": truth["delegation_execution_integrity"], "val_work": truth["worker_claim_integrity"], "val_mat": "PASSED" if mat else "FAILED", "val_stall": "PASSED" if mission.stall_detector_status == "Clear" else "FAILED", "val_plan": "PASSED" if not any(f["code"] == "REPLAN_LIMIT_EXCEEDED" for f in findings) else "FAILED", "val_comp": guard_status, "val_overall": overall,
            "findings": findings, "artifact_lineage_count": len(mission.artifact_lineage),
            "progression_state": mission.progression_state, "execution_state": mission.execution_state, "autonomous_safe": "Yes" if overall == "PASSED" else "No"
        }

    def run_engineering_audit(self) -> Dict[str, Any]:
        """Inspect detached persisted evidence and return a zero-mutation audit."""
        before_digest = mission_registry.persisted_digest()
        missions = mission_registry.snapshot()
        validator = MissionInvariantValidator()
        mission_reports = []
        all_findings = []
        for mission in missions.values():
            findings = validator.validate(mission)
            all_findings.extend({"mission_id": mission.mission_id, **finding} for finding in findings)
            mission_reports.append({
                "mission_id": mission.mission_id,
                "status": mission.status,
                "plan_version": mission.plan_version,
                "plan_status": mission.plan_status,
                "progress": mission.progress,
                "progression_state": mission.progression_state,
                "execution_state": mission.execution_state,
                "replan_count": mission.replan_count,
                "retry_count": mission.retry_count,
                "outreach_batch_count": mission.outreach_batch_count,
                "success_criteria": mission.success_criteria_progress,
                "mission_objective_achieved": mission.mission_objective_achieved,
                "decisions": len(mission.progression_decisions),
                "materializations": len(mission.progression_materializations),
                "execution_requests": len(mission.execution_requests),
                "active_delegations": len(mission.active_delegations),
                "delegation_history": len(mission.delegation_history),
                "worker_claims": len(mission.worker_claims),
                "artifact_lineage": len(mission.artifact_lineage),
                "findings": findings,
            })
        after_digest = mission_registry.persisted_digest()
        critical_count = sum(1 for finding in all_findings if finding["severity"] == "CRITICAL")
        high_count = sum(1 for finding in all_findings if finding["severity"] == "HIGH")
        isolation = "PASSED" if before_digest == after_digest else "FAILED"
        return {
            "mission_action": "AUDIT",
            "read_only": "Yes",
            "state_mutation_from_query": "None",
            "architecture": [
                "DirectorContextBuilderTool classifies command and authority",
                "DirectorEvaluatorTool routes mutation or read-only audit",
                "MissionEngine owns plan and progression state",
                "MissionProgressionMaterializer creates idempotent execution requests",
                "MissionWorkCoordinator atomically correlates request, delegation, and claim",
                "AutonomousMissionWorker invokes the registered specialist",
                "MissionEngine validates artifact lineage and business outcomes",
                "MissionCompletionGuard separates plan completion from verified mission completion",
            ],
            "invariants": [
                "Mission completion requires verified success evidence",
                "Plan completion never implies mission completion",
                "Every artifact has decision-to-worker lineage",
                "At most one request per materialization attempt, delegation per request, and active claim per delegation",
                "Terminal missions have no executable work",
                "Retries, replans, repeated strategies, outreach batches, zero-progress plans, and stalls are bounded",
                "Read-only audit does not persist artifacts, events, or mission state",
            ],
            "thresholds": {
                "max_replans": MAX_PLAN_LOOPS,
                "max_execution_retries": MAX_EXECUTION_RETRIES,
                "max_repeated_strategies": MAX_REPEATED_STRATEGIES,
                "max_outreach_batches": MAX_OUTREACH_BATCHES,
                "max_zero_progress_plans": MAX_ZERO_PROGRESS_PLANS,
                "max_stall_recoveries": MAX_STALL_RECOVERIES,
                "stall_timeout_seconds": STALL_TIMEOUT_SECONDS,
            },
            "missions_inspected": len(mission_reports),
            "mission_reports": mission_reports,
            "findings": all_findings,
            "critical_issues": critical_count,
            "high_issues": high_count,
            "production_ready": critical_count == 0 and high_count == 0 and isolation == "PASSED",
            "safe_for_autonomous_execution": critical_count == 0 and isolation == "PASSED",
            "mutation_ledger": {
                "missions_created": 0, "plans_created": 0, "decisions_created": 0, "materializations_created": 0,
                "execution_requests_created": 0, "delegations_created": 0, "worker_claims_created": 0, "artifacts_created": 0,
                "repository_writes": 0, "state_changing_events": 0, "specialist_invocations": 0, "auto_continue_triggers": 0,
                "external_side_effects": 0, "read_only_isolation_integrity": isolation
            }
        }

class MissionEngine:

    @staticmethod
    def _plan_number(version: str) -> int:
        return MissionInvariantValidator._plan_number(version)

    @staticmethod
    def _active_plan_milestones(mission: PersistentMission) -> List[Dict[str, Any]]:
        return [m for m in mission.milestones if m.get("plan_version", "v1") == mission.plan_version]

    @staticmethod
    def _update_progress(mission: PersistentMission):
        if mission.mission_objective_achieved:
            mission.progress = 100
            return
        total = max(1, len(mission.milestones))
        completed = sum(1 for m in mission.milestones if m.get("status") == "Completed")
        mission.progress = min(99, round((completed / total) * 99))

    @staticmethod
    def _escalate(mission: PersistentMission, reason: str):
        mission.status = "WAITING_DIRECTOR"
        mission.progression_state = "ESCALATION_REQUIRED"
        mission.execution_state = "BLOCKED"
        mission.dispatch_state = "NONE"
        mission.auto_continue_status = "STOPPED"
        mission.health = "AT_RISK"
        mission.escalation_reason = reason
        mission.terminal_reason = reason
        mission.loop_safety_status = reason
        mission.events.append(f"[{datetime.now(timezone.utc).isoformat()}] MISSION_ESCALATED: {reason}.")

    async def _bootstrap_mission(self, mission: PersistentMission, context_company_id: str):
        recurse = False
        with mission_registry.locked():
            mission = mission_registry.missions.get(mission.mission_id, mission)
            if mission.status != "ACTIVE":
                return

            plan_number = self._plan_number(mission.plan_version)
            if plan_number > MAX_PLAN_LOOPS + 1 or mission.replan_count > MAX_PLAN_LOOPS:
                self._escalate(mission, "REPLAN_LIMIT_REACHED")
                mission_registry.save_mission(mission)
                return

            guard = MissionCompletionGuard()
            verified_count = guard.refresh_progress(mission)
            active_plan = self._active_plan_milestones(mission)
            pending = [m for m in active_plan if m.get("status", "Pending") in {"Pending", "READY"}]

            if active_plan and not pending:
                guard_status, objective_achieved = guard.evaluate_completion(mission)
                if objective_achieved:
                    mission.status = "COMPLETED"
                    mission.plan_status = "COMPLETED"
                    mission.mission_objective_achieved = True
                    mission.progression_state = "COMPLETED"
                    mission.execution_state = "COMPLETED"
                    mission.dispatch_state = "NONE"
                    mission.auto_continue_status = "STOPPED"
                    mission.next_eligible_action = "None"
                    mission.terminal_reason = "SUCCESS_CRITERIA_VERIFIED"
                    mission.last_progress_at = datetime.now(timezone.utc).isoformat()
                    self._update_progress(mission)
                    mission.events.append(f"[{datetime.now(timezone.utc).isoformat()}] MISSION_COMPLETED: {guard_status}.")
                    mission_registry.save_mission(mission)
                    return

                mission.plan_status = "COMPLETED"
                if mission.mission_type == "ARTIFACT_PRODUCTION":
                    self._escalate(mission, "ARTIFACT_SUCCESS_CRITERIA_UNMET")
                    self._update_progress(mission)
                    mission_registry.save_mission(mission)
                    return
                if verified_count <= mission.last_verified_success_count:
                    mission.zero_progress_count += 1
                else:
                    mission.zero_progress_count = 0
                mission.last_verified_success_count = verified_count

                strategy = "communication_outreach"
                repeated = sum(1 for item in mission.strategy_history if item == strategy)
                mission.repeated_strategy_count = repeated
                if mission.replan_count >= MAX_PLAN_LOOPS:
                    self._escalate(mission, "REPLAN_LIMIT_REACHED")
                elif mission.zero_progress_count >= MAX_ZERO_PROGRESS_PLANS:
                    self._escalate(mission, "ZERO_PROGRESS_LIMIT_REACHED")
                elif repeated >= MAX_REPEATED_STRATEGIES:
                    self._escalate(mission, "REPEATED_STRATEGY_LIMIT_REACHED")
                elif mission.outreach_batch_count >= MAX_OUTREACH_BATCHES:
                    self._escalate(mission, "OUTREACH_BATCH_LIMIT_REACHED")
                else:
                    next_version = f"v{plan_number + 1}"
                    numeric_ids = [int(str(m.get("milestone_id", "m0")).lstrip("m")) for m in mission.milestones if str(m.get("milestone_id", "")).lstrip("m").isdigit()]
                    new_milestone_id = f"m{max(numeric_ids or [0]) + 1}"
                    next_batch = mission.outreach_batch_count + 1
                    MissionPlanActivationService().activate_new_plan(mission, next_version, [{
                        "milestone_id": new_milestone_id,
                        "name": f"Lead Nurturing & Outreach Batch {next_batch}",
                        "status": "Pending",
                        "progress": 0,
                        "phase": f"Phase {plan_number + 1} - Strategic Expansion",
                        "strategy_key": strategy,
                    }])
                    mission.outreach_batch_count = next_batch
                    mission.strategy_history.append(strategy)
                    mission.repeated_strategy_count = sum(1 for item in mission.strategy_history if item == strategy)
                    mission.events.append(f"[{datetime.now(timezone.utc).isoformat()}] PLAN_COMPLETED_OBJECTIVE_UNMET: Activated {next_version}.")
                    recurse = True
                self._update_progress(mission)
                mission_registry.save_mission(mission)
            elif not active_plan:
                self._escalate(mission, "ACTIVE_PLAN_HAS_NO_MILESTONES")
                mission_registry.save_mission(mission)
                return
            else:
                current = pending[0]
                mission.current_phase = current.get("phase", mission.current_phase)
                mission.current_milestone = current.get("name", "Unknown Milestone")
                milestone_id = current.get("milestone_id", "m1")

                if any(d.get("milestone_id") == milestone_id and d.get("status") in {"Pending", "Claimed", "Running"} for d in mission.active_delegations):
                    return
                if any(r.get("milestone_id") == milestone_id and r.get("status") in MissionInvariantValidator.ACTIVE_REQUEST_STATES for r in mission.execution_requests):
                    return

                existing_decision = next((
                    d for d in reversed(mission.progression_decisions)
                    if d.get("milestone_id") == milestone_id and d.get("plan_version") == mission.plan_version
                ), None)
                if existing_decision:
                    MissionProgressionMaterializer().materialize(mission, existing_decision)
                    mission_registry.save_mission(mission)
                    return

                current["status"] = "READY"
                actions = {
                    "m1": ("Discover qualified restaurant prospects", "lead_intelligence", "LeadArtifact"),
                    "m2": ("Analyze website and evaluate opportunities", "website_intelligence", "WebsiteArtifact"),
                    "m3": ("Evaluate business opportunity & map services", "opportunity_intelligence", "OpportunityArtifact"),
                    "m4": ("Design business solution architecture", "business_solution_architect", "BusinessSolutionArtifact"),
                }
                if all(current.get(key) for key in ("action", "target_intelligence", "expected_artifact")):
                    action_desc = current["action"]
                    target_agent = current["target_intelligence"]
                    expected_artifact = current["expected_artifact"]
                elif milestone_id in actions:
                    action_desc, target_agent, expected_artifact = actions[milestone_id]
                elif str(milestone_id).lstrip("m").isdigit() and int(str(milestone_id).lstrip("m")) >= 5:
                    action_desc = f"Initiate batch {mission.outreach_batch_count} automated outreach"
                    target_agent, expected_artifact = "communication_intelligence", "CommunicationArtifact"
                else:
                    self._escalate(mission, "UNRESOLVED_PLAN_ACTION")
                    mission_registry.save_mission(mission)
                    return

                decision = {
                    "decision_id": f"dec_{uuid.uuid4().hex[:8]}",
                    "milestone_id": milestone_id,
                    "plan_version": mission.plan_version,
                    "selected_action": action_desc,
                    "target_intelligence": target_agent,
                    "expected_artifact": expected_artifact,
                    "target_count": current.get("target_count", mission.success_criteria.get("required", 1)),
                    "strategy_key": current.get("strategy_key", milestone_id),
                    "reason": f"{current.get('name')} milestone dependencies satisfied.",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                if current.get("discovery_scope"):
                    decision["discovery_scope"] = dict(current["discovery_scope"])
                mission.progression_decisions.append(decision)
                mission.next_eligible_action = action_desc
                MissionProgressionMaterializer().materialize(mission, decision)
                mission_registry.save_mission(mission)

        if recurse:
            refreshed = mission_registry.get_mission(mission.mission_id)
            if refreshed:
                await self._bootstrap_mission(refreshed, context_company_id)

    async def approve_materialization(self, mission_id: str, materialization_id: str) -> bool:
        with mission_registry.locked():
            mission = mission_registry.missions.get(mission_id)
            if not mission or mission.status != "ACTIVE":
                return False
            materialization = next((m for m in mission.progression_materializations if m.get("materialization_id") == materialization_id), None)
            if not materialization or materialization.get("status") != "APPROVAL_REQUIRED":
                return False
            decision = next((d for d in mission.progression_decisions if d.get("decision_id") == materialization.get("decision_id")), None)
            if not decision:
                return False
            materialization["status"] = "EXECUTION_READY"
            materialization["approved_at"] = datetime.now(timezone.utc).isoformat()
            if not any(r.get("materialization_id") == materialization_id for r in mission.execution_requests):
                request_id = f"mer_{uuid.uuid4().hex[:8]}"
                mission.execution_requests.append({
                    "execution_request_id": request_id,
                    "materialization_id": materialization_id,
                    "decision_id": decision["decision_id"],
                    "status": "READY",
                    "attempt": 1,
                    "idempotency_key": f"request:{materialization_id}:1",
                    "capability": "client_outreach",
                    "resolution_strategy": "dynamic_resolution",
                    "target_intelligence": decision["target_intelligence"],
                    "selected_action": decision["selected_action"],
                    "milestone_id": decision["milestone_id"],
                    "plan_version": decision["plan_version"],
                    "expected_artifact": decision["expected_artifact"],
                    "target_count": decision.get("target_count", 1),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            mission.progression_state = "READY"
            mission.dispatch_state = "AWAITING_DISPATCH"
            mission.auto_continue_status = "RUNNING"
            mission_registry.save_mission(mission)
            return True

    async def reject_materialization(self, mission_id: str, materialization_id: str, reason: str) -> bool:
        with mission_registry.locked():
            mission = mission_registry.missions.get(mission_id)
            if not mission or mission.status != "ACTIVE":
                return False
            materialization = next((m for m in mission.progression_materializations if m.get("materialization_id") == materialization_id), None)
            if not materialization or materialization.get("status") != "APPROVAL_REQUIRED":
                return False
            
            materialization["status"] = "REJECTED"
            materialization["rejected_at"] = datetime.now(timezone.utc).isoformat()
            
            MissionExecutionStateReconciler.block(mission, f"APPROVAL_REJECTED: {reason}")
            mission.status = "WAITING_DIRECTOR"
            mission_registry.save_mission(mission)
            return True

    async def revoke_materialization(self, mission_id: str, materialization_id: str, reason: str) -> bool:
        with mission_registry.locked():
            mission = mission_registry.missions.get(mission_id)
            if not mission or mission.status != "ACTIVE":
                return False
            materialization = next((m for m in mission.progression_materializations if m.get("materialization_id") == materialization_id), None)
            if not materialization or materialization.get("status") != "EXECUTION_READY":
                # Can only revoke if it hasn't actually started running
                return False
            
            # Verify it hasn't actually been picked up
            request = next((r for r in mission.execution_requests if r.get("materialization_id") == materialization_id), None)
            if request and request.get("status") not in {"READY", "CLAIMED"}:
                # Already dispatched/running, cannot revoke safely here
                return False
                
            materialization["status"] = "REVOKED"
            materialization["revoked_at"] = datetime.now(timezone.utc).isoformat()
            
            if request:
                request["status"] = "BLOCKED"
                request["error"] = f"APPROVAL_REVOKED: {reason}"
                
            MissionExecutionStateReconciler.block(mission, f"APPROVAL_REVOKED: {reason}")
            mission.status = "WAITING_DIRECTOR"
            mission_registry.save_mission(mission)
            return True

    async def process_delegation_completion(self, mission_id: str, delegation_id: str, artifact_evidence: Any) -> bool:
        if isinstance(artifact_evidence, str):
            artifact_evidence = {"artifact_type": artifact_evidence, "verified": False}
        artifact_evidence = dict(artifact_evidence or {})
        evidence_source = normalize_evidence_source(
            artifact_evidence.get("evidence_source")
            or artifact_evidence.get("provider_mode")
            or artifact_evidence.get("mode")
            or (artifact_evidence.get("provenance") or {}).get("evidence_source")
        )
        artifact_evidence["evidence_source"] = evidence_source.value
        artifact_evidence["simulation_evidence"] = bool(
            artifact_evidence.get("simulation_evidence", is_simulation_evidence(evidence_source))
        )

        with mission_registry.locked():
            mission = mission_registry.missions.get(mission_id)
            if not mission:
                return False
            if any(d.get("delegation_id") == delegation_id and d.get("status") == "Completed" for d in mission.delegation_history):
                return True
            delegation = next((d for d in mission.active_delegations if d.get("delegation_id") == delegation_id), None)
            if not delegation:
                return False
            expected_type = delegation.get("expected_artifact")
            error: Optional[str] = None
            valid_evidence = bool(
                artifact_evidence.get("artifact_id")
                and artifact_evidence.get("artifact_type") == expected_type
                and artifact_evidence.get("verified") is True
            )
            if valid_evidence and mission.verification_mode in {
                LIVE_EVIDENCE_CANARY, QUALIFIED_LEAD_CANARY
            }:
                source_metadata = artifact_evidence.get("source_metadata") or {}
                valid_evidence = bool(
                    evidence_source == EvidenceSource.LIVE_EXTERNAL
                    and artifact_evidence.get("simulation_evidence") is False
                    and artifact_evidence.get("source_provider")
                    and source_metadata.get("request_succeeded") is True
                )
                if not valid_evidence:
                    error = "LIVE_EVIDENCE_UNAVAILABLE"
            if (
                valid_evidence
                and expected_type == "LeadArtifact"
                and mission.verification_mode == PRODUCTION_BUSINESS_SUCCESS
            ):
                source_metadata = artifact_evidence.get("source_metadata") or {}
                valid_evidence = bool(
                    evidence_source == EvidenceSource.LIVE_EXTERNAL
                    and artifact_evidence.get("simulation_evidence") is False
                    and artifact_evidence.get("source_provider")
                    and source_metadata.get("request_succeeded") is True
                )
                if not valid_evidence:
                    error = "PRODUCTION_EVIDENCE_UNAVAILABLE"
            if (
                valid_evidence
                and expected_type == "LeadArtifact"
                and mission.verification_mode in {
                    PRODUCTION_BUSINESS_SUCCESS, QUALIFIED_LEAD_CANARY
                }
                and not is_entity_qualified(artifact_evidence.get("entity_qualification"))
            ):
                valid_evidence = False
                error = "BUSINESS_ENTITY_UNVERIFIED"
            if (
                valid_evidence
                and mission.verification_mode == PRODUCTION_BUSINESS_SUCCESS
                and expected_type != "LeadArtifact"
                and not qualifies_for_production_success(evidence_source)
            ):
                valid_evidence = False
                error = "PRODUCTION_EVIDENCE_UNAVAILABLE"
            if not valid_evidence:
                error = error or (
                    f"ARTIFACT_VALIDATION_FAILED: expected {expected_type}, received {artifact_evidence.get('artifact_type', 'None')}"
                )
            else:
                now = datetime.now(timezone.utc).isoformat()
                request = next((r for r in mission.execution_requests if r.get("execution_request_id") == delegation.get("execution_request_id")), None)
                claim = next((c for c in mission.worker_claims if c.get("worker_claim_id") == delegation.get("worker_claim_id")), None)
                if not request or not claim or claim.get("status") != "ACTIVE":
                    error = "EXECUTION_LINEAGE_VALIDATION_FAILED"
                    valid_evidence = False
                else:
                    request["status"] = "COMPLETED"
                    request["completed_at"] = now
                    claim["status"] = "COMPLETED"
                    claim["completed_at"] = now
                    delegation["status"] = "Completed"
                    delegation["completed_at"] = now
                    lineage = {
                        "artifact_id": artifact_evidence["artifact_id"],
                        "artifact_type": artifact_evidence["artifact_type"],
                        "verified": True,
                        "verification_method": artifact_evidence.get("verification_method", "specialist_result"),
                        "mission_id": mission_id,
                        "plan_version": delegation.get("plan_version"),
                        "milestone_id": delegation.get("milestone_id"),
                        "decision_id": delegation.get("decision_id"),
                        "materialization_id": delegation.get("materialization_id"),
                        "execution_request_id": delegation.get("execution_request_id"),
                        "delegation_id": delegation_id,
                        "worker_claim_id": claim.get("worker_claim_id"),
                        "specialist": delegation.get("target_agent", "Unknown"),
                        "evidence_source": artifact_evidence["evidence_source"],
                        "simulation_evidence": artifact_evidence["simulation_evidence"],
                        "source_provider": artifact_evidence.get("source_provider"),
                        "source_metadata": {
                            key: value
                            for key, value in dict(artifact_evidence.get("source_metadata") or {}).items()
                            if key in {
                                "provider", "endpoint", "retrieval_type", "request_succeeded",
                                "request_count", "requested_at", "result_count", "query", "max_results",
                                "raw_result_count", "normalized_result_count", "usable_result_count",
                                "qualified_artifact_target", "candidate_scan_limit", "query_mode", "candidate_count_examined",
                                "qualified_candidate_index", "candidate_diagnostics",
                            }
                        },
                        "source_reference": artifact_evidence.get("source_reference"),
                        "source_url": artifact_evidence.get("source_url") or artifact_evidence.get("source_reference"),
                        "source_type": artifact_evidence.get("source_type"),
                        "business_name": artifact_evidence.get("business_name"),
                        "business_category": artifact_evidence.get("business_category"),
                        "business_location": artifact_evidence.get("business_location"),
                        "business_domain": artifact_evidence.get("business_domain"),
                        "entity_qualification": dict(artifact_evidence.get("entity_qualification") or {}),
                        "qualification_reasons": list(artifact_evidence.get("qualification_reasons") or []),
                        "created_at": now,
                    }
                    if not any(a.get("artifact_id") == lineage["artifact_id"] for a in mission.artifact_lineage):
                        mission.artifact_lineage.append(lineage)
                    mission.delegation_history.append(dict(delegation))
                    mission.active_delegations = [d for d in mission.active_delegations if d.get("delegation_id") != delegation_id]
                    mission.last_completed_delegation = delegation.get("target_agent", "Unknown")
                    mission.last_result_artifact = artifact_evidence["artifact_type"]
                    milestone_id = delegation.get("milestone_id", "m1")
                    if milestone_id not in mission.action_history:
                        mission.action_history.append(milestone_id)
                    for milestone in mission.milestones:
                        if milestone.get("milestone_id") == milestone_id:
                            milestone["progress"] = 100
                            milestone["status"] = "Completed"
                            milestone["completed_at"] = now
                            milestone["artifact_id"] = lineage["artifact_id"]
                            break
                    mission.execution_state = "READY"
                    mission.progression_state = "READY"
                    mission.dispatch_state = "NONE"
                    mission.auto_continue_status = "RUNNING"
                    mission.last_progress_at = now
                    mission.retry_count = 0
                    mission.stall_recovery_count = 0
                    mission.stall_detector_status = "Clear"
                    self._update_progress(mission)
                    MissionCompletionGuard().refresh_progress(mission)
                    mission_registry.save_mission(mission)

        if not valid_evidence:
            await self.process_execution_failure(
                mission_id,
                delegation_id=delegation_id,
                error=error,
                failure_evidence=artifact_evidence,
            )
            return False
        refreshed = mission_registry.get_mission(mission_id)
        if refreshed:
            await self._bootstrap_mission(refreshed, "internal_napstertec")
        return True

    async def process_execution_failure(
        self,
        mission_id: str,
        error: str,
        delegation_id: Optional[str] = None,
        execution_request_id: Optional[str] = None,
        failure_evidence: Optional[Dict[str, Any]] = None,
    ) -> bool:
        with mission_registry.locked():
            mission = mission_registry.missions.get(mission_id)
            if not mission:
                return False
            delegation = next((d for d in mission.active_delegations if d.get("delegation_id") == delegation_id), None) if delegation_id else None
            request_id = execution_request_id or (delegation.get("execution_request_id") if delegation else None)
            request = next((r for r in mission.execution_requests if r.get("execution_request_id") == request_id), None)
            now = datetime.now(timezone.utc).isoformat()
            safe_failure_evidence = _sanitize_failure_evidence(failure_evidence)
            error_upper = str(error or "").upper()
            entity = safe_failure_evidence.get("entity_qualification") or {}
            entity_status = str(entity.get("status") or "").upper()
            source_type = str(
                safe_failure_evidence.get("source_type") or entity.get("source_type") or ""
            ).upper()
            deterministic_entity_rejection = bool(
                mission.verification_mode == QUALIFIED_LEAD_CANARY
                and (
                    "BUSINESS_ENTITY_UNVERIFIED" in error_upper
                    or entity_status in _TERMINAL_ENTITY_STATUSES
                    or source_type in _TERMINAL_ENTITY_SOURCE_TYPES
                )
            )
            terminal_live_evidence_failure = bool(
                mission.verification_mode in {LIVE_EVIDENCE_CANARY, QUALIFIED_LEAD_CANARY}
                and (
                    "LIVE_EVIDENCE_UNAVAILABLE" in error_upper
                    or "ARTIFACT_EVIDENCE_MISSING" in error_upper
                    or "INVALID_PROVENANCE" in error_upper
                    or "MALFORMED_PROVIDER_EVIDENCE" in error_upper
                )
            )
            discovery_scope_incomplete = bool(
                mission.verification_mode == QUALIFIED_LEAD_CANARY
                and "DISCOVERY_SCOPE_INCOMPLETE" in error_upper
            )
            transient_retryable = any(
                marker in error_upper for marker in _TRANSIENT_RETRYABLE_MARKERS
            )
            if discovery_scope_incomplete:
                failure_classification = "TERMINAL_SCOPE_CONFIGURATION_FAILURE"
            elif deterministic_entity_rejection:
                failure_classification = "TERMINAL_BUSINESS_REJECTION"
            elif terminal_live_evidence_failure:
                failure_classification = "TERMINAL_LIVE_EVIDENCE_FAILURE"
            elif mission.verification_mode == QUALIFIED_LEAD_CANARY:
                failure_classification = "TERMINAL_CANARY_EXECUTION_FAILURE"
            elif transient_retryable:
                failure_classification = "TRANSIENT_RETRYABLE"
            else:
                failure_classification = "NON_RETRYABLE_EXECUTION_FAILURE"
            if safe_failure_evidence:
                safe_failure_evidence["artifact_acceptance"] = "REJECTED"
                safe_failure_evidence["rejection_reason"] = error
                safe_failure_evidence["failure_classification"] = failure_classification
            if delegation:
                delegation["status"] = "FAILED"
                delegation["failed_at"] = now
                delegation["error"] = error
                delegation["failure_classification"] = failure_classification
                if safe_failure_evidence:
                    delegation["failure_evidence"] = safe_failure_evidence
                claim = next((c for c in mission.worker_claims if c.get("worker_claim_id") == delegation.get("worker_claim_id")), None)
                if claim:
                    claim["status"] = "FAILED"
                    claim["completed_at"] = now
                    claim["health"] = "FAILED"
                mission.delegation_history.append(dict(delegation))
                mission.active_delegations = [d for d in mission.active_delegations if d.get("delegation_id") != delegation.get("delegation_id")]
            if request:
                request["status"] = "FAILED"
                request["failed_at"] = now
                request["error"] = error
                request["failure_classification"] = failure_classification
                if safe_failure_evidence:
                    request["failure_evidence"] = safe_failure_evidence
            mission.last_error = error
            mission.events.append(
                f"[{now}] MISSION_EXECUTION_FAILED: {error} ({failure_classification})."
            )

            attempt = int(request.get("attempt", 1)) if request else MAX_EXECUTION_RETRIES
            if discovery_scope_incomplete:
                self._escalate(mission, "DISCOVERY_SCOPE_INCOMPLETE")
                mission.last_error = "DISCOVERY_SCOPE_INCOMPLETE"
            elif deterministic_entity_rejection:
                self._escalate(mission, "BUSINESS_ENTITY_UNVERIFIED")
                mission.last_error = "BUSINESS_ENTITY_UNVERIFIED"
            elif terminal_live_evidence_failure:
                self._escalate(mission, "LIVE_EVIDENCE_UNAVAILABLE")
                mission.last_error = "LIVE_EVIDENCE_UNAVAILABLE"
            elif mission.verification_mode == QUALIFIED_LEAD_CANARY:
                self._escalate(mission, "QUALIFIED_LEAD_EXECUTION_FAILED")
            elif transient_retryable and request and attempt < MAX_EXECUTION_RETRIES:
                mission.retry_count += 1
                retry = dict(request)
                retry_id = f"mer_{uuid.uuid4().hex[:8]}"
                retry.update({
                    "execution_request_id": retry_id,
                    "status": "READY",
                    "attempt": attempt + 1,
                    "idempotency_key": f"request:{request.get('materialization_id')}:{attempt + 1}",
                    "created_at": now,
                    "retry_of": request.get("execution_request_id"),
                })
                retry.pop("delegation_id", None)
                retry.pop("failed_at", None)
                retry.pop("error", None)
                retry.pop("failure_classification", None)
                retry.pop("failure_evidence", None)
                mission.execution_requests.append(retry)
                mission.progression_state = "READY"
                mission.execution_state = "READY"
                mission.dispatch_state = "AWAITING_DISPATCH"
                mission.auto_continue_status = "RUNNING"
            elif transient_retryable:
                self._escalate(mission, "EXECUTION_RETRY_LIMIT_REACHED")
            else:
                self._escalate(mission, "NON_RETRYABLE_EXECUTION_FAILURE")
            mission_registry.save_mission(mission)
            return True

    async def record_success_evidence(self, mission_id: str, evidence: Dict[str, Any]) -> bool:
        with mission_registry.locked():
            mission = mission_registry.missions.get(mission_id)
            if not mission:
                return False
            record = dict(evidence)
            record.setdefault("evidence_id", f"evi_{uuid.uuid4().hex[:8]}")
            record.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
            source = normalize_evidence_source(record.get("evidence_source"))
            record["evidence_source"] = source.value
            record["simulation_evidence"] = bool(
                record.get("simulation_evidence", is_simulation_evidence(source))
            )
            identity = record.get("client_id") or record.get("lead_id") or record["evidence_id"]
            existing = next((
                e for e in mission.success_evidence
                if (e.get("client_id") or e.get("lead_id") or e.get("evidence_id")) == identity
            ), None)
            if existing:
                if existing.get("verified") or not record.get("verified"):
                    return False
                existing.update(record)
            else:
                mission.success_evidence.append(record)
            MissionCompletionGuard().refresh_progress(mission)
            mission.last_progress_at = datetime.now(timezone.utc).isoformat()
            mission_registry.save_mission(mission)
        refreshed = mission_registry.get_mission(mission_id)
        if refreshed and all(m.get("status") == "Completed" for m in self._active_plan_milestones(refreshed)):
            await self._bootstrap_mission(refreshed, "internal_napstertec")
        return True

    async def _advance_structural_canary_chain(self, mission_id: str) -> None:
        """Run one bounded internal canary chain before returning its snapshot."""
        from app.engine.autonomous_worker import autonomous_worker

        for _ in range(MAX_EXECUTION_RETRIES):
            current = mission_registry.get_mission(mission_id)
            if not current or current.status != "ACTIVE":
                break

            progressed = False
            if any(request.get("status") == "READY" for request in current.execution_requests):
                dispatched = await MissionExecutionDispatcher().process_ready_requests(mission_id)
                progressed = dispatched > 0

            current = mission_registry.get_mission(mission_id)
            if not current or current.status != "ACTIVE":
                break
            if any(
                delegation.get("status") == "Pending"
                for delegation in current.active_delegations
            ):
                progressed = await autonomous_worker.process_mission_once(mission_id) or progressed

            current = mission_registry.get_mission(mission_id)
            if not current or current.status != "ACTIVE":
                break

            if not progressed:
                with mission_registry.locked():
                    persisted = mission_registry.missions.get(mission_id)
                    if not persisted or persisted.status != "ACTIVE":
                        break
                    claimed_without_delegation = any(
                        request.get("status") == "CLAIMED"
                        and not any(
                            delegation.get("execution_request_id") == request.get("execution_request_id")
                            for delegation in persisted.active_delegations
                        )
                        for request in persisted.execution_requests
                    )
                    pending_without_worker = any(
                        delegation.get("status") == "Pending"
                        for delegation in persisted.active_delegations
                    )
                    changed = False
                    if claimed_without_delegation:
                        changed = MissionExecutionStateReconciler.block(
                            persisted, "EXECUTION_REQUEST_UNDISPATCHED"
                        )
                    elif pending_without_worker:
                        changed = MissionExecutionStateReconciler.block(
                            persisted, "WORKER_UNAVAILABLE"
                        )
                    else:
                        changed = MissionExecutionStateReconciler.ensure_truthful(persisted)
                    if changed:
                        mission_registry.save_mission(persisted)
                break

        with mission_registry.locked():
            current = mission_registry.missions.get(mission_id)
            if current and current.status == "ACTIVE":
                if MissionExecutionStateReconciler.ensure_truthful(current):
                    mission_registry.save_mission(current)

    async def process_mission_request(self, mode: str, query: str, session_id: str) -> MissionArtifact:
        mission: Optional[PersistentMission] = None
        recs = ""
        if mode == "MISSION CREATION MODE":
            mission = mission_registry.create_mission(raw_query=query)
            await self._bootstrap_mission(mission, "internal_napstertec")
            mission = mission_registry.get_mission(mission.mission_id)
            if (
                mission
                and mission.mission_type == "ARTIFACT_PRODUCTION"
                and (
                    (mission.verification_mode == STRUCTURAL_CANARY and mission.simulation_mode)
                    or (
                        mission.verification_mode in {LIVE_EVIDENCE_CANARY, QUALIFIED_LEAD_CANARY}
                        and not mission.simulation_mode
                    )
                )
            ):
                await self._advance_structural_canary_chain(mission.mission_id)
                mission = mission_registry.get_mission(mission.mission_id)
            if mission and mission.status == "COMPLETED":
                recs = "Structural canary execution chain completed."
            elif mission and mission.execution_state == "BLOCKED":
                recs = f"Mission execution blocked: {mission.last_error}."
            else:
                recs = "Mission created. Plan v1 active."
        elif mode in ["MISSION STATUS MODE", "MISSION CONTROL MODE"]:
            mission = mission_registry.get_mission_by_hint(query)
            if not mission:
                raise ValueError("MissionStateValidationFailed: MissionNotFound.")
            if mode == "MISSION CONTROL MODE":
                if "pause" in query.lower():
                    mission = mission_registry.update_mission_status(mission.mission_id, "PAUSED")
                    recs = "Mission paused."
                elif "resume" in query.lower():
                    mission = mission_registry.update_mission_status(mission.mission_id, "ACTIVE")
                    if mission:
                        await self._bootstrap_mission(mission, "internal_napstertec")
                        mission = mission_registry.get_mission(mission.mission_id)
                    recs = "Mission resumed."
            else:
                recs = "Mission status read without mutation."
        if not mission:
            raise ValueError("MissionStateValidationFailed: MissionNotFound.")

        findings = MissionInvariantValidator().validate(mission)
        truth = MissionExecutionEvidenceResolver().resolve_execution_truth(mission)
        guard = MissionCompletionGuard()
        guard_status, _ = guard.evaluate_completion(mission)
        verified_count = guard.verified_success_count(mission)
        latest_lineage = mission.artifact_lineage[-1] if mission.artifact_lineage else {}
        latest_failure_evidence = next((
            dict(request.get("failure_evidence") or {})
            for request in reversed(mission.execution_requests)
            if request.get("failure_evidence")
        ), {})
        report_evidence = latest_lineage or latest_failure_evidence
        evidence_source = normalize_evidence_source(report_evidence.get("evidence_source")).value
        external_side_effects = "NONE" if not mission.external_operations else "RECORDED"
        canary_pipeline_verified = bool(
            mission.verification_mode in {
                STRUCTURAL_CANARY, LIVE_EVIDENCE_CANARY, QUALIFIED_LEAD_CANARY
            }
            and mission.mission_objective_achieved
            and latest_lineage
        )
        real_world_business_evidence_verified = bool(
            mission.verification_mode in {PRODUCTION_BUSINESS_SUCCESS, QUALIFIED_LEAD_CANARY}
            and mission.mission_objective_achieved
        )
        current_record = next((m for m in mission.milestones if m.get("name") == mission.current_milestone), {})
        milestones = [
            MissionMilestone(
                milestone_id=m.get("milestone_id", "m1"),
                name=m.get("name", "Milestone"),
                description=m.get("description", "Strategic milestone step."),
                status=m.get("status", "Pending"),
                sequence=int(str(m.get("milestone_id", "m1")).lstrip("m")) if str(m.get("milestone_id", "m1")).lstrip("m").isdigit() else 1,
                success_criteria=m.get("success_criteria", "Verified artifact received."),
                progress=m.get("progress", 0),
            ) for m in mission.milestones
        ]
        high_or_critical = any(f["severity"] in {"CRITICAL", "HIGH"} for f in findings)
        return MissionArtifact(
            artifact_id=f"ms_art_{uuid.uuid4().hex[:8]}", agent_run_id=session_id, lead_id="internal_napstertec", mission_summary=f"Persistent orchestration loop for: {mission.title}",
            mission_id=mission.mission_id, objective_id=mission.objective_id,
            original_request=mission.original_request, objective=mission.objective, normalized_objective=mission.objective,
            status=mission.status, priority=mission.priority, autonomy_level=mission.autonomy_level, overall_progress=mission.progress, mission_health=mission.health,
            success_criteria_progress=mission.success_criteria_progress, mission_type=mission.mission_type,
            success_criterion=mission.success_criteria.get("criterion", "verified_won_clients"),
            target_count=max(1, int(mission.success_criteria.get("required", 1))), verified_count=verified_count,
            evidence_source=evidence_source, simulation_mode=mission.simulation_mode, terminal_reason=mission.terminal_reason,
            external_side_effects=external_side_effects, canary_pipeline_verified=canary_pipeline_verified,
            real_world_business_evidence_verified=real_world_business_evidence_verified,
            mission_objective_achieved=mission.mission_objective_achieved, current_phase=mission.current_phase,
            current_milestone=mission.current_milestone, current_milestone_status=current_record.get("status", "None"), milestones=milestones, plan_version=mission.plan_version, plan_status=mission.plan_status,
            historical_plans=mission.historical_plans, progression_state=mission.progression_state, execution_state=mission.execution_state, stall_detector_status=mission.stall_detector_status,
            loop_safety_status=mission.loop_safety_status, dispatch_state=mission.dispatch_state, director_plan_status="Executive Plan Linked", active_delegations=mission.active_delegations, execution_requests=mission.execution_requests, worker_claims=mission.worker_claims, external_operations=mission.external_operations,
            delegation_history=mission.delegation_history, artifact_lineage=mission.artifact_lineage, success_evidence=mission.success_evidence,
            replan_count=mission.replan_count, retry_count=mission.retry_count, repeated_strategy_count=mission.repeated_strategy_count, outreach_batch_count=mission.outreach_batch_count, zero_progress_count=mission.zero_progress_count,
            last_completed_delegation=mission.last_completed_delegation, next_eligible_action=mission.next_eligible_action, auto_continue_status=mission.auto_continue_status,
            governance_status="Active", budget_status="Within Thresholds", deadline_status="On Track", dependencies=[], blockers=[f["code"] for f in findings if f["severity"] in {"CRITICAL", "HIGH"}], risks=[], pending_approvals=[m["materialization_id"] for m in mission.progression_materializations if m.get("status") == "APPROVAL_REQUIRED"], recent_mission_events=mission.events[-5:],
            action_history=mission.action_history, progression_decisions=mission.progression_decisions, progression_materializations=mission.progression_materializations,
            completion_guard_status=guard_status, mission_completion_integrity="FAILED" if any(f["code"] == "UNVERIFIED_MISSION_COMPLETION" for f in findings) else "PASSED", progression_materialization_integrity="FAILED" if any("MATERIALIZATION" in f["code"] for f in findings) else "PASSED", mission_stall_integrity="PASSED" if mission.stall_detector_status == "Clear" else "FAILED", execution_state_integrity=truth["execution_state_integrity"],
            delegation_execution_integrity=truth["delegation_execution_integrity"], worker_claim_integrity=truth["worker_claim_integrity"], mission_dispatch_integrity="FAILED" if high_or_critical else "PASSED", next_evaluation="Event Triggered", recommended_next_action=recs,
            execution_metadata={
                "evaluation_method": "Mission Completion Guard & Orchestration",
                "plan_status": mission.plan_status,
                "plan_version": mission.plan_version,
                "mission_type": mission.mission_type,
                "verification_mode": mission.verification_mode,
                "success_criterion": mission.success_criteria.get("criterion"),
                "target_count": max(1, int(mission.success_criteria.get("required", 1))),
                "verified_count": verified_count,
                "evidence_source": evidence_source,
                "simulation_mode": mission.simulation_mode,
                "terminal_reason": mission.terminal_reason,
                "external_side_effects": external_side_effects,
                "canary_pipeline_verified": canary_pipeline_verified,
                "real_world_business_evidence_verified": real_world_business_evidence_verified,
                "artifact_provenance": latest_lineage,
                "failure_evidence": latest_failure_evidence,
                "invariant_findings": findings,
            }
        )


async def mission_outcome_reaction(event: BusinessEvent):
    mission_id = event.execution_metadata.get("mission_id") or (event.correlation_id if str(event.correlation_id).startswith("mis_") else None)
    if not mission_id:
        return
    await MissionEngine().record_success_evidence(mission_id, {
        "evidence_id": event.event_id,
        "event_type": event.event_type,
        "lead_id": event.lead_id,
        "verified": event.confidence >= 0.9,
        "confidence": event.confidence,
        "source": "business_event_bus",
    })


event_bus.subscribe("DEAL_WON", mission_outcome_reaction)