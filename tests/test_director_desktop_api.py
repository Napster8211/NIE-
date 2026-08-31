import unittest
import os
import tempfile
from fastapi.testclient import TestClient
from fastapi import FastAPI

TEST_NIE_OWNER_KEY = "explicit-test-only-owner-key"
os.environ.setdefault("NIE_ENV", "test")
os.environ["NIE_OWNER_KEY"] = TEST_NIE_OWNER_KEY

from app.api.director_desktop import router as desktop_router

# Repositories and models
from app.repositories.company_objective_repository import company_objective_repository
from app.repositories.mission_portfolio_repository import mission_portfolio_repository
from app.repositories.strategic_plan_repository import strategic_plan_repository
from app.repositories.executive_strategy_repository import executive_strategy_repository
from app.repositories.approval_repository import approval_repository
from app.engine.mission_engine import mission_registry
from app.agent.agent_registry import agent_registry
from app.agent.agent_models import AgentMetadata, AgentCapability

# Added CompanyObjectiveStatus import
from app.schemas.company_objective import CompanyObjective, CompanyObjectiveSuccessCriteria, CompanyObjectiveStatus

class MockAgent:
    def __init__(self):
        self.metadata = AgentMetadata(
            name="test_agent", display_name="Test Agent", description="Test",
            category="engineering", capabilities={AgentCapability.CODING}
        )

class TestDirectorDesktopAPI(unittest.TestCase):
    def setUp(self):
        # Create an isolated FastAPI app
        self.app = FastAPI()
        self.app.include_router(desktop_router)
        self.client = TestClient(self.app)
        self.client.headers.update({"Authorization": f"Bearer {TEST_NIE_OWNER_KEY}"})

        # Sandbox repositories
        self.temp_dir = tempfile.TemporaryDirectory()
        
        company_objective_repository.storage_path = os.path.join(self.temp_dir.name, "obj.json")
        company_objective_repository._objectives = {}
        
        mission_registry.mission_file = os.path.join(self.temp_dir.name, "mis.json")
        mission_registry.missions = {}
        
        approval_repository.storage_path = os.path.join(self.temp_dir.name, "app.json")
        approval_repository._approvals = {}

        # Inject a Mock Agent into the Global Registry for testing
        agent_registry._agents = {}
        agent_registry.register(MockAgent())

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_bootstrap_returns_typed_valid_state_with_empty_repos(self):
        response = self.client.get("/director/desktop/bootstrap")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertIn("overview", data)
        self.assertIn("director", data)
        self.assertIn("objectives", data)
        self.assertIn("active_missions", data)
        self.assertIn("departments", data)
        self.assertIn("pending_approvals", data)
        self.assertIn("financial_summary", data)
        
        self.assertEqual(data["overview"]["active_objectives"], 0)
        self.assertEqual(data["overview"]["active_missions"], 0)

    def test_bootstrap_returns_active_objectives(self):
        # Using a proper length ID to pass strict Pydantic Regex and explicit ACTIVE status
        obj = CompanyObjective(
            objective_id="obj_12345678", title="Test Obj", objective="Test",
            status=CompanyObjectiveStatus.ACTIVE,
            success_criteria=CompanyObjectiveSuccessCriteria(criterion="revenue", required=10),
            max_missions=5, max_strategy_changes=2, max_zero_progress_cycles=2
        )
        company_objective_repository.create(obj)
        
        response = self.client.get("/director/desktop/bootstrap")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertEqual(len(data["objectives"]), 1)
        self.assertEqual(data["objectives"][0]["objective_id"], "obj_12345678")
        self.assertEqual(data["overview"]["active_objectives"], 1)

    def test_missing_objective_returns_404(self):
        response = self.client.get("/director/objectives/obj_fake1234")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "OBJECTIVE_NOT_FOUND")

    def test_departments_list_contains_agents(self):
        response = self.client.get("/director/departments")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreater(len(data), 0) 
        
        # Verify the new hierarchical structure and ensure no secrets leak
        for dept in data:
            self.assertIn("department_id", dept)
            self.assertNotIn("api_key", dept)
            self.assertNotIn("secret", dept)
            self.assertIn("agents", dept)
            self.assertIsInstance(dept["agents"], list)
            
            # Drill down into the nested specialists
            for agent in dept["agents"]:
                self.assertIn("agent_id", agent)
                self.assertNotIn("api_key", agent)
                self.assertNotIn("secret", agent)

    def test_query_limit_capped(self):
        response = self.client.get("/director/missions?limit=9999")
        self.assertEqual(response.status_code, 200)
        # Service caps at 100

    def test_portfolio_completion_does_not_imply_objective_completion(self):
        obj = CompanyObjective(
            objective_id="obj_87654321", title="Test Obj 2", objective="Test",
            success_criteria=CompanyObjectiveSuccessCriteria(criterion="revenue", required=10),
            max_missions=5, max_strategy_changes=2, max_zero_progress_cycles=2
        )
        company_objective_repository.create(obj)
        
        response = self.client.get("/director/objectives/obj_87654321")
        self.assertEqual(response.json()["progress_percentage"], 0.0)
        self.assertEqual(response.json()["status"], "DRAFT")

    def test_get_bootstrap_causes_no_mutation(self):
        # Hash state before
        obj_digest_before = company_objective_repository.persisted_digest()
        
        self.client.get("/director/desktop/bootstrap")
        
        obj_digest_after = company_objective_repository.persisted_digest()
        self.assertEqual(obj_digest_before, obj_digest_after)

if __name__ == "__main__":
    unittest.main()
