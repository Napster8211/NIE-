import unittest
import os
import tempfile
from app.schemas.company_objective import CompanyObjective, CompanyObjectiveSuccessCriteria
from app.schemas.strategic_plan import StrategicPlan, StrategicPlanStatus, StrategicWorkstream, DepartmentAssignment
from app.schemas.mission_portfolio import MissionPortfolioStatus, MissionPortfolio, MissionDefinition
from app.schemas.executive_strategy import ExecutiveStrategyEvaluation
from app.services.executive_strategy_service import ExecutiveStrategyService
from app.repositories.executive_strategy_repository import executive_strategy_repository
from app.repositories.mission_portfolio_repository import mission_portfolio_repository
from app.repositories.strategic_plan_repository import strategic_plan_repository
from app.repositories.company_objective_repository import CompanyObjectiveRepository

class MockDecisionRepo:
    def __init__(self):
        self.decisions = []
    def create(self, d):
        self.decisions.append(d)

class TestDirectorExecutiveStrategyLoop(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_path = os.path.join(self.temp_dir.name, "ports.json")
        mission_portfolio_repository.storage_path = self.storage_path
        mission_portfolio_repository._portfolios = {}
        
        self.plan_path = os.path.join(self.temp_dir.name, "plans.json")
        strategic_plan_repository.storage_path = self.plan_path
        strategic_plan_repository._plans = {}

        self.eval_path = os.path.join(self.temp_dir.name, "evals.json")
        executive_strategy_repository.storage_path = self.eval_path
        executive_strategy_repository._evals = {}

        self.obj_path = os.path.join(self.temp_dir.name, "objs.json")
        self.objective_repo = CompanyObjectiveRepository(storage_path=self.obj_path)

        self.decision_repo = MockDecisionRepo()
        self.service = ExecutiveStrategyService(
            objective_repo=self.objective_repo,
            portfolio_repo=mission_portfolio_repository,
            decision_repo=self.decision_repo,
            plan_repo=strategic_plan_repository
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_fixtures(self):
        obj = CompanyObjective(
            objective_id="obj_12345678", title="Test Obj", objective="Test",
            success_criteria=CompanyObjectiveSuccessCriteria(criterion="revenue", required=10),
            max_missions=5, max_strategy_changes=2, max_zero_progress_cycles=2
        )
        self.objective_repo.create(obj)
        
        plan = StrategicPlan(
            strategic_plan_id="plan_12345678", objective_id="obj_12345678", objective_version=1,
            status=StrategicPlanStatus.READY, business_outcome="Test", executive_summary="Test",
            workstreams=[], department_assignments=[]
        )
        strategic_plan_repository.create(plan)
        
        m_def = MissionDefinition(
            mission_definition_id="mdef_1", workstream_id="ws_1", title="Test", objective="T",
            department_id="d_1", success_criterion="s", strategic_reason="r"
        )
        port = MissionPortfolio(
            portfolio_id="port_12345678", objective_id="obj_12345678", objective_version=1,
            strategic_plan_id="plan_12345678", strategic_plan_version=1, status=MissionPortfolioStatus.READY,
            mission_definitions=[m_def]
        )
        mission_portfolio_repository.create(port)
        return obj, plan, port

    def test_verified_mission_outcome_enters_executive_evaluation(self):
        obj, plan, port = self._create_fixtures()
        
        eval_record = self.service.evaluate_portfolio_outcome(
            objective_id=obj.objective_id, portfolio_id=port.portfolio_id,
            terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
            verified_outcome="SUCCESS", evidence_refs=["evi_1"], success_criteria_met=True
        )
        
        self.assertIsNotNone(eval_record)
        self.assertEqual(eval_record.trigger_mission_id, "mis_1")
        self.assertTrue(eval_record.progress_delta > 0)
        self.assertEqual(eval_record.recommendation, "CONTINUE")

    def test_zero_progress_increments_zero_progress_counter(self):
        obj, plan, port = self._create_fixtures()
        
        self.assertEqual(obj.zero_progress_cycles, 0)
        
        eval_record = self.service.evaluate_portfolio_outcome(
            objective_id=obj.objective_id, portfolio_id=port.portfolio_id,
            terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
            verified_outcome="FAILED", evidence_refs=["evi_1"], success_criteria_met=False
        )
        
        # It's an ineffective failure, zero progress should trigger replan because changes=0
        self.assertEqual(eval_record.strategy_effectiveness, "INEFFECTIVE")
        self.assertEqual(eval_record.recommendation, "REPLAN")
        
        updated_obj = self.objective_repo.get(obj.objective_id)
        self.assertEqual(updated_obj.zero_progress_cycles, 1)

    def test_meaningful_progress_resets_zero_progress_counter(self):
        obj, plan, port = self._create_fixtures()
        self.objective_repo.update(obj.objective_id, {"zero_progress_cycles": 1}, expected_version=1)
        port.objective_version = 2
        mission_portfolio_repository.update(port)
        
        eval_record = self.service.evaluate_portfolio_outcome(
            objective_id=obj.objective_id, portfolio_id=port.portfolio_id,
            terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
            verified_outcome="SUCCESS", evidence_refs=["evi_1"], success_criteria_met=True
        )
        
        updated_obj = self.objective_repo.get(obj.objective_id)
        self.assertEqual(updated_obj.zero_progress_cycles, 0) # Reset!

    def test_replan_increments_strategy_revision_count(self):
        obj, plan, port = self._create_fixtures()
        
        self.assertEqual(obj.strategy_change_count, 0)
        
        eval_record = self.service.evaluate_portfolio_outcome(
            objective_id=obj.objective_id, portfolio_id=port.portfolio_id,
            terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
            verified_outcome="FAILED", evidence_refs=["evi_1"], success_criteria_met=False
        )
        
        self.assertEqual(eval_record.recommendation, "REPLAN")
        updated_obj = self.objective_repo.get(obj.objective_id)
        self.assertEqual(updated_obj.strategy_change_count, 1)

    def test_identical_terminal_event_is_idempotent(self):
        obj, plan, port = self._create_fixtures()
        
        eval1 = self.service.evaluate_portfolio_outcome(
            objective_id=obj.objective_id, portfolio_id=port.portfolio_id,
            terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
            verified_outcome="SUCCESS", evidence_refs=["evi_1"], success_criteria_met=True
        )
        
        eval2 = self.service.evaluate_portfolio_outcome(
            objective_id=obj.objective_id, portfolio_id=port.portfolio_id,
            terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
            verified_outcome="SUCCESS", evidence_refs=["evi_1"], success_criteria_met=True
        )
        
        self.assertEqual(eval1.evaluation_id, eval2.evaluation_id) # Deduped!

    def test_executive_decision_persisted(self):
        obj, plan, port = self._create_fixtures()
        eval_record = self.service.evaluate_portfolio_outcome(
            objective_id=obj.objective_id, portfolio_id=port.portfolio_id,
            terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
            verified_outcome="SUCCESS", evidence_refs=["evi_1"], success_criteria_met=True
        )
        
        decision = self.service.generate_decision(eval_record)
        self.assertEqual(len(self.decision_repo.decisions), 1)
        self.assertEqual(decision.mission_id, "mis_1")

    def test_stale_objective_version_invalidates_evaluation(self):
        obj, plan, port = self._create_fixtures()
        
        # Manually bump objective version (simulating an external owner edit)
        self.objective_repo.update(obj.objective_id, {"title": "Updated"}, expected_version=1)
        
        with self.assertRaisesRegex(ValueError, "STALE_PORTFOLIO_STATE"):
            self.service.evaluate_portfolio_outcome(
                objective_id=obj.objective_id, portfolio_id=port.portfolio_id,
                terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
                verified_outcome="SUCCESS", evidence_refs=["evi_1"], success_criteria_met=True
            )

    def test_executive_cycle_limit_enforced(self):
        obj, plan, port = self._create_fixtures()
        self.objective_repo.update(obj.objective_id, {"metadata": {"executive_cycle_count": 19}}, expected_version=1)
        port.objective_version = 2
        mission_portfolio_repository.update(port)
        
        eval_record = self.service.evaluate_portfolio_outcome(
            objective_id=obj.objective_id, portfolio_id=port.portfolio_id,
            terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
            verified_outcome="SUCCESS", evidence_refs=["evi_1"], success_criteria_met=True
        )
        
        self.assertEqual(eval_record.recommendation, "PAUSE")
        self.assertIn("MAX_EXECUTIVE_CYCLES_REACHED", eval_record.reason_codes)

    def test_strategy_revision_limit_requests_owner_decision(self):
        obj, plan, port = self._create_fixtures()
        # Max strategy changes is 2
        self.objective_repo.update(obj.objective_id, {"strategy_change_count": 2}, expected_version=1)
        port.objective_version = 2
        mission_portfolio_repository.update(port)
        
        eval_record = self.service.evaluate_portfolio_outcome(
            objective_id=obj.objective_id, portfolio_id=port.portfolio_id,
            terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
            verified_outcome="FAILED", evidence_refs=["evi_1"], success_criteria_met=False
        )
        
        self.assertEqual(eval_record.recommendation, "REQUEST_OWNER_DECISION")
        self.assertIn("STRATEGY_REVISION_LIMIT_REACHED", eval_record.reason_codes)

if __name__ == "__main__":
    unittest.main()