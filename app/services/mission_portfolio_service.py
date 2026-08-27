import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from app.schemas.company_objective import CompanyObjective
from app.schemas.strategic_plan import StrategicPlan, StrategicPlanStatus, DepartmentAssignment
from app.schemas.mission_portfolio import (
    MissionPortfolio, MissionPortfolioStatus, MissionDefinition
)
from app.repositories.mission_portfolio_repository import mission_portfolio_repository

class MissionPortfolioService:

    def _determine_artifact(self, req_caps: List[str], dept: str) -> str:
        if "coding" in req_caps or "implementation" in req_caps: return "ImplementationArtifact"
        if "campaign" in req_caps or "marketing" in req_caps: return "CampaignArtifact"
        if "social" in req_caps: return "SocialArtifact"
        if "lead_generation" in req_caps: return "LeadArtifact"
        if "outreach" in req_caps: return "CommunicationArtifact"
        if "budget" in req_caps or "finance" in req_caps: return "FinanceArtifact"
        if "deployment" in req_caps: return "DeploymentArtifact"
        if "research" in req_caps: return "OpportunityArtifact"
        if "sales" in req_caps or "crm" in req_caps: return "SalesArtifact"
        return "UnknownArtifact"

    def _determine_mission_type(self, artifact: str) -> str:
        if artifact == "LeadArtifact" or artifact == "SalesArtifact":
            return "CLIENT_ACQUISITION"
        return "ARTIFACT_PRODUCTION"

    def _build_dag_and_groups(self, definitions: List[MissionDefinition]) -> tuple[Dict[str, List[str]], List[List[str]]]:
        deps = {d.mission_definition_id: d.dependencies for d in definitions}
        visited = set()
        path = set()
        
        def has_cycle(node):
            visited.add(node)
            path.add(node)
            for neighbor in deps.get(node, []):
                if neighbor not in deps:
                    raise ValueError(f"UNKNOWN_DEPENDENCY: {neighbor}")
                if neighbor == node:
                    raise ValueError(f"SELF_DEPENDENCY_DETECTED: {node}")
                if neighbor in path:
                    raise ValueError(f"CIRCULAR_DEPENDENCY_DETECTED: {node} -> {neighbor}")
                if neighbor not in visited:
                    if has_cycle(neighbor):
                        return True
            path.remove(node)
            return False

        for node in deps:
            if node not in visited:
                if has_cycle(node):
                    pass 

        levels = {}
        def get_level(node):
            if node in levels:
                return levels[node]
            if not deps.get(node):
                levels[node] = 0
                return 0
            max_dep_level = max(get_level(dep) for dep in deps[node])
            levels[node] = max_dep_level + 1
            return levels[node]
            
        for node in deps:
            get_level(node)
            
        max_level = max(levels.values()) if levels else -1
        if max_level > 10:
            raise ValueError("MAX_DEPENDENCY_DEPTH_EXCEEDED")
            
        groups = [[] for _ in range(max_level + 1)]
        for node, level in levels.items():
            groups[level].append(node)
            
        return deps, groups

    def materialize_portfolio(self, plan: StrategicPlan, objective: CompanyObjective) -> MissionPortfolio:
        if plan.status != StrategicPlanStatus.READY:
            raise ValueError("STRATEGIC_PLAN_NOT_READY")
        if plan.objective_id != objective.objective_id or plan.objective_version != objective.version:
            raise ValueError("STRATEGIC_PLAN_OBJECTIVE_MISMATCH")

        existing = mission_portfolio_repository.get_latest_for_objective(objective.objective_id)
        if existing and existing.strategic_plan_id == plan.strategic_plan_id and existing.status in [MissionPortfolioStatus.READY, MissionPortfolioStatus.ACTIVE]:
            return existing

        mission_defs = []
        dept_counts = {}
        seen_signatures = set()
        
        for ws in plan.workstreams:
            assignments = [a for a in plan.department_assignments if ws.workstream_id in a.assigned_workstreams]
            if not assignments:
                continue
                
            for assignment in assignments:
                dept_id = assignment.agent_id
                dept_counts[dept_id] = dept_counts.get(dept_id, 0) + 1
                if dept_counts[dept_id] > 10: 
                    raise ValueError("MAX_MISSIONS_PER_DEPARTMENT_EXCEEDED")

                artifact = self._determine_artifact(assignment.matched_capabilities, dept_id)
                m_type = self._determine_mission_type(artifact)
                
                sig = f"{ws.workstream_id}:{dept_id}:{artifact}"
                if sig in seen_signatures:
                    continue 
                seen_signatures.add(sig)

                m_def = MissionDefinition(
                    mission_definition_id=f"mdef_{uuid.uuid4().hex[:8]}",
                    workstream_id=ws.workstream_id,
                    title=f"Execute {ws.title} via {dept_id}",
                    objective=ws.purpose,
                    department_id=dept_id,
                    mission_type=m_type,
                    priority=ws.priority,
                    success_criterion="verified_artifacts" if m_type == "ARTIFACT_PRODUCTION" else "verified_won_clients",
                    target_count=1,
                    expected_artifact=artifact,
                    dependencies=[], 
                    strategic_reason=assignment.selection_reason,
                    execution_ready=True
                )
                
                if m_def.success_criterion is None or m_def.success_criterion == "":
                    m_def.execution_ready = False
                    m_def.execution_blockers.append("MISSING_SUCCESS_CRITERIA")
                    
                mission_defs.append(m_def)

        active_linked = len(objective.linked_mission_ids)
        if active_linked + len(mission_defs) > objective.max_missions:
            raise ValueError("OBJECTIVE_MAX_MISSIONS_EXCEEDED")

        ws_to_def = {d.workstream_id: d.mission_definition_id for d in mission_defs}
        for ws in plan.workstreams:
            for dep_ws in ws.dependencies:
                if ws.workstream_id in ws_to_def and dep_ws in ws_to_def:
                    m_def = next(d for d in mission_defs if d.workstream_id == ws.workstream_id)
                    m_def.dependencies.append(ws_to_def[dep_ws])

        try:
            deps, groups = self._build_dag_and_groups(mission_defs)
        except ValueError as e:
            return mission_portfolio_repository.create(MissionPortfolio(
                portfolio_id=f"port_{uuid.uuid4().hex[:8]}",
                objective_id=objective.objective_id, objective_version=objective.version,
                strategic_plan_id=plan.strategic_plan_id, strategic_plan_version=plan.version,
                status=MissionPortfolioStatus.PARTIALLY_BLOCKED,
                mission_definitions=mission_defs,
                risk_state="AT_RISK", blocking_reasons=[str(e)]
            ))

        if len(mission_defs) > 20: 
            raise ValueError("MAX_MISSIONS_PER_PORTFOLIO_EXCEEDED")

        port = MissionPortfolio(
            portfolio_id=f"port_{uuid.uuid4().hex[:8]}",
            objective_id=objective.objective_id,
            objective_version=objective.version,
            strategic_plan_id=plan.strategic_plan_id,
            strategic_plan_version=plan.version,
            status=MissionPortfolioStatus.READY,
            mission_definitions=mission_defs,
            dependencies=deps,
            execution_groups=groups,
            max_parallel_missions=4,
            max_total_missions=20
        )
        return mission_portfolio_repository.create(port)