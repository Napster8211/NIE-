"""
NapsterTec AI - Finance Intelligence Agent
Module: app/agent/definitions/finance_agent.py
"""
import logging
from typing import Dict, Any

from app.agent.base_agent import BaseAgent
from app.agent.agent_models import AgentMetadata, AgentContext, AgentResult, AgentPermission, AgentCapability

logger = logging.getLogger(__name__)

class FinanceIntelligenceAgent(BaseAgent):
    def __init__(self, **kwargs):
        metadata = AgentMetadata(
            name="finance_intelligence",
            display_name="Finance Intelligence Director (AI CFO)",
            description="Evaluates the financial health of NapsterTec, monitors cash flow, estimates runway, and evaluates ROI.",
            version="1.0.0",
            category="finance",
            capabilities={AgentCapability.RESEARCH},
            supported_task_types=["analyze financial health", "calculate runway", "budget management", "roi analysis", "cfo report"],
            allowed_tools={"finance_context_builder", "finance_evaluator", "finance_artifact_saver"},
            required_permissions={AgentPermission.READ, AgentPermission.WRITE} 
        )
        super().__init__(metadata=metadata, **kwargs)

    async def execute(self, context: AgentContext) -> AgentResult:
        result = AgentResult(success=False, agent_name=self.metadata.name, session_id=context.session_id)
        
        try:
            search_query = context.planner_output.get("query", context.task)
            
            b_res = await self.invoke_tool("finance_context_builder", {"query": search_query}, context)
            b_data = getattr(b_res["output"], "data", b_res["output"])
            if hasattr(b_data, "model_dump"): b_data = b_data.model_dump()
            if not isinstance(b_data, dict): b_data = {}
            
            if not b_data.get("found"):
                result.final_output = f"Task Failed: {b_data.get('error', 'Context missing.')}"
                return result
            iso_context = b_data["isolated_context"]

            e_res = await self.invoke_tool("finance_evaluator", {"context": iso_context}, context)
            e_data = getattr(e_res["output"], "data", e_res["output"])
            if hasattr(e_data, "model_dump"): e_data = e_data.model_dump()
            if not isinstance(e_data, dict): e_data = {}
            
            if not e_data or "artifact" not in e_data:
                result.final_output = "Task Failed: Evaluator failed to generate artifact."
                return result
            artifact = e_data["artifact"]

            s_res = await self.invoke_tool("finance_artifact_saver", {"artifact": artifact}, context)
            s_data = getattr(s_res["output"], "data", s_res["output"])
            if hasattr(s_data, "model_dump"): s_data = s_data.model_dump()
            if not isinstance(s_data, dict): s_data = {}

            if s_data.get("success") and s_data.get("registered"):
                result.success = True
                status_msg = "Completed"
            else:
                result.success = False
                status_msg = "Persistence Failed"

            health = artifact.get('financial_health', {})
            rev = artifact.get('revenue_summary', {})
            exp = artifact.get('expense_summary', {})
            runway = artifact.get('runway', {})
            risks = artifact.get('financial_risks', [])
            roi = artifact.get('roi_analysis', [])
            recs = artifact.get('financial_recommendations', [])
            budgets = artifact.get('budgets', [])

            top_risk = risks[0].get('risk_type') if risks else "None"
            top_roi = roi[0].get('investment_area') if roi else "None"

            summary = (
                f"**Finance Intelligence Execution Report**\n\n"
                f"Financial Health Score: {health.get('score')}/100 ({health.get('trend')})\n"
                f"Revenue Forecast: {rev.get('total_expected_revenue')}\n"
                f"Expense Forecast: {exp.get('total_expenses')}\n"
                f"Cash Runway: {runway.get('cash_runway')} (Safe Window: {runway.get('safe_operating_window')})\n"
                f"Burn Rate: {runway.get('monthly_burn')}\n"
                f"Budget Health: {len(budgets)} Departments Evaluated\n"
                f"Top Financial Risk: {top_risk}\n"
                f"Highest ROI Investment: {top_roi}\n"
                f"Executive Recommendations: {recs[0] if recs else 'None'}\n\n"
                f"**Artifact Generation:**\n"
                f"Artifact Created: FinanceArtifact\n"
                f"Artifact ID: {s_data.get('artifact_id', 'Unknown')}\n"
                f"Repository Saved: {'Yes' if s_data.get('success') else 'No'} (v{s_data.get('version', 0)})\n"
                f"Registry Registered: {'Yes' if s_data.get('registered') else 'No'}\n"
                f"Validation: {s_data.get('validation', 'Failed')}\n\n"
                f"Status: {status_msg}"
            )
            
            result.final_output = summary
            result.tool_calls.extend([b_res, e_res, s_res])
            return result

        except Exception as e:
            logger.error(f"[Finance Agent] Execution failed: {str(e)}", exc_info=True)
            result.errors.append(str(e))
            return result