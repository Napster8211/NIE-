import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from decimal import Decimal

from app.schemas.shared_artifacts import FinanceAgentContext, FinanceArtifact
from app.schemas.finance import (
    FinancialGovernanceSnapshot, CFOFinancialAssessment, BudgetStatus
)
from app.repositories.finance_repository import finance_repository

class FinanceEngine:
    def _determine_health_status(self, available: Decimal, utilization: float, threshold: float, total: Decimal) -> str:
        if total == 0:
            return "NOT_CONFIGURED"
        if available < 0:
            return "OVER_BUDGET"
        if available == 0:
            return "EXHAUSTED"
        if utilization >= threshold:
            return "WARNING"
        return "HEALTHY"

    def _determine_risk_level(self, status: str) -> str:
        mapping = {
            "HEALTHY": "LOW",
            "WARNING": "MEDIUM",
            "CONSTRAINED": "HIGH",
            "EXHAUSTED": "CRITICAL",
            "OVER_BUDGET": "CRITICAL",
            "NOT_CONFIGURED": "UNKNOWN",
            "DATA_UNAVAILABLE": "UNKNOWN",
            "INCONSISTENT": "CRITICAL"
        }
        return mapping.get(status, "UNKNOWN")

    def generate_snapshot(self, objective_id: str) -> FinancialGovernanceSnapshot:
        try:
            budget = finance_repository.get_objective_budget(objective_id)
            if not budget:
                return FinancialGovernanceSnapshot(
                    snapshot_id=f"snap_{uuid.uuid4().hex[:8]}",
                    objective_id=objective_id,
                    currency="UNKNOWN",
                    budget_status=BudgetStatus.UNSET,
                    data_quality="NOT_CONFIGURED"
                )
            
            # Re-verify invariants for INCONSISTENT detection
            derived_available = budget.total_budget - budget.allocated_amount - budget.reserved_amount - budget.committed_amount - budget.spent_amount
            data_quality = "VERIFIED"
            if derived_available != budget.available_amount:
                data_quality = "INCONSISTENT"
                
            utilization = 0.0
            if budget.total_budget > 0:
                utilization = float((budget.allocated_amount + budget.reserved_amount + budget.committed_amount + budget.spent_amount) / budget.total_budget) * 100

            return FinancialGovernanceSnapshot(
                snapshot_id=f"snap_{uuid.uuid4().hex[:8]}",
                objective_id=budget.objective_id,
                currency=budget.currency,
                total_budget=budget.total_budget,
                allocated_amount=budget.allocated_amount,
                reserved_amount=budget.reserved_amount,
                committed_amount=budget.committed_amount,
                spent_amount=budget.spent_amount,
                available_amount=budget.available_amount,
                utilization_percent=utilization,
                budget_status=budget.status,
                ledger_version=budget.version,
                data_quality=data_quality
            )
        except Exception:
            return FinancialGovernanceSnapshot(
                snapshot_id=f"snap_{uuid.uuid4().hex[:8]}",
                objective_id=objective_id,
                currency="UNKNOWN",
                budget_status=BudgetStatus.UNSET,
                data_quality="DATA_UNAVAILABLE"
            )

    def assess_finances(self, snapshot: FinancialGovernanceSnapshot) -> CFOFinancialAssessment:
        if snapshot.data_quality in {"NOT_CONFIGURED", "DATA_UNAVAILABLE", "INCONSISTENT"}:
            status = snapshot.data_quality
        else:
            status = self._determine_health_status(snapshot.available_amount, snapshot.utilization_percent, snapshot.warning_threshold_percent, snapshot.total_budget)
            
        risk = self._determine_risk_level(status)
        
        findings = []
        recs = []
        requires_attention = False
        
        if status == "NOT_CONFIGURED":
            findings.append("No authoritative financial budget is configured for this objective.")
            recs.append("REQUEST_FINANCIAL_CONFIGURATION")
        elif status == "DATA_UNAVAILABLE":
            findings.append("Financial repository could not be read safely.")
            recs.append("RETRY_ANALYSIS")
        elif status == "INCONSISTENT":
            findings.append("CRITICAL: Ledger double-entry invariants failed validation.")
            recs.append("ESCALATE_TO_HUMAN")
            requires_attention = True
        else:
            findings.append(f"Total Budget: {snapshot.total_budget} {snapshot.currency}")
            findings.append(f"Available: {snapshot.available_amount} {snapshot.currency} ({100 - snapshot.utilization_percent:.1f}%)")
            
            if status == "HEALTHY":
                recs.append("CONTINUE")
            elif status == "WARNING":
                findings.append(f"Budget utilization ({snapshot.utilization_percent:.1f}%) exceeds warning threshold.")
                recs.append("CONTINUE_WITH_CAUTION")
                requires_attention = True
            elif status == "EXHAUSTED":
                findings.append("Available budget is completely exhausted.")
                recs.append("PAUSE_COST_BEARING_WORK")
                recs.append("REQUEST_BUDGET")
                requires_attention = True
            elif status == "OVER_BUDGET":
                findings.append("Budget is overdrawn.")
                recs.append("PAUSE_COST_BEARING_WORK")
                recs.append("ESCALATE_TO_HUMAN")
                requires_attention = True

        return CFOFinancialAssessment(
            assessment_id=f"cfo_{uuid.uuid4().hex[:8]}",
            objective_id=snapshot.objective_id,
            snapshot_id=snapshot.snapshot_id,
            financial_status=status,
            risk_level=risk,
            utilization_percent=snapshot.utilization_percent,
            available_amount=f"{snapshot.available_amount} {snapshot.currency}",
            currency=snapshot.currency,
            findings=findings,
            recommendations=recs,
            requires_director_attention=requires_attention
        )

    def evaluate_finances(self, context: FinanceAgentContext, session_id: str) -> FinanceArtifact:
        snapshot = self.generate_snapshot(context.company_id)
        assessment = self.assess_finances(snapshot)
        
        artifact_id = f"fin_{uuid.uuid4().hex[:8]}"
        
        return FinanceArtifact(
            artifact_id=artifact_id,
            agent_run_id=session_id,
            lead_id=context.company_id,
            executive_summary=f"CFO Assessment: {assessment.financial_status}. Risk: {assessment.risk_level}.",
            financial_health={
                "score": int(100 - snapshot.utilization_percent) if snapshot.data_quality == "VERIFIED" else 0,
                "trend": "Stable",
                "confidence": "100%" if snapshot.data_quality == "VERIFIED" else "0%",
                "operating_margin": "N/A", "gross_margin": "N/A", "net_margin": "N/A",
                "revenue_growth": "N/A", "expense_growth": "N/A",
                "cash_position": f"{snapshot.total_budget} {snapshot.currency}" if snapshot.data_quality == "VERIFIED" else "UNKNOWN",
                "liquidity": "N/A",
                "reasoning": " | ".join(assessment.findings)
            },
            revenue_summary={"total_expected_revenue": "N/A", "recurring_revenue": "N/A", "implementation_revenue": "N/A", "annual_forecast": "N/A"},
            expense_summary={"total_expenses": f"{snapshot.spent_amount} {snapshot.currency}", "infrastructure_costs": "N/A", "marketing_costs": "N/A", "development_costs": "N/A", "ai_provider_costs": "N/A", "future_payroll_allocations": "N/A"},
            runway={"monthly_burn": "N/A", "cash_runway": "N/A", "safe_operating_window": "N/A", "expansion_capacity": "N/A", "hiring_capacity": "N/A", "infrastructure_capacity": "N/A", "investment_capacity": "N/A", "confidence_score": "N/A"},
            budgets=[], roi_analysis=[],
            financial_risks=[{"risk_type": assessment.risk_level, "level": assessment.risk_level, "description": assessment.financial_status, "reasoning": "Ledger derived"}],
            financial_recommendations=assessment.recommendations,
            execution_metadata={
                "evaluation_method": "Deterministic Ledger Assessment",
                "snapshot_id": snapshot.snapshot_id,
                "assessment_id": assessment.assessment_id,
                "ledger_version": snapshot.ledger_version,
                "data_quality": snapshot.data_quality
            }
        )