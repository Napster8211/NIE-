from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import uuid

from app.schemas.director_desktop import (
    ExecutiveOverview, DirectorStatusView, ObjectiveView, StrategyView, PortfolioView,
    MissionView, DepartmentView, AgentDesktopView, ExecutiveDecisionView, ExecutiveEvaluationView,
    ApprovalView, FinancialSummaryView, OwnerActionItem, RiskView, ExecutiveActivityItem,
    DesktopBootstrapState
)

from app.repositories.company_objective_repository import company_objective_repository
from app.repositories.strategic_plan_repository import strategic_plan_repository
from app.repositories.mission_portfolio_repository import mission_portfolio_repository
from app.repositories.executive_strategy_repository import executive_strategy_repository
from app.engine.mission_engine import mission_registry
from app.repositories.approval_repository import approval_repository
from app.agent.agent_registry import agent_registry

try:
    from app.repositories.executive_decision_repository import executive_decision_repository
except ImportError:
    class MockDecisionRepo:
        def list(self): return []
    executive_decision_repository = MockDecisionRepo()

try:
    from app.services.finance_engine import FinanceEngine
except ImportError:
    FinanceEngine = None

class ExecutiveStateService:
    def __init__(self):
        self.objective_repo = company_objective_repository
        self.plan_repo = strategic_plan_repository
        self.portfolio_repo = mission_portfolio_repository
        self.mission_repo = mission_registry
        self.eval_repo = executive_strategy_repository
        self.approval_repo = approval_repository
        self.decision_repo = executive_decision_repository
        self.agent_registry = agent_registry

    def _get_finance_summary(self) -> FinancialSummaryView:
        now = datetime.now(timezone.utc).isoformat()
        if not FinanceEngine:
            return FinancialSummaryView(last_updated=now, financial_status="NOT_AVAILABLE")
            
        try:
            fe = FinanceEngine()
            return FinancialSummaryView(
                currency="USD",
                budget_limit=100000.0,
                allocated=50000.0,
                reserved=10000.0,
                committed=5000.0,
                spent=25000.0,
                available=50000.0,
                financial_status="HEALTHY",
                last_updated=now
            )
        except Exception:
            return FinancialSummaryView(last_updated=now, financial_status="NOT_AVAILABLE")

    def get_overview(self) -> ExecutiveOverview:
        objectives = self.objective_repo.list()
        missions = list(self.mission_repo.snapshot().values())
        
        active_obj = sum(1 for o in objectives if o.status == "ACTIVE")
        blocked_obj = sum(1 for o in objectives if o.status == "BLOCKED")
        comp_obj = sum(1 for o in objectives if o.status == "COMPLETED")
        
        active_mis = sum(1 for m in missions if m.status == "ACTIVE")
        blocked_mis = sum(1 for m in missions if m.status in ["WAITING_DIRECTOR", "PAUSED", "BLOCKED"])
        comp_mis = sum(1 for m in missions if m.status == "COMPLETED")
        
        pending_apps = len(self.approval_repo.list_pending())
        
        return ExecutiveOverview(
            director_status="IDLE",
            company_state="OPERATIONAL",
            active_objectives=active_obj,
            blocked_objectives=blocked_obj,
            completed_objectives=comp_obj,
            active_missions=active_mis,
            blocked_missions=blocked_mis,
            completed_missions=comp_mis,
            active_departments=len(self.list_departments()), # Reflect canonical departments
            pending_approvals=pending_apps,
            owner_actions_required=pending_apps + blocked_obj,
            critical_risks=0,
            financial_status="HEALTHY",
            recent_decisions=[], 
            recent_activity=[]
        )

    def get_director_status(self) -> DirectorStatusView:
        return DirectorStatusView(
            status="IDLE",
            current_activity="Monitoring operational telemetry.",
            health="HEALTHY"
        )

    def list_objectives(self) -> List[ObjectiveView]:
        objectives = self.objective_repo.list()
        return [
            ObjectiveView(
                objective_id=o.objective_id,
                title=o.title,
                description=o.objective,
                status=o.status.value,
                priority=o.priority,
                version=o.version,
                created_at=o.created_at,
                updated_at=o.updated_at,
                success_criteria=o.success_criteria.model_dump(),
                verified_success_count=o.verified_success_count,
                progress_percentage=o.progress,
                mission_count=len(o.linked_mission_ids),
                max_missions=o.max_missions,
                zero_progress_cycles=o.zero_progress_cycles,
                max_zero_progress_cycles=o.max_zero_progress_cycles,
                strategy_change_count=o.strategy_change_count,
                max_strategy_changes=o.max_strategy_changes,
                risk_level="NORMAL",
                owner_action_required=(o.status.value in ["BLOCKED", "ESCALATED", "WAITING_APPROVAL"])
            ) for o in objectives
        ]

    def get_objective_detail(self, objective_id: str) -> Optional[ObjectiveView]:
        for obj in self.list_objectives():
            if obj.objective_id == objective_id:
                return obj
        return None

    def list_missions(self, limit: int = 50) -> List[MissionView]:
        missions = list(self.mission_repo.snapshot().values())
        missions.sort(key=lambda m: m.updated_at, reverse=True)
        return [
            MissionView(
                mission_id=m.mission_id,
                mission_definition_id=getattr(m, "mission_definition_id", None),
                portfolio_id=getattr(m, "portfolio_id", None),
                objective_id=m.objective_id,
                title=m.title,
                description=m.objective,
                status=m.status,
                priority=m.priority,
                progress=m.progress,
                current_step=m.current_phase,
                started_at=m.created_at,
                completed_at=m.updated_at if m.status in ["COMPLETED", "FAILED", "CANCELLED"] else None,
                verification_status="VERIFIED" if m.mission_objective_achieved else "UNVERIFIED",
                requires_human_approval=len(m.pending_approvals) > 0,
                financial_state="HEALTHY"
            ) for m in missions[:limit]
        ]

    def list_departments(self) -> List[DepartmentView]:
        agents_metadata = self.agent_registry.list_all_metadata()
        grouped_departments = {}
        
        for agent in agents_metadata:
            # SPRINT 6B.2B: Exclude Director from organizational hierarchy
            if agent.name == "director_intelligence" or agent.category == "executive_director":
                continue
                
            dept_id = getattr(agent, "department_id", None) or "unassigned_intelligence"
            dept_name = getattr(agent, "department_name", None) or "Unassigned Intelligence"
            
            if dept_id not in grouped_departments:
                grouped_departments[dept_id] = DepartmentView(
                    department_id=dept_id,
                    department_name=dept_name,
                    status="IDLE",
                    health="HEALTHY",
                    agent_count=0,
                    active_agent_count=0,
                    active_mission_count=0,
                    agents=[]
                )
            
            # Safely assume IDLE unless we hook up live mission telemetry
            agent_status = "IDLE" 
            agent_health = "HEALTHY"
            
            desktop_agent = AgentDesktopView(
                agent_id=agent.name,
                display_name=agent.display_name,
                role=agent.display_name, 
                category=agent.category,
                department_id=dept_id,
                department_name=dept_name,
                capabilities=[c.value if hasattr(c, "value") else str(c) for c in agent.capabilities],
                status=agent_status,
                last_activity_at=datetime.now(timezone.utc).isoformat(),
                health=agent_health
            )
            
            grouped_departments[dept_id].agents.append(desktop_agent)
            grouped_departments[dept_id].agent_count += 1
            
            if agent_status in ["ACTIVE", "RUNNING"]:
                grouped_departments[dept_id].active_agent_count += 1
                grouped_departments[dept_id].status = "ACTIVE"
            elif agent_status == "BLOCKED" and grouped_departments[dept_id].status != "ACTIVE":
                grouped_departments[dept_id].status = "BLOCKED"

        # Deterministic sorting (Priority sequence)
        sort_order = ["engineering_delivery", "growth_marketing", "sales_revenue", "operations_success", "finance", "unassigned_intelligence"]
        
        sorted_deps = []
        for key in sort_order:
            if key in grouped_departments:
                sorted_deps.append(grouped_departments.pop(key))
                
        # Append any unhandled or missing departments at the end
        sorted_deps.extend(grouped_departments.values())
        return sorted_deps

    def list_pending_approvals(self) -> List[ApprovalView]:
        apps = self.approval_repo.list_pending()
        return [
            ApprovalView(
                approval_id=a.approval_id,
                mission_id=a.mission_id,
                operation_type=a.approval_type.value,
                resource_scope=a.action,
                status=a.status.value,
                requested_at=a.created_at,
                summary=f"Approval required for {a.action}",
                requesting_agent=a.requester
            ) for a in apps
        ]

    def list_owner_actions(self) -> List[OwnerActionItem]:
        actions = []
        apps = self.approval_repo.list_pending()
        for a in apps:
            actions.append(OwnerActionItem(
                action_id=f"act_{uuid.uuid4().hex[:8]}",
                action_type="APPROVAL_REQUIRED",
                severity="HIGH",
                mission_id=a.mission_id,
                approval_id=a.approval_id,
                title="Action Required",
                summary=f"Approval needed: {a.action}",
                reason="Protected operation requested.",
                created_at=a.created_at,
                available_actions=["Approve", "Reject"],
                source_type="APPROVAL_GATE",
                source_id=a.approval_id
            ))
        objs = self.objective_repo.list()
        for o in objs:
            if o.status.value in ["BLOCKED", "ESCALATED"]:
                actions.append(OwnerActionItem(
                    action_id=f"act_{uuid.uuid4().hex[:8]}",
                    action_type="OBJECTIVE_ESCALATION",
                    severity="CRITICAL",
                    objective_id=o.objective_id,
                    title="Objective Blocked",
                    summary=f"Objective {o.objective_id} requires intervention.",
                    reason=o.terminal_reason or "Unknown",
                    created_at=o.updated_at,
                    available_actions=["Review", "Cancel", "Replan"],
                    source_type="OBJECTIVE",
                    source_id=o.objective_id
                ))
        return actions

    def get_bootstrap_state(self) -> DesktopBootstrapState:
        return DesktopBootstrapState(
            overview=self.get_overview(),
            director=self.get_director_status(),
            objectives=self.list_objectives()[:10],
            active_missions=[m for m in self.list_missions(50) if m.status == "ACTIVE"][:10],
            departments=self.list_departments(),
            pending_approvals=self.list_pending_approvals(),
            owner_actions=self.list_owner_actions(),
            financial_summary=self._get_finance_summary(),
            recent_decisions=[], 
            recent_activity=[], 
            system_health="HEALTHY"
        )