import unittest
import os
import tempfile
from app.schemas.company_objective import CompanyObjective, CompanyObjectiveSuccessCriteria
from app.schemas.strategic_plan import StrategicPlan, StrategicPlanStatus, StrategicWorkstream, DepartmentAssignment
from app.schemas.mission_portfolio import MissionPortfolioStatus
from app.services.mission_portfolio_service import MissionPortfolioService
from app.repositories.mission_portfolio_repository import mission_portfolio_repository

class TestDirectorMissionPortfolio(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_path = os.path.join(self.temp_dir.name, "ports.json")
        mission_portfolio_repository.storage_path = self.storage_path
        mission_portfolio_repository._portfolios = {}
        
        self.portfolio_service = MissionPortfolioService()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_mock_plan(self):
        return StrategicPlan(
            strategic_plan_id="plan_12345678",
            objective_id="obj_12345678",
            objective_version=1,
            status=StrategicPlanStatus.READY,
            business_outcome="Test",
            executive_summary="Test",
            workstreams=[
                StrategicWorkstream(workstream_id="ws_1", title="WS1", purpose="Do A", desired_outcome="A done"),
                StrategicWorkstream(workstream_id="ws_2", title="WS2", purpose="Do B", desired_outcome="B done", dependencies=["ws_1"])
            ],
            department_assignments=[
                DepartmentAssignment(agent_id="engineering_intelligence", role="Exec", assigned_workstreams=["ws_1"], selection_reason="Needs coding"),
                DepartmentAssignment(agent_id="marketing_intelligence", role="Exec", assigned_workstreams=["ws_2"], selection_reason="Needs marketing")
            ]
        )

    def _create_mock_objective(self):
        return CompanyObjective(
            objective_id="obj_12345678",
            title="Test Obj",
            objective="Test",
            success_criteria=CompanyObjectiveSuccessCriteria(criterion="revenue", required=1000),
            max_missions=5, max_strategy_changes=2, max_zero_progress_cycles=2
        )

    def test_ready_strategic_plan_creates_portfolio(self):
        plan = self._create_mock_plan()
        obj = self._create_mock_objective()
        
        port = self.portfolio_service.materialize_portfolio(plan, obj)
        
        self.assertEqual(port.status, MissionPortfolioStatus.READY)
        self.assertEqual(port.objective_id, obj.objective_id)
        self.assertEqual(port.strategic_plan_id, plan.strategic_plan_id)
        self.assertEqual(len(port.mission_definitions), 2)
        
        # Verify definitions
        def1 = next(d for d in port.mission_definitions if d.workstream_id == "ws_1")
        def2 = next(d for d in port.mission_definitions if d.workstream_id == "ws_2")
        
        self.assertEqual(def1.department_id, "engineering_intelligence")
        self.assertEqual(def2.department_id, "marketing_intelligence")
        
        # Verify dependency map
        self.assertIn(def1.mission_definition_id, def2.dependencies)
        
        # Verify Execution Groups
        self.assertEqual(len(port.execution_groups), 2)
        self.assertIn(def1.mission_definition_id, port.execution_groups[0])
        self.assertIn(def2.mission_definition_id, port.execution_groups[1])

    def test_non_ready_plan_rejected(self):
        plan = self._create_mock_plan()
        plan.status = StrategicPlanStatus.DRAFT
        obj = self._create_mock_objective()
        
        with self.assertRaisesRegex(ValueError, "STRATEGIC_PLAN_NOT_READY"):
            self.portfolio_service.materialize_portfolio(plan, obj)

    def test_circular_dependency_rejected(self):
        plan = self._create_mock_plan()
        # Create a cycle: ws_1 depends on ws_2, ws_2 depends on ws_1
        plan.workstreams[0].dependencies = ["ws_2"]
        obj = self._create_mock_objective()
        
        port = self.portfolio_service.materialize_portfolio(plan, obj)
        self.assertEqual(port.status, MissionPortfolioStatus.PARTIALLY_BLOCKED)
        self.assertTrue(any("CIRCULAR_DEPENDENCY_DETECTED" in b for b in port.blocking_reasons))

    def test_objective_max_missions_enforced(self):
        plan = self._create_mock_plan()
        obj = self._create_mock_objective()
        obj.max_missions = 1 # We have 2 workstreams/missions to create
        
        with self.assertRaisesRegex(ValueError, "OBJECTIVE_MAX_MISSIONS_EXCEEDED"):
            self.portfolio_service.materialize_portfolio(plan, obj)

    def test_stale_objective_rejected(self):
        plan = self._create_mock_plan()
        obj = self._create_mock_objective()
        obj.version = 2 # Plan was made for version 1
        
        with self.assertRaisesRegex(ValueError, "STRATEGIC_PLAN_OBJECTIVE_MISMATCH"):
            self.portfolio_service.materialize_portfolio(plan, obj)

    def test_duplicate_portfolio_materialization_idempotent(self):
        plan = self._create_mock_plan()
        obj = self._create_mock_objective()
        
        port1 = self.portfolio_service.materialize_portfolio(plan, obj)
        port2 = self.portfolio_service.materialize_portfolio(plan, obj)
        
        self.assertEqual(port1.portfolio_id, port2.portfolio_id)

    def test_portfolio_creates_no_agent_permission(self):
        plan = self._create_mock_plan()
        obj = self._create_mock_objective()
        
        port = self.portfolio_service.materialize_portfolio(plan, obj)
        port_dict = port.model_dump()
        
        self.assertNotIn("granted_permissions", port_dict)
        self.assertNotIn("FINANCIAL_COMMITMENT", str(port_dict))
        self.assertNotIn("DEPLOYMENT", str(port_dict))

if __name__ == "__main__":
    unittest.main()