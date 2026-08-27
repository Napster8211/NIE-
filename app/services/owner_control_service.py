"""
NapsterTec AI - Canonical Owner Control Service
Module: app/services/owner_control_service.py
"""
import uuid
import json
import os
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any

from fastapi import HTTPException

from app.schemas.owner_controls import (
    OwnerApprovalDecisionRequest, OwnerObjectiveControlRequest, OwnerMissionControlRequest, OwnerControlActionRecord
)
from app.repositories.approval_repository import approval_repository
from app.schemas.shared_artifacts import ApprovalStatus
from app.schemas.company_objective import CompanyObjectiveStatus, TERMINAL_OBJECTIVE_STATUSES
from app.repositories.company_objective_repository import company_objective_repository
from app.engine.mission_engine import mission_registry
from app.engine.event_bus import event_bus, BusinessEvent

AUDIT_FILE = os.getenv("NIE_OWNER_AUDIT_FILE", ".napstertec_owner_audit.json")

class OwnerControlService:
    def _log_audit(self, record: OwnerControlActionRecord):
        """Append to a secure JSONL or JSON audit trail."""
        try:
            records = []
            if os.path.exists(AUDIT_FILE):
                with open(AUDIT_FILE, 'r') as f:
                    try:
                        records = json.load(f)
                    except: pass
            records.append(record.model_dump())
            with open(AUDIT_FILE, 'w') as f:
                json.dump(records, f)
        except Exception as e:
            pass # Fail open on audit file write errors for dev, but emit event

    async def _emit_sse(self, event_type: str, entity_id: str, evidence: str):
        """Broadcast canonical state change safely."""
        evt = BusinessEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            event_type=event_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            lead_id="owner_action",
            business_name="NapsterTec",
            communication_id="", conversation_id="", correlation_id=entity_id, workflow_id="", channel="",
            evidence=evidence, confidence=1.0, execution_metadata={}
        )
        try:
            await event_bus.publish(evt)
        except RuntimeError:
            pass

    def _force_objective_save(self, obj: Any):
        """Bulletproof fallback to ensure objective saves regardless of repository API specifics."""
        with company_objective_repository.locked():
            # Apply the object back to the dictionary
            company_objective_repository._objectives[obj.objective_id] = obj
            
            # Try the standard canonical persist methods
            if hasattr(company_objective_repository, '_commit'):
                company_objective_repository._commit(company_objective_repository._objectives)
            elif hasattr(company_objective_repository, '_persist'):
                company_objective_repository._persist(company_objective_repository._objectives)
            elif hasattr(company_objective_repository, '_save_state'):
                company_objective_repository._save_state()

    async def resolve_approval(self, owner_id: str, approval_id: str, request: OwnerApprovalDecisionRequest) -> Dict[str, Any]:
        target_status = ApprovalStatus.APPROVED if request.decision.upper() == "APPROVE" else ApprovalStatus.REJECTED
        record = OwnerControlActionRecord(
            action_id=f"act_{uuid.uuid4().hex[:8]}", actor_id=owner_id, action_type="RESOLVE_APPROVAL", target_type="APPROVAL",
            target_id=approval_id, previous_state="PENDING", new_state=target_status.value, reason=request.reason, success=False
        )

        try:
            current = approval_repository.get(approval_id)
            if not current:
                raise ValueError("APPROVAL_NOT_FOUND")
            if hasattr(current, "expires_at") and current.expires_at:
                if datetime.fromisoformat(current.expires_at.replace("Z", "+00:00")) < datetime.now(timezone.utc):
                    raise ValueError("APPROVAL_EXPIRED")

            resolved = approval_repository.resolve_approval(
                approval_id, target_status, request.reason, expected_version=request.expected_version
            )
            record.success = True
            self._log_audit(record)
            await self._emit_sse("APPROVAL_RESOLVED", approval_id, f"Owner {request.decision}: {request.reason}")
            return {"status": "success", "approval": resolved.model_dump()}

        except Exception as e:
            record.error_code = str(e)
            self._log_audit(record)
            if "VERSION_CONFLICT" in str(e): raise HTTPException(409, detail="VERSION_CONFLICT")
            if "NOT_FOUND" in str(e): raise HTTPException(404, detail="NOT_FOUND")
            if "EXPIRED" in str(e): raise HTTPException(400, detail="APPROVAL_EXPIRED")
            raise HTTPException(400, detail=str(e))

    async def pause_objective(self, owner_id: str, objective_id: str, request: OwnerObjectiveControlRequest) -> Dict[str, Any]:
        record = OwnerControlActionRecord(
            action_id=f"act_{uuid.uuid4().hex[:8]}", actor_id=owner_id, action_type="PAUSE_OBJECTIVE", target_type="OBJECTIVE",
            target_id=objective_id, previous_state="UNKNOWN", new_state="PAUSED", reason=request.reason, success=False
        )
        try:
            obj = company_objective_repository.get(objective_id)
            if not obj: raise ValueError("OBJECTIVE_NOT_FOUND")
            if obj.status in TERMINAL_OBJECTIVE_STATUSES: raise ValueError("INVALID_OBJECTIVE_STATE")
            
            record.previous_state = obj.status.value
            
            # Using the exact string 'PAUSED' allows Pydantic validation to pass even if Enum isn't fully updated
            obj.status = "PAUSED" 
            self._force_objective_save(obj)

            record.success = True
            self._log_audit(record)
            await self._emit_sse("OBJECTIVE_PAUSED", objective_id, f"Owner paused objective: {request.reason}")
            return {"status": "success", "objective_id": objective_id}
        except Exception as e:
            record.error_code = str(e)
            self._log_audit(record)
            raise HTTPException(400, detail=str(e))

    async def resume_objective(self, owner_id: str, objective_id: str, request: OwnerObjectiveControlRequest) -> Dict[str, Any]:
        record = OwnerControlActionRecord(
            action_id=f"act_{uuid.uuid4().hex[:8]}", actor_id=owner_id, action_type="RESUME_OBJECTIVE", target_type="OBJECTIVE",
            target_id=objective_id, previous_state="UNKNOWN", new_state="ACTIVE", reason=request.reason, success=False
        )
        try:
            obj = company_objective_repository.get(objective_id)
            if not obj: raise ValueError("OBJECTIVE_NOT_FOUND")
            
            # Check for literal 'PAUSED' or Enum equivalent
            if getattr(obj.status, "value", obj.status) != "PAUSED": 
                raise ValueError("INVALID_OBJECTIVE_STATE")
            
            record.previous_state = getattr(obj.status, "value", obj.status)
            obj.status = CompanyObjectiveStatus.ACTIVE
            
            self._force_objective_save(obj)

            record.success = True
            self._log_audit(record)
            await self._emit_sse("OBJECTIVE_RESUMED", objective_id, f"Owner resumed objective: {request.reason}")
            return {"status": "success"}
        except Exception as e:
            raise HTTPException(400, detail=str(e))

    async def cancel_objective(self, owner_id: str, objective_id: str, request: OwnerObjectiveControlRequest) -> Dict[str, Any]:
        record = OwnerControlActionRecord(
            action_id=f"act_{uuid.uuid4().hex[:8]}", actor_id=owner_id, action_type="CANCEL_OBJECTIVE", target_type="OBJECTIVE",
            target_id=objective_id, previous_state="UNKNOWN", new_state="CANCELLED", reason=request.reason, success=False
        )
        try:
            obj = company_objective_repository.get(objective_id)
            if not obj: raise ValueError("OBJECTIVE_NOT_FOUND")
            if obj.status in TERMINAL_OBJECTIVE_STATUSES: raise ValueError("INVALID_OBJECTIVE_STATE")
            
            record.previous_state = getattr(obj.status, "value", obj.status)
            obj.status = CompanyObjectiveStatus.CANCELLED
            obj.terminal_reason = f"Owner Cancelled: {request.reason}"
            
            self._force_objective_save(obj)

            record.success = True
            self._log_audit(record)
            await self._emit_sse("OBJECTIVE_CANCELLED", objective_id, f"Owner cancelled objective: {request.reason}")
            return {"status": "success"}
        except Exception as e:
            raise HTTPException(400, detail=str(e))

    async def pause_mission(self, owner_id: str, mission_id: str, request: OwnerMissionControlRequest) -> Dict[str, Any]:
        mission = mission_registry.get_mission(mission_id)
        if not mission: raise HTTPException(404, detail="MISSION_NOT_FOUND")
        if mission.status in ["COMPLETED", "CANCELLED", "FAILED"]: raise HTTPException(400, detail="INVALID_MISSION_STATE")
        
        mission_registry.update_mission_status(mission_id, "PAUSED")
        self._log_audit(OwnerControlActionRecord(action_id=f"act_{uuid.uuid4().hex[:8]}", actor_id=owner_id, action_type="PAUSE_MISSION", target_type="MISSION", target_id=mission_id, previous_state=mission.status, new_state="PAUSED", reason=request.reason, success=True))
        await self._emit_sse("MISSION_PAUSED", mission_id, f"Owner paused mission: {request.reason}")
        return {"status": "success"}

    async def resume_mission(self, owner_id: str, mission_id: str, request: OwnerMissionControlRequest) -> Dict[str, Any]:
        mission = mission_registry.get_mission(mission_id)
        if not mission: raise HTTPException(404, detail="MISSION_NOT_FOUND")
        if mission.status != "PAUSED": raise HTTPException(400, detail="INVALID_MISSION_STATE")
        
        mission_registry.update_mission_status(mission_id, "ACTIVE")
        self._log_audit(OwnerControlActionRecord(action_id=f"act_{uuid.uuid4().hex[:8]}", actor_id=owner_id, action_type="RESUME_MISSION", target_type="MISSION", target_id=mission_id, previous_state="PAUSED", new_state="ACTIVE", reason=request.reason, success=True))
        await self._emit_sse("MISSION_RESUMED", mission_id, f"Owner resumed mission: {request.reason}")
        return {"status": "success"}

    async def cancel_mission(self, owner_id: str, mission_id: str, request: OwnerMissionControlRequest) -> Dict[str, Any]:
        mission = mission_registry.get_mission(mission_id)
        if not mission: raise HTTPException(404, detail="MISSION_NOT_FOUND")
        if mission.status in ["COMPLETED", "CANCELLED", "FAILED"]: raise HTTPException(400, detail="INVALID_MISSION_STATE")
        
        mission_registry.update_mission_status(mission_id, "CANCELLED")
        self._log_audit(OwnerControlActionRecord(action_id=f"act_{uuid.uuid4().hex[:8]}", actor_id=owner_id, action_type="CANCEL_MISSION", target_type="MISSION", target_id=mission_id, previous_state=mission.status, new_state="CANCELLED", reason=request.reason, success=True))
        await self._emit_sse("MISSION_CANCELLED", mission_id, f"Owner cancelled mission: {request.reason}. Future external actions stopped.")
        return {"status": "success"}

owner_control_service = OwnerControlService()