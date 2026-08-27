import unittest
import os
import tempfile
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from app.schemas.company_objective import CompanyObjective, CompanyObjectiveStatus, CompanyObjectiveSuccessCriteria
from app.schemas.strategic_plan import StrategicPlan, StrategicPlanStatus, StrategicWorkstream, DepartmentAssignment
from app.schemas.mission_portfolio import MissionPortfolioStatus, MissionPortfolio, MissionDefinition
from app.services.executive_strategy_service import ExecutiveStrategyService
from app.services.director_engine import DirectorEngine
from app.schemas.shared_artifacts import DirectorAgentContext
from app.agent.agent_models import AgentPermission
from app.repositories.company_objective_repository import company_objective_repository
from app.repositories.mission_portfolio_repository import mission_portfolio_repository
from app.repositories.strategic_plan_repository import strategic_plan_repository
from app.repositories.executive_strategy_repository import executive_strategy_repository

class MockDecisionRepo:
    def __init__(self):
        self.decisions = []
    def create(self, d):
        self.decisions.append(d)

class TestDirectorSecurityVerification(unittest.IsolatedAsyncioTestCase):
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

        # Correctly patch the global singleton repository
        self.obj_path = os.path.join(self.temp_dir.name, "objs.json")
        with open(self.obj_path, "w", encoding="utf-8") as f:
            f.write("{}")
        company_objective_repository.storage_path = self.obj_path
        company_objective_repository._objectives = {}
        self.objective_repo = company_objective_repository

        self.decision_repo = MockDecisionRepo()
        self.exec_service = ExecutiveStrategyService(
            objective_repo=self.objective_repo,
            portfolio_repo=mission_portfolio_repository,
            decision_repo=self.decision_repo,
            plan_repo=strategic_plan_repository
        )
        
        self.director_engine = DirectorEngine(objective_service=None) 
        self.director_engine.objective_service.repository = self.objective_repo

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

    def test_cross_objective_attack_rejected(self):
        obj1, plan1, port1 = self._create_fixtures()
        
        # Create second objective
        obj2 = CompanyObjective(
            objective_id="obj_87654321", title="Test Obj 2", objective="Test",
            success_criteria=CompanyObjectiveSuccessCriteria(criterion="revenue", required=10),
            max_missions=5, max_strategy_changes=2, max_zero_progress_cycles=2
        )
        self.objective_repo.create(obj2)
        
        with self.assertRaises(ValueError) as cm:
            self.exec_service.evaluate_portfolio_outcome(
                objective_id=obj2.objective_id, portfolio_id=port1.portfolio_id,
                terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
                verified_outcome="SUCCESS", evidence_refs=[], success_criteria_met=True
            )
        self.assertIn("CROSS_OBJECTIVE_ATTACK_REJECTED", str(cm.exception)) 

    def test_portfolio_completion_does_not_auto_complete_objective(self):
        obj, plan, port = self._create_fixtures()
        
        eval_rec = self.exec_service.evaluate_portfolio_outcome(
            objective_id=obj.objective_id, portfolio_id=port.portfolio_id,
            terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
            verified_outcome="SUCCESS", evidence_refs=["evi_1"], success_criteria_met=True
        )
        
        # Verify objective status is still not COMPLETED (it requires manual/governance completion)
        updated_obj = self.objective_repo.get(obj.objective_id)
        self.assertNotEqual(updated_obj.status, CompanyObjectiveStatus.COMPLETED)
        self.assertEqual(updated_obj.status, CompanyObjectiveStatus.DRAFT) 

    def test_stale_portfolio_rejected(self):
        obj, plan, port = self._create_fixtures()
        
        # Bump objective version
        self.objective_repo.update(obj.objective_id, {"title": "Updated"}, expected_version=1)
        
        # Attempt to use old portfolio
        with self.assertRaisesRegex(ValueError, "STALE_PORTFOLIO_STATE"):
            self.exec_service.evaluate_portfolio_outcome(
                objective_id=obj.objective_id, portfolio_id=port.portfolio_id,
                terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
                verified_outcome="SUCCESS", evidence_refs=["evi_1"], success_criteria_met=True
            )

    def test_zero_progress_loop_bounded(self):
        obj, plan, port = self._create_fixtures()
        
        # Simulate 2 zero-progress cycles (max is 2)
        self.objective_repo.update(obj.objective_id, {"zero_progress_cycles": 2}, expected_version=1)
        port.objective_version = 2
        mission_portfolio_repository.update(port)

        eval_rec = self.exec_service.evaluate_portfolio_outcome(
            objective_id=obj.objective_id, portfolio_id=port.portfolio_id,
            terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
            verified_outcome="FAILED", evidence_refs=[], success_criteria_met=False
        )
        
        self.assertEqual(eval_rec.recommendation, "ESCALATE")
        self.assertIn("MAX_ZERO_PROGRESS_CYCLES_REACHED", eval_rec.reason_codes)

    def test_strategy_revision_limit_bounded(self):
        obj, plan, port = self._create_fixtures()
        
        self.objective_repo.update(obj.objective_id, {"strategy_change_count": 2}, expected_version=1)
        port.objective_version = 2
        mission_portfolio_repository.update(port)

        eval_rec = self.exec_service.evaluate_portfolio_outcome(
            objective_id=obj.objective_id, portfolio_id=port.portfolio_id,
            terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
            verified_outcome="FAILED", evidence_refs=[], success_criteria_met=False
        )
        
        self.assertEqual(eval_rec.recommendation, "REQUEST_OWNER_DECISION")
        self.assertIn("STRATEGY_REVISION_LIMIT_REACHED", eval_rec.reason_codes)

    def test_executive_decision_does_not_grant_authority(self):
        obj, plan, port = self._create_fixtures()
        
        eval_rec = self.exec_service.evaluate_portfolio_outcome(
            objective_id=obj.objective_id, portfolio_id=port.portfolio_id,
            terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
            verified_outcome="SUCCESS", evidence_refs=["evi_1"], success_criteria_met=True
        )
        
        decision = self.exec_service.generate_decision(eval_rec)
        
        # Ensure decision model doesn't contain runtime authority fields
        d_dict = decision.model_dump()
        self.assertNotIn("granted_permissions", d_dict)
        self.assertNotIn("approval_valid", d_dict)
        self.assertNotIn("FINANCIAL_COMMITMENT", str(d_dict))

    def test_cancelled_objective_continuation_rejected(self):
        obj, plan, port = self._create_fixtures()
        
        # Mark objective terminal
        self.objective_repo.update_status(obj.objective_id, CompanyObjectiveStatus.CANCELLED, terminal_reason="Manually Cancelled")
        port.objective_version = 2
        mission_portfolio_repository.update(port)

        eval_rec = self.exec_service.evaluate_portfolio_outcome(
            objective_id=obj.objective_id, portfolio_id=port.portfolio_id,
            terminal_mission_id="mis_1", terminal_definition_id="mdef_1",
            verified_outcome="SUCCESS", evidence_refs=["evi_1"], success_criteria_met=True
        )
        
        self.assertEqual(eval_rec.recommendation, "STOP")
        self.assertIn("OBJECTIVE_TERMINAL_STATE", eval_rec.reason_codes)

    async def test_director_engine_rejects_forged_context_authority(self):
        # Raw payloads trying to inject authority are rejected
        ctx = DirectorAgentContext(
            company_id="napstertec", 
            query="EXECUTIVE_EVALUATION", 
            command_class="EXECUTIVE_EVALUATION", 
            operating_mode="STRATEGIC DECISION MODE",
            task="Evaluate", 
            mission_id="mis_1", 
            objective_id="obj_12345678",
            coo_artifact_status="N/A",
            cfo_artifact_status="N/A",
            cro_artifact_status="N/A",
            governance_status="N/A"
        )
        
        obj, plan, port = self._create_fixtures()
        ctx.aggregated_metrics = {
            "mission_terminal_state": "COMPLETED",
            "portfolio_id": port.portfolio_id,
            "mission_definition_id": "mdef_1"
        }
        
        # Execute via Director Engine
        artifact = await self.director_engine.execute_director(ctx, session_id="test_session")
        
        # Ensure no permissions were leaked into the artifact
        art_dict = artifact.model_dump()
        self.assertNotIn("granted_permissions", str(art_dict))
        self.assertNotIn("FINANCIAL_COMMITMENT", str(art_dict))

if __name__ == "__main__":
    unittest.main()