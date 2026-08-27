import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from app.schemas.company_objective import CompanyObjective, CompanyObjectiveStatus
from app.schemas.strategic_plan import (
    StrategicPlan, StrategicPlanStatus, StrategicWorkstream, DepartmentAssignment
)
from app.repositories.strategic_plan_repository import strategic_plan_repository

class DirectorStrategyService:
    def __init__(self, agent_registry=None):
        self.agent_registry = agent_registry # Dependency injection for testing

    def _get_available_agents(self) -> List[Any]:
        if self.agent_registry:
            return self.agent_registry.list_all_metadata()
        
        # Safe fallback if registry not injected or unavailable during test phase
        try:
            from app.agent.registry import agent_registry
            return agent_registry.list_all_metadata()
        except ImportError:
            # Fallback to simulated minimal departments for pure deterministic logic tests
            class MockAgentMeta:
                def __init__(self, name, caps):
                    self.name = name
                    self.capabilities = caps
            return [
                MockAgentMeta("engineering_intelligence", ["coding", "deployment", "infrastructure"]),
                MockAgentMeta("marketing_intelligence", ["campaign", "social_media", "content"]),
                MockAgentMeta("sales_intelligence", ["lead_generation", "crm", "outreach"]),
                MockAgentMeta("finance_intelligence", ["budget_management", "cash_flow_analysis", "roi_analysis"]),
                MockAgentMeta("research_intelligence", ["market_research", "data_analysis"])
            ]

    def _interpret_objective(self, objective: CompanyObjective) -> Dict[str, Any]:
        obj_text = objective.objective.lower()
        title_text = objective.title.lower()
        full_context = f"{obj_text} {title_text}"
        
        # Very basic deterministic extraction based on keyword signatures (since we cannot use LLMs here).
        # In a real deployed LLM environment, this is where the Director LLM synthesizes.
        workstreams = []
        caps_required = set()
        
        if "revenue" in full_context or "sales" in full_context or "contract" in full_context or "sell" in full_context:
            caps_required.update(["lead_generation", "outreach"])
            workstreams.append(("Prospect Acquisition", "Identify and engage prospects to generate revenue.", ["lead_generation", "outreach"]))
            
        if "web" in full_context or "app" in full_context or "platform" in full_context or "code" in full_context or "development" in full_context:
            caps_required.update(["coding", "deployment"])
            workstreams.append(("Technical Development", "Build and deploy the requested technical asset.", ["coding", "deployment"]))
            
        if "marketing" in full_context or "campaign" in full_context or "brand" in full_context or "audience" in full_context:
            caps_required.update(["campaign", "social_media"])
            workstreams.append(("Marketing Campaign", "Create and execute targeted marketing initiatives.", ["campaign", "social_media"]))
            
        if "budget" in full_context or "cost" in full_context or "finance" in full_context or "roi" in full_context:
            caps_required.update(["budget_management"])
            workstreams.append(("Financial Oversight", "Ensure project remains within budget constraints.", ["budget_management"]))
            
        if "research" in full_context or "market" in full_context or "competitor" in full_context:
            caps_required.update(["market_research"])
            workstreams.append(("Market Intelligence", "Gather data on market conditions and competitors.", ["market_research"]))
            
        if not workstreams:
            workstreams.append(("General Discovery", "Determine actionable bounds for ambiguous objective.", ["research"]))
            caps_required.add("research")
            
        return {
            "workstreams": workstreams,
            "required_capabilities": list(caps_required)
        }

    def _select_departments(self, required_capabilities: List[str]) -> tuple[List[DepartmentAssignment], List[str]]:
        available_agents = self._get_available_agents()
        assignments = []
        uncovered_caps = set(required_capabilities)
        
        # Simple greedy set cover
        while uncovered_caps:
            best_agent = None
            best_coverage = set()
            
            for agent in available_agents:
                # Handle varying metadata structures between real and mock
                agent_caps = set()
                raw_caps = getattr(agent, "capabilities", [])
                for cap in raw_caps:
                    agent_caps.add(str(getattr(cap, "value", cap)).lower())
                
                coverage = uncovered_caps.intersection(agent_caps)
                if len(coverage) > len(best_coverage):
                    best_coverage = coverage
                    best_agent = agent
                    
            if not best_agent:
                break # Cannot cover remaining caps
                
            assignments.append(DepartmentAssignment(
                agent_id=best_agent.name,
                role="Strategic Execution",
                matched_capabilities=list(best_coverage),
                required_capabilities=list(best_coverage),
                selection_reason=f"Selected to provide {', '.join(best_coverage)}"
            ))
            uncovered_caps -= best_coverage
            
        return assignments, list(uncovered_caps)

    def develop_strategy(self, objective: CompanyObjective) -> StrategicPlan:
        # Check staleness
        existing_plan = strategic_plan_repository.get_latest_for_objective(objective.objective_id)
        if existing_plan and existing_plan.objective_version == objective.version and existing_plan.status in [StrategicPlanStatus.READY, StrategicPlanStatus.NEEDS_CLARIFICATION]:
            return existing_plan # Return existing idempotent plan
            
        interpretation = self._interpret_objective(objective)
        
        # Map Workstreams
        workstreams = []
        for i, (title, purpose, req_caps) in enumerate(interpretation["workstreams"]):
            workstreams.append(StrategicWorkstream(
                workstream_id=f"ws_{uuid.uuid4().hex[:8]}",
                title=title,
                purpose=purpose,
                desired_outcome="Successful execution of workstream goals.",
                required_capabilities=req_caps,
                priority="HIGH" if i == 0 else "NORMAL"
            ))
            
        # Select Departments
        assignments, uncovered_caps = self._select_departments(interpretation["required_capabilities"])
        
        # Bind assignments to workstreams
        for ws in workstreams:
            for assignment in assignments:
                if any(cap in assignment.matched_capabilities for cap in ws.required_capabilities):
                    if ws.workstream_id not in assignment.assigned_workstreams:
                        assignment.assigned_workstreams.append(ws.workstream_id)

        # Determine Readiness
        readiness = "READY"
        status = StrategicPlanStatus.READY
        questions = []
        
        if uncovered_caps:
            readiness = "BLOCKED"
            status = StrategicPlanStatus.BLOCKED
            questions.append(f"CAPABILITY_GAP: No registered department provides: {', '.join(uncovered_caps)}.")
            
        # Ambiguity check
        if len(workstreams) == 1 and workstreams[0].title == "General Discovery":
            readiness = "NEEDS_CLARIFICATION"
            status = StrategicPlanStatus.NEEDS_CLARIFICATION
            questions.append("The objective lacks specific domain constraints (e.g., development, marketing, sales). Please clarify the intended outcome.")
            
        plan = StrategicPlan(
            strategic_plan_id=f"plan_{uuid.uuid4().hex[:12]}",
            objective_id=objective.objective_id,
            objective_version=objective.version,
            status=status,
            business_outcome=objective.title,
            executive_summary=f"Director strategy for {objective.title}",
            success_criteria=objective.success_criteria.model_dump(mode="json"),
            workstreams=workstreams,
            department_assignments=assignments,
            clarification_questions=questions,
            execution_readiness=readiness,
            strategy_confidence=0.9 if readiness == "READY" else 0.4
        )
        
        return strategic_plan_repository.create(plan)