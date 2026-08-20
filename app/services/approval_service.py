"""
NapsterTec AI - Approval Decision Service
Module: app/services/approval_service.py

Brokers the exact lifecycle of human approval decisions, ensuring AI Self-Approval 
is prevented, and safely releases/blocks exact Mission Control Plane execution flows.
"""
import uuid
import logging
from typing import Dict, Any

from app.schemas.shared_artifacts import DirectorAgentContext, DirectorArtifact, ApprovalStatus, MutationLedger
from app.repositories.approval_repository import approval_repository, ApprovalInvariantError, ApprovalPersistenceError
from app.engine.mission_engine import MissionEngine

logger = logging.getLogger(__name__)

class ApprovalDecisionService:
    async def execute_decision(self, context: DirectorAgentContext, session_id: str) -> DirectorArtifact:
        command_class = context.command_class
        metrics = context.aggregated_metrics
        approval_id = metrics.get("approval_id")
        reason = metrics.get("approval_reason", "Human decision recorded.")
        
        # 1. AI SELF-APPROVAL PREVENTION
        # Ensure that mutation commands (APPROVE/REJECT/REVOKE) originate from a trusted human source, 
        # not the AutonomousAgentLoop or a general task fallback.
        if command_class in ["APPROVAL_APPROVE", "APPROVAL_REJECT", "APPROVAL_REVOKE"]:
            if not session_id or session_id in ["system_director", "agent_session", "system"]:
                raise PermissionError("APPROVAL_DENIED: Trusted human identity required. AI self-approval is forbidden.")
            if not approval_id:
                raise ValueError("APPROVAL_ID_REQUIRED: Exact approval ID is required for mutation.")
        
        ledger = MutationLedger()
        summary = ""
        read_only = True
        
        try:
            # 2. READ-ONLY INSPECTION PATH
            if command_class == "APPROVAL_INSPECT":
                if approval_id:
                    approval = approval_repository.get(approval_id)
                    if not approval:
                        raise ValueError(f"APPROVAL_NOT_FOUND: {approval_id}")
                    metrics["approvals"] = [approval.model_dump(mode="json")]
                    summary = f"Approval {approval_id} inspected successfully."
                else:
                    pending = approval_repository.list_pending()
                    metrics["approvals"] = [p.model_dump(mode="json") for p in pending]
                    summary = f"Listed {len(pending)} pending approvals."
                    
            # 3. APPROVAL PATH
            elif command_class == "APPROVAL_APPROVE":
                approval = approval_repository.get(approval_id)
                if not approval:
                    raise ValueError(f"APPROVAL_NOT_FOUND: {approval_id}")
                    
                # Exact Work Correlation: Resolve atomically, failing closed if already resolved
                resolved = approval_repository.resolve_approval(approval_id, ApprovalStatus.APPROVED, reason)
                read_only = False
                ledger.state_changing_events += 1
                
                # Safe Work Release
                engine = MissionEngine()
                released = await engine.approve_materialization(resolved.mission_id, resolved.materialization_id)
                if not released:
                    raise RuntimeError("APPROVAL_WORK_RELEASE_FAILED: Mismatched mission correlation. Work remains blocked.")
                
                summary = f"Approval {approval_id} APPROVED. Exact work safely released for downstream execution authorization."
                metrics["approvals"] = [resolved.model_dump(mode="json")]
                
            # 4. REJECT PATH
            elif command_class == "APPROVAL_REJECT":
                approval = approval_repository.get(approval_id)
                if not approval:
                    raise ValueError(f"APPROVAL_NOT_FOUND: {approval_id}")
                    
                resolved = approval_repository.resolve_approval(approval_id, ApprovalStatus.REJECTED, reason)
                read_only = False
                ledger.state_changing_events += 1
                
                # Block Work & Escalate
                engine = MissionEngine()
                blocked = await engine.reject_materialization(resolved.mission_id, resolved.materialization_id, reason)
                if not blocked:
                    raise RuntimeError("APPROVAL_WORK_BLOCK_FAILED: Mismatched mission correlation.")
                    
                summary = f"Approval {approval_id} REJECTED. Work safely blocked and mission escalated."
                metrics["approvals"] = [resolved.model_dump(mode="json")]
                
            # 5. REVOKE PATH
            elif command_class == "APPROVAL_REVOKE":
                approval = approval_repository.get(approval_id)
                if not approval:
                    raise ValueError(f"APPROVAL_NOT_FOUND: {approval_id}")
                
                resolved = approval_repository.revoke_approval(approval_id, reason)
                read_only = False
                ledger.state_changing_events += 1
                
                # Revoke Work (Fail closed if already consumed/executed)
                engine = MissionEngine()
                revoked = await engine.revoke_materialization(resolved.mission_id, resolved.materialization_id, reason)
                if not revoked:
                    raise RuntimeError("APPROVAL_WORK_REVOKE_FAILED: Could not revoke. Protected action may have already executed.")
                    
                summary = f"Approval {approval_id} REVOKED. Work safely returned to a blocked state."
                metrics["approvals"] = [resolved.model_dump(mode="json")]
        
        except (ApprovalInvariantError, ApprovalPersistenceError) as e:
            # Safely wrap persistence and invariant failures as values the execution engine expects
            raise ValueError(f"APPROVAL_ERROR: {str(e)}")
            
        return DirectorArtifact(
            artifact_id=f"dir_{uuid.uuid4().hex[:8]}", 
            agent_run_id=session_id, 
            lead_id=context.company_id,
            operating_mode=context.operating_mode, 
            execution_context=context.execution_context, 
            mission_id=context.mission_id, 
            mission_action=context.mission_action,
            objective_id=context.objective_id, 
            objective_action=context.objective_action,
            read_only=read_only, 
            state_mutation_from_query="None" if read_only else "Executed", 
            mutation_ledger=ledger,
            company_health="Healthy", 
            executive_summary=summary,
            execution_metadata=context.aggregated_metrics
        )