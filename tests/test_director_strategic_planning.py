import unittest
import os
import tempfile
from app.schemas.company_objective import CompanyObjective, CompanyObjectiveSuccessCriteria
from app.schemas.strategic_plan import StrategicPlanStatus
from app.services.director_strategy import DirectorStrategyService
from app.repositories.strategic_plan_repository import strategic_plan_repository

class MockAgentMeta:
    def __init__(self, name, caps):
        self.name = name
        self.capabilities = caps

class MockRegistry:
    def list_all_metadata(self):
        return [
            MockAgentMeta("engineering_intelligence", ["coding", "deployment", "infrastructure"]),
            MockAgentMeta("marketing_intelligence", ["campaign", "social_media", "content"]),
            MockAgentMeta("sales_intelligence", ["lead_generation", "crm", "outreach"]),
            MockAgentMeta("finance_intelligence", ["budget_management", "cash_flow_analysis", "roi_analysis"]),
            MockAgentMeta("research_intelligence", ["market_research", "data_analysis"])
        ]

class TestDirectorStrategicPlanning(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_path = os.path.join(self.temp_dir.name, "plans.json")
        strategic_plan_repository.storage_path = self.storage_path
        strategic_plan_repository._plans = {}
        
        self.strategy_service = DirectorStrategyService(agent_registry=MockRegistry())

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_valid_objective_produces_strategic_plan(self):
        obj = CompanyObjective(
            objective_id="obj_12345678",
            title="Increase web revenue via lead generation and coding.",
            objective="We need to build a new platform and sell it.",
            success_criteria=CompanyObjectiveSuccessCriteria(criterion="revenue", required=1000),
            max_missions=5, max_strategy_changes=2, max_zero_progress_cycles=2
        )
        
        plan = self.strategy_service.develop_strategy(obj)
        
        self.assertEqual(plan.objective_id, obj.objective_id)
        self.assertEqual(plan.objective_version, obj.version)
        self.assertEqual(plan.status, StrategicPlanStatus.READY)
        self.assertEqual(plan.execution_readiness, "READY")
        
        # Verify departments mapped correctly
        assigned_depts = [a.agent_id for a in plan.department_assignments]
        self.assertIn("engineering_intelligence", assigned_depts)
        self.assertIn("sales_intelligence", assigned_depts)
        
        # Verify irrelevant departments excluded (e.g. finance/marketing shouldn't be here strictly based on prompt)
        self.assertNotIn("finance_intelligence", assigned_depts)
        
        # Verify success criteria preserved
        self.assertEqual(plan.success_criteria.get("criterion"), "revenue")
        self.assertEqual(plan.success_criteria.get("required"), 1000)

    def test_missing_critical_detail_triggers_clarification(self):
        obj = CompanyObjective(
            objective_id="obj_12345678",
            title="Make us better",
            objective="Improve the company overall.",
            success_criteria=CompanyObjectiveSuccessCriteria(criterion="improvement", required=1),
            max_missions=5, max_strategy_changes=2, max_zero_progress_cycles=2
        )
        
        plan = self.strategy_service.develop_strategy(obj)
        
        self.assertEqual(plan.status, StrategicPlanStatus.NEEDS_CLARIFICATION)
        self.assertEqual(plan.execution_readiness, "NEEDS_CLARIFICATION")
        self.assertTrue(len(plan.clarification_questions) > 0)
        self.assertEqual(plan.workstreams[0].title, "General Discovery")

    def test_capability_gap_blocks_readiness(self):
        # Create a mock registry missing engineering entirely
        class GapRegistry:
            def list_all_metadata(self):
                return [MockAgentMeta("marketing_intelligence", ["campaign"])]
                
        gap_service = DirectorStrategyService(agent_registry=GapRegistry())
        
        obj = CompanyObjective(
            objective_id="obj_12345678",
            title="Build a web application",
            objective="Code and deploy a web app.",
            success_criteria=CompanyObjectiveSuccessCriteria(criterion="app", required=1),
            max_missions=5, max_strategy_changes=2, max_zero_progress_cycles=2
        )
        
        plan = gap_service.develop_strategy(obj)
        
        self.assertEqual(plan.status, StrategicPlanStatus.BLOCKED)
        self.assertEqual(plan.execution_readiness, "BLOCKED")
        self.assertTrue(any("CAPABILITY_GAP" in q for q in plan.clarification_questions))

    def test_stale_objective_version_invalidates_strategy(self):
        obj = CompanyObjective(
            objective_id="obj_12345678",
            title="Increase web revenue",
            objective="Build a web app.",
            success_criteria=CompanyObjectiveSuccessCriteria(criterion="revenue", required=1000),
            max_missions=5, max_strategy_changes=2, max_zero_progress_cycles=2
        )
        
        plan1 = self.strategy_service.develop_strategy(obj)
        self.assertEqual(plan1.status, StrategicPlanStatus.READY)
        
        # Simulate objective update (version increments)
        obj.version = 2
        
        plan2 = self.strategy_service.develop_strategy(obj)
        
        self.assertNotEqual(plan1.strategic_plan_id, plan2.strategic_plan_id)
        self.assertEqual(plan2.objective_version, 2)
        
        # The repository should have superseded the old plan
        saved_plan1 = strategic_plan_repository.get(plan1.strategic_plan_id)
        self.assertEqual(saved_plan1.status, StrategicPlanStatus.SUPERSEDED)

    def test_strategy_idempotency(self):
        obj = CompanyObjective(
            objective_id="obj_12345678",
            title="Increase web revenue",
            objective="Build a web app.",
            success_criteria=CompanyObjectiveSuccessCriteria(criterion="revenue", required=1000),
            max_missions=5, max_strategy_changes=2, max_zero_progress_cycles=2
        )
        
        plan1 = self.strategy_service.develop_strategy(obj)
        plan2 = self.strategy_service.develop_strategy(obj)
        
        # Since objective version didn't change, the service should return the exact same plan ID
        self.assertEqual(plan1.strategic_plan_id, plan2.strategic_plan_id)

    def test_department_capability_does_not_grant_permissions(self):
        obj = CompanyObjective(
            objective_id="obj_12345678",
            title="Finance budget check",
            objective="Analyze the budget constraints.",
            success_criteria=CompanyObjectiveSuccessCriteria(criterion="report", required=1),
            max_missions=5, max_strategy_changes=2, max_zero_progress_cycles=2
        )
        plan = self.strategy_service.develop_strategy(obj)
        
        assigned_depts = [a.agent_id for a in plan.department_assignments]
        self.assertIn("finance_intelligence", assigned_depts)
        
        # Verify the assignment structure does not contain execution authority
        finance_assignment = next(a for a in plan.department_assignments if a.agent_id == "finance_intelligence")
        
        assignment_dict = finance_assignment.model_dump()
        self.assertNotIn("granted_permissions", assignment_dict)
        self.assertNotIn("FINANCIAL_COMMITMENT", str(assignment_dict))

if __name__ == "__main__":
    unittest.main()