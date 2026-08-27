import unittest
import os
import tempfile
from decimal import Decimal

from app.schemas.finance import (
    ObjectiveBudget, MissionBudgetAllocation, BudgetReservation,
    FinancialCommitment, ActualSpend, BudgetStatus, safe_money, normalize_currency
)
from app.repositories.finance_repository import FinanceRepository
from app.services.finance_engine import FinanceEngine
from app.tools.plugins.finance_tools import AssessAffordabilityTool

class TestCFOGovernance(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_path = os.path.join(self.temp_dir.name, "finance_cfo.json")
        self.repo = FinanceRepository(storage_path=self.storage_path)
        
        # Override the default path in BOTH the tool and engine module namespaces
        import app.repositories.finance_repository as fr
        import app.services.finance_engine as fe
        
        self.original_repo_fr = fr.finance_repository
        self.original_repo_fe = fe.finance_repository
        
        fr.finance_repository = self.repo
        fe.finance_repository = self.repo
        
        self.engine = FinanceEngine()

    def tearDown(self):
        import app.repositories.finance_repository as fr
        import app.services.finance_engine as fe
        
        fr.finance_repository = self.original_repo_fr
        fe.finance_repository = self.original_repo_fe
        self.temp_dir.cleanup()

    # --- SNAPSHOT TESTS ---
    def test_snapshot_derived_from_ledger(self):
        self.repo.create_objective_budget("obj_test_1", "USD", 10000)
        self.repo.allocate_to_mission("obj_test_1", "mis_test_1", 3000)
        
        snap = self.engine.generate_snapshot("obj_test_1")
        self.assertEqual(snap.objective_id, "obj_test_1")
        self.assertEqual(snap.currency, "USD")
        self.assertEqual(snap.total_budget, Decimal("10000.00"))
        self.assertEqual(snap.allocated_amount, Decimal("3000.00"))
        self.assertEqual(snap.available_amount, Decimal("7000.00"))
        self.assertEqual(snap.data_quality, "VERIFIED")

    def test_no_budget_returns_unset_and_unconfigured(self):
        snap = self.engine.generate_snapshot("obj_missing")
        self.assertEqual(snap.budget_status, BudgetStatus.UNSET)
        self.assertEqual(snap.data_quality, "NOT_CONFIGURED")

    # --- CFO ASSESSMENT TESTS ---
    def test_healthy_budget_assessment(self):
        self.repo.create_objective_budget("obj_test_1", "USD", 10000)
        snap = self.engine.generate_snapshot("obj_test_1")
        assess = self.engine.assess_finances(snap)
        
        self.assertEqual(assess.financial_status, "HEALTHY")
        self.assertEqual(assess.risk_level, "LOW")
        self.assertIn("CONTINUE", assess.recommendations)

    def test_warning_threshold_assessment(self):
        self.repo.create_objective_budget("obj_test_1", "USD", 1000)
        self.repo.allocate_to_mission("obj_test_1", "mis_test_1", 850) # 85% util
        
        snap = self.engine.generate_snapshot("obj_test_1")
        assess = self.engine.assess_finances(snap)
        
        self.assertEqual(assess.financial_status, "WARNING")
        self.assertEqual(assess.risk_level, "MEDIUM")
        self.assertIn("CONTINUE_WITH_CAUTION", assess.recommendations)

    def test_exhausted_assessment(self):
        self.repo.create_objective_budget("obj_test_1", "USD", 1000)
        self.repo.allocate_to_mission("obj_test_1", "mis_test_1", 1000)
        
        snap = self.engine.generate_snapshot("obj_test_1")
        assess = self.engine.assess_finances(snap)
        
        self.assertEqual(assess.financial_status, "EXHAUSTED")
        self.assertEqual(assess.risk_level, "CRITICAL")
        self.assertIn("PAUSE_COST_BEARING_WORK", assess.recommendations)

    def test_missing_budget_does_not_become_healthy(self):
        snap = self.engine.generate_snapshot("obj_missing")
        assess = self.engine.assess_finances(snap)
        
        self.assertEqual(assess.financial_status, "NOT_CONFIGURED")
        self.assertEqual(assess.risk_level, "UNKNOWN")
        self.assertNotIn("HEALTHY", assess.financial_status)

    def test_cfo_assessment_grants_no_permissions(self):
        from app.agent.definitions.finance_agent import FinanceIntelligenceAgent
        agent = FinanceIntelligenceAgent()
        # Verify it has READ, but absolutely NOT FINANCIAL_COMMITMENT
        from app.agent.agent_models import AgentPermission
        self.assertIn(AgentPermission.READ, agent.metadata.required_permissions)
        self.assertNotIn(AgentPermission.FINANCIAL_COMMITMENT, agent.metadata.required_permissions)
        self.assertNotIn(AgentPermission.WRITE_EXTERNAL, agent.metadata.required_permissions)

    # --- AFFORDABILITY TESTS ---
    async def test_affordable_mission_proposal(self):
        self.repo.create_objective_budget("obj_1", "GHS", 5000)
        self.repo.allocate_to_mission("obj_1", "mis_1", 2000)
        
        tool = AssessAffordabilityTool()
        result = await tool.execute(objective_id="obj_1", mission_id="mis_1", estimated_cost=500.0, currency="GHS")
        self.assertTrue(result["affordable"])
        
    async def test_unaffordable_mission_proposal(self):
        self.repo.create_objective_budget("obj_1", "GHS", 5000)
        self.repo.allocate_to_mission("obj_1", "mis_1", 2000)
        
        tool = AssessAffordabilityTool()
        result = await tool.execute(objective_id="obj_1", mission_id="mis_1", estimated_cost=2500.0, currency="GHS")
        self.assertFalse(result["affordable"])

    async def test_affordability_currency_mismatch(self):
        self.repo.create_objective_budget("obj_1", "GHS", 5000)
        self.repo.allocate_to_mission("obj_1", "mis_1", 2000)
        
        tool = AssessAffordabilityTool()
        result = await tool.execute(objective_id="obj_1", mission_id="mis_1", estimated_cost=500.0, currency="USD")
        self.assertFalse(result["affordable"])
        self.assertIn("CURRENCY_MISMATCH", result["reason"])

if __name__ == "__main__":
    unittest.main()