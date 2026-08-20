import logging
import hashlib
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel

from app.agent.agent_models import AgentContext, AgentPermission
from app.repositories.approval_repository import approval_repository, ApprovalStatus

logger = logging.getLogger(__name__)

class AuthorizationResult(BaseModel):
    status: str
    error: Optional[str] = None
    approval_consumed: bool = False
    
class AuthorizationGate:
    @staticmethod
    def _build_action_fingerprint(operation_type: str, parameters: Dict[str, Any]) -> str:
        # Sort keys to ensure deterministic exact-action fingerprinting
        payload = json.dumps(parameters, sort_keys=True)
        return hashlib.sha256(f"{operation_type}:{payload}".encode('utf-8')).hexdigest()

    @staticmethod
    def evaluate_execution(
        agent_context: AgentContext,
        tool_name: str,
        required_permissions: List[str],
        approval_required: bool,
        operation_type: str,
        parameters: Dict[str, Any]
    ) -> AuthorizationResult:
        
        # 1. VERIFY RUNTIME AUTHORITY (KEY 1)
        granted = set(agent_context.granted_permissions)
        granted_strs = {str(getattr(p, 'value', p)).lower() for p in granted}
        
        missing_strs = []
        for p in required_permissions:
            p_str = str(getattr(p, 'value', p)).lower()
            if p_str not in granted_strs:
                missing_strs.append(p_str)
                
        if missing_strs:
            return AuthorizationResult(
                status="AUTHORITY_MISSING",
                error=f"Missing required operational authority: {missing_strs}"
            )
            
        # Unprotected operation passes safely here
        if not approval_required:
            return AuthorizationResult(status="AUTHORIZED")
            
        # 2. VERIFY EXACT HUMAN APPROVAL (KEY 2)
        planner_out = agent_context.planner_output or {}
        mission_id = planner_out.get("mission_id")
        mat_id = planner_out.get("materialization_id")
        
        if not mission_id or not mat_id:
            return AuthorizationResult(
                status="LINEAGE_MISMATCH",
                error="Cannot correlate protected operation without mission/materialization lineage."
            )
            
        approvals = approval_repository.list_by_mission(mission_id)
        target_approval = next((a for a in approvals if a.materialization_id == mat_id), None)
        
        if not target_approval:
            return AuthorizationResult(status="APPROVAL_MISSING", error=f"No ApprovalRequest found for materialization {mat_id}")
            
        if target_approval.status == ApprovalStatus.PENDING:
            return AuthorizationResult(status="APPROVAL_REQUIRED", error="Human approval is pending.")
        if target_approval.status == ApprovalStatus.REJECTED:
            return AuthorizationResult(status="APPROVAL_REJECTED", error="Human rejected this operation.")
        if target_approval.status == ApprovalStatus.EXPIRED:
            return AuthorizationResult(status="APPROVAL_EXPIRED", error="Approval has expired.")
        if target_approval.status == ApprovalStatus.CANCELLED:
            return AuthorizationResult(status="APPROVAL_REVOKED", error="Approval was revoked.")
        if target_approval.status == ApprovalStatus.CONSUMED:
            return AuthorizationResult(status="APPROVAL_CONSUMED", error="Replay prevented: Approval already consumed.")
            
        if target_approval.status != ApprovalStatus.APPROVED:
            return AuthorizationResult(status="DENIED", error=f"Unknown approval state: {target_approval.status}")

        # 3. VERIFY ACTION FINGERPRINT (Exact-Action Scoping)
        expected_fingerprint = AuthorizationGate._build_action_fingerprint(operation_type, parameters)
        if target_approval.action_fingerprint and target_approval.action_fingerprint != expected_fingerprint:
            return AuthorizationResult(status="FINGERPRINT_MISMATCH", error="Action fingerprint does not match approved scope.")
            
        return AuthorizationResult(status="AUTHORIZED")
        
    @staticmethod
    def consume_approval(agent_context: AgentContext):
        planner_out = agent_context.planner_output or {}
        mission_id = planner_out.get("mission_id")
        mat_id = planner_out.get("materialization_id")
        if not mission_id or not mat_id:
            return
            
        approvals = approval_repository.list_by_mission(mission_id)
        target_approval = next((a for a in approvals if a.materialization_id == mat_id), None)
        
        if target_approval and target_approval.status == ApprovalStatus.APPROVED:
            # Safely transition state preventing replay
            approval_repository.resolve_approval(
                target_approval.approval_id, 
                ApprovalStatus.CONSUMED, 
                "Operation executed successfully."
            )