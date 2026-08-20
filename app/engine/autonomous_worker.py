"""
NapsterTec AI - Autonomous Mission Worker
Module: app/engine/autonomous_worker.py
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from app.engine.mission_engine import (
    mission_registry, PersistentMission, MissionExecutionDispatcher,
    MissionWorkCoordinator, MissionEngine, MissionSafetyReconciler,
    LIVE_EVIDENCE_CANARY, QUALIFIED_LEAD_CANARY,
)
from app.engine.event_bus import event_bus, BusinessEvent
from app.agent.agent_models import AgentContext, AgentPermission, AgentResult
from app.schemas.evidence import normalize_evidence_source, is_simulation_evidence

logger = logging.getLogger(__name__)

class AutonomousMissionWorker:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AutonomousMissionWorker, cls).__new__(cls)
            cls._instance.worker_id = f"worker_{uuid.uuid4().hex[:8]}"
            cls._instance.is_running = False
            cls._instance.execution_count = 0
            cls._instance.failure_count = 0
            cls._instance.specialist_executor = None
        return cls._instance

    async def start_worker_loop(self):
        if self.is_running: return
        quarantined = MissionSafetyReconciler().quarantine_unsafe_missions()
        if quarantined:
            logger.error("[Autonomous Mission Worker] Quarantined unsafe missions before startup: %s", quarantined)
        self.is_running = True
        logger.info(f"[Autonomous Mission Worker] {self.worker_id} started execution loop.")
        
        await event_bus.publish(BusinessEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}", event_type="MISSION_WORKER_STARTED", timestamp=datetime.now(timezone.utc).isoformat(),
            lead_id="internal_napstertec", business_name="NapsterTec", communication_id="", conversation_id="", correlation_id="", workflow_id="", channel="", evidence=f"Worker {self.worker_id} online.", confidence=1.0
        ))

        asyncio.create_task(self._poll_loop())

    async def stop_worker_loop(self):
        self.is_running = False
        logger.info(f"[Autonomous Mission Worker] {self.worker_id} stopping loop.")
        await event_bus.publish(BusinessEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}", event_type="MISSION_WORKER_STOPPED", timestamp=datetime.now(timezone.utc).isoformat(),
            lead_id="internal_napstertec", business_name="NapsterTec", communication_id="", conversation_id="", correlation_id="", workflow_id="", channel="", evidence=f"Worker {self.worker_id} offline.", confidence=1.0
        ))

    async def _poll_loop(self):
        while self.is_running:
            try:
                # 1. Mission Dispatcher Bridge: Scan for READY execution requests -> Dispatch to Director
                await MissionExecutionDispatcher().process_ready_requests()
                
                # 2. Worker Loop: Scan for PENDING delegations -> Execute the Specialist
                await self._process_pending_work()
            except Exception as e:
                logger.error(f"[Autonomous Mission Worker] Error in poll loop: {e}", exc_info=True)
                self.failure_count += 1
            await asyncio.sleep(5.0)

    async def _process_pending_work(self):
        for mission_id in mission_registry.snapshot().keys():
            await self.process_mission_once(mission_id)

    async def process_mission_once(self, mission_id: str) -> bool:
        if MissionSafetyReconciler().quarantine_unsafe_missions(mission_id):
            return False
        claimed = MissionWorkCoordinator().claim_pending_delegation(mission_id, self.worker_id)
        if not claimed:
            return False
        delegation, claim = claimed
        logger.info("[Autonomous Mission Worker] Claimed delegation %s for mission %s", delegation["delegation_id"], mission_id)
        await event_bus.publish(BusinessEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}", event_type="MISSION_WORK_CLAIMED", timestamp=datetime.now(timezone.utc).isoformat(),
            lead_id="internal_napstertec", business_name="NapsterTec", communication_id="", conversation_id="",
            correlation_id=mission_id, workflow_id=delegation["delegation_id"], channel="",
            evidence=f"Delegation {delegation['delegation_id']} claimed by {self.worker_id}", confidence=1.0,
            execution_metadata={"mission_id": mission_id, "worker_claim_id": claim["worker_claim_id"]},
        ))
        success = await self._dispatch_to_specialist(mission_id, delegation)
        if success:
            self.execution_count += 1
        else:
            self.failure_count += 1
        return success

    async def _dispatch_to_specialist(self, mission_id: str, delegation: Dict[str, Any]) -> bool:
        try:
            if not MissionWorkCoordinator().mark_delegation_running(mission_id, delegation["delegation_id"]):
                return False

            await event_bus.publish(BusinessEvent(
                event_id=f"evt_{uuid.uuid4().hex[:8]}", event_type="MISSION_DELEGATION_STARTED", timestamp=datetime.now(timezone.utc).isoformat(),
                lead_id="internal_napstertec", business_name="NapsterTec", communication_id="", conversation_id="",
                correlation_id=mission_id, workflow_id=delegation["delegation_id"], channel="",
                evidence=f"Executing {delegation['target_agent']}", confidence=1.0,
                execution_metadata={"mission_id": mission_id, "delegation_id": delegation["delegation_id"]},
            ))

            result = await self._execute_specialist(mission_id, delegation)
            if not result.success:
                error = "; ".join(result.errors) or result.final_output or "SPECIALIST_EXECUTION_FAILED"
                await MissionEngine().process_execution_failure(
                    mission_id,
                    delegation_id=delegation["delegation_id"],
                    error=error,
                    failure_evidence=self._extract_failure_evidence(result),
                )
                return False

            evidence = self._extract_artifact_evidence(result, delegation.get("expected_artifact"))
            if not evidence:
                await MissionEngine().process_execution_failure(
                    mission_id, delegation_id=delegation["delegation_id"], error="ARTIFACT_EVIDENCE_MISSING"
                )
                return False
            evidence.setdefault("verification_method", "registered_specialist_result")
            completed = await MissionEngine().process_delegation_completion(mission_id, delegation["delegation_id"], evidence)
            if completed:
                await event_bus.publish(BusinessEvent(
                    event_id=f"evt_{uuid.uuid4().hex[:8]}", event_type="MISSION_DELEGATION_COMPLETED", timestamp=datetime.now(timezone.utc).isoformat(),
                    lead_id="internal_napstertec", business_name="NapsterTec", communication_id="", conversation_id="",
                    correlation_id=mission_id, workflow_id=delegation["delegation_id"], channel="",
                    evidence=f"Verified artifact {evidence['artifact_id']} produced.", confidence=1.0,
                    execution_metadata={"mission_id": mission_id, "artifact_id": evidence["artifact_id"]},
                ))
            return completed
        except Exception as e:
            logger.error(f"[Autonomous Mission Worker] Specialist dispatch failed: {e}", exc_info=True)
            await MissionEngine().process_execution_failure(
                mission_id, delegation_id=delegation.get("delegation_id"), error=f"SPECIALIST_DISPATCH_FAILED: {e}"
            )
            return False

    async def _execute_specialist(self, mission_id: str, delegation: Dict[str, Any]) -> AgentResult:
        if self.specialist_executor:
            return await self.specialist_executor(mission_id, delegation)
        # Runtime import avoids coupling engine startup to the API composition root.
        from app.agent.agent_registry import agent_registry
        from app.api.endpoints import tool_manager

        specialist = agent_registry.get_agent(delegation.get("target_agent"))
        if not specialist:
            return AgentResult(
                success=False, agent_name=str(delegation.get("target_agent")), session_id=f"ses_{uuid.uuid4().hex[:8]}",
                errors=["TARGET_SPECIALIST_NOT_REGISTERED"],
            )
        specialist.inject_dependencies(tool_manager=tool_manager)
        context = self._build_specialist_context(mission_id, delegation)
        return await specialist.run(context)

    @staticmethod
    def _build_specialist_context(mission_id: str, delegation: Dict[str, Any]) -> AgentContext:
        verification_mode = delegation.get("verification_mode")
        live_evidence_canary = verification_mode == LIVE_EVIDENCE_CANARY
        qualified_lead_canary = verification_mode == QUALIFIED_LEAD_CANARY
        read_only_live_discovery = live_evidence_canary or qualified_lead_canary
        discovery_scope = dict(delegation.get("discovery_scope") or {})
        planner_query = discovery_scope.get("category")
        planner_location = discovery_scope.get("location")
        if not qualified_lead_canary:
            planner_query = planner_query or delegation.get("objective", "")
            planner_location = planner_location or "Global"
            
        # SPRINT 3E: Strict Privilege subsetting instead of hardcoding widening
        granted_permissions = {AgentPermission.READ, AgentPermission.WRITE}
        
        target_agent = delegation.get("target_agent")
        if target_agent == "lead_intelligence":
            granted_permissions.add(AgentPermission.READ_EXTERNAL_DISCOVERY)
            
        # ONLY explicitly listed deployments / communicators get protected permissions
        if target_agent == "communication_intelligence":
            granted_permissions.add(AgentPermission.OUTREACH)
            granted_permissions.add(AgentPermission.EMAIL)
        elif target_agent == "deployment_intelligence":
            granted_permissions.add(AgentPermission.DEPLOYMENT)
        elif target_agent == "sales_intelligence":
            granted_permissions.add(AgentPermission.CRM)
        elif target_agent == "publishing_intelligence":
            granted_permissions.add(AgentPermission.PUBLISHING)

        runtime_metadata: Dict[str, Any] = {}
        if read_only_live_discovery:
            runtime_metadata = {
                "authority_scope": "READ_EXTERNAL_DISCOVERY",
                "blocked_tools": ["lead_upsert"],
                "forbidden_tool_permissions": [
                    "external_api",
                    "write_external",
                    "outreach",
                    "email",
                    "crm",
                    "deployment",
                    "publishing",
                    "financial_commitment",
                    "destructive_action"
                ],
                "external_write_allowed": False,
                "outreach_allowed": False,
            }

        return AgentContext(
            task=delegation.get("objective", "Execute mission delegation"),
            session_id=f"ses_{uuid.uuid4().hex[:8]}",
            planner_output={
                "query": planner_query or "",
                "location": planner_location or "",
                "discovery_scope": discovery_scope or None,
                "candidate_scan_limit": discovery_scope.get("candidate_scan_limit"),
                "query_mode": discovery_scope.get("query_mode"),
                "mission_id": mission_id,
                "plan_version": delegation.get("plan_version"),
                "milestone_id": delegation.get("milestone_id"),
                "decision_id": delegation.get("decision_id"),
                "materialization_id": delegation.get("materialization_id"),
                "execution_request_id": delegation.get("execution_request_id"),
                "delegation_id": delegation.get("delegation_id"),
                "worker_claim_id": delegation.get("worker_claim_id"),
                "target_count": delegation.get("target_count", 1),
                "verification_mode": delegation.get("verification_mode"),
                "simulation_mode": delegation.get("simulation_mode", False),
                "require_live_evidence": read_only_live_discovery,
                "require_entity_qualification": qualified_lead_canary,
            },
            shared_state={"mission_id": mission_id, "delegation": delegation},
            granted_permissions=granted_permissions,
            runtime_metadata=runtime_metadata,
        )

    def _collect_evidence_records(self, result: AgentResult) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        def walk(value: Any):
            if hasattr(value, "model_dump"):
                value = value.model_dump(mode="json")
            if isinstance(value, dict):
                if (
                    (value.get("artifact_id") and value.get("artifact_type"))
                    or (
                        "evidence_source" in value
                        and ("source_metadata" in value or "provider_used" in value)
                    )
                ):
                    records.append(value)
                for nested in value.values():
                    walk(nested)
            elif isinstance(value, (list, tuple)):
                for nested in value:
                    walk(nested)

        walk(result.artifacts)
        walk(result.tool_calls)
        return records

    @staticmethod
    def _safe_evidence_payload(candidate: Dict[str, Any]) -> Dict[str, Any]:
        results = candidate.get("results") or []
        first_result = results[0] if results and isinstance(results[0], dict) else {}
        entity_qualification = dict(
            candidate.get("entity_qualification")
            or first_result.get("entity_qualification")
            or {}
        )
        evidence_source = normalize_evidence_source(
            candidate.get("evidence_source")
            or candidate.get("mode")
            or (candidate.get("provenance") or {}).get("evidence_source")
        )
        source_metadata = {
            key: value
            for key, value in dict(candidate.get("source_metadata") or {}).items()
            if key in {
                "provider", "endpoint", "retrieval_type", "request_succeeded",
                "request_count", "requested_at", "result_count", "query", "max_results",
                "raw_result_count", "normalized_result_count", "usable_result_count",
                "qualified_artifact_target", "candidate_scan_limit", "query_mode", "candidate_count_examined",
                "qualified_candidate_index", "candidate_diagnostics",
            }
        }
        return {
            "evidence_source": evidence_source.value,
            "simulation_evidence": bool(
                candidate.get("simulation_evidence", is_simulation_evidence(evidence_source))
            ),
            "source_provider": (
                candidate.get("source_provider")
                or candidate.get("provider")
                or candidate.get("provider_used")
                or first_result.get("source_provider")
                or source_metadata.get("provider")
            ),
            "source_metadata": source_metadata,
            "source_url": (
                candidate.get("source_url")
                or candidate.get("source_reference")
                or first_result.get("source_url")
                or first_result.get("website")
            ),
            "source_type": candidate.get("source_type") or first_result.get("source_type"),
            "business_name": candidate.get("business_name") or first_result.get("business_name") or first_result.get("name"),
            "business_category": candidate.get("business_category") or first_result.get("business_category") or first_result.get("category"),
            "business_location": candidate.get("business_location") or first_result.get("business_location") or first_result.get("city"),
            "business_domain": candidate.get("business_domain") or first_result.get("business_domain"),
            "entity_qualification": entity_qualification,
            "qualification_reasons": list(
                candidate.get("qualification_reasons")
                or first_result.get("qualification_reasons")
                or entity_qualification.get("qualification_reasons")
                or []
            ),
        }

    def _extract_failure_evidence(self, result: AgentResult) -> Optional[Dict[str, Any]]:
        records = self._collect_evidence_records(result)
        candidate = next(
            (item for item in records if item.get("artifact_type") == "LeadArtifact"),
            None,
        ) or next(
            (item for item in records if "evidence_source" in item),
            None,
        )
        return self._safe_evidence_payload(candidate) if candidate else None

    def _extract_artifact_evidence(self, result: AgentResult, expected_type: Optional[str]) -> Optional[Dict[str, Any]]:
        records = self._collect_evidence_records(result)
        candidate = next((item for item in records if item.get("artifact_type") == expected_type), None)
        if not candidate or candidate.get("verified") is False:
            return None
        evidence = self._safe_evidence_payload(candidate)
        evidence.update({
            "artifact_id": candidate["artifact_id"],
            "artifact_type": candidate["artifact_type"],
            "verified": candidate.get("verified", True) is True,
            "verification_method": candidate.get("verification_method", "registered_specialist_result"),
            "provenance": dict(candidate.get("provenance") or {}),
            "source_reference": candidate.get("source_reference"),
        })
        return evidence

    async def _dispatch_to_director(self, mission: PersistentMission, delegation: Dict[str, Any]) -> bool:
        """Compatibility shim for callers of the pre-stabilization private method."""
        return await self._dispatch_to_specialist(mission.mission_id, delegation)

autonomous_worker = AutonomousMissionWorker()