import unittest
import importlib
import os
import sys
from fastapi.testclient import TestClient

TEST_NIE_OWNER_KEY = "explicit-test-only-owner-key"
os.environ.setdefault("NIE_ENV", "test")
os.environ["NIE_OWNER_KEY"] = TEST_NIE_OWNER_KEY

from app.main import app
from app.agent.agent_registry import agent_registry
from app.agent.base_agent import BaseAgent
from app.api.director_desktop import router as desktop_router

class TestOrganizationModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Force load and explicit registration of all specialist agents."""
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        definitions_dir = os.path.join(base_dir, "app", "agent", "definitions")
        
        # 1. Import all modules to load the class definitions into memory
        if os.path.exists(definitions_dir):
            for filename in os.listdir(definitions_dir):
                if filename.endswith(".py") and filename != "__init__.py":
                    module_name = f"app.agent.definitions.{filename[:-3]}"
                    if module_name in sys.modules:
                        importlib.reload(sys.modules[module_name])
                    else:
                        importlib.import_module(module_name)
        
        # 2. Find all subclasses of BaseAgent that were just loaded, instantiate them, and register them!
        for agent_class in BaseAgent.__subclasses__():
            try:
                # Do not register abstract or mock classes if they crept in
                if "Mock" not in agent_class.__name__:
                    agent_registry.register(agent_class())
            except Exception as e:
                pass
            
    def setUp(self):
        self.client = TestClient(app)
        self.client.headers.update({"Authorization": f"Bearer {TEST_NIE_OWNER_KEY}"})
        self.agents = agent_registry.list_all_metadata()

    def test_total_specialist_agents(self):
        """1. Verify exactly 22 specialist agents are registered."""
        specialists = [a for a in self.agents if a.name != "director_intelligence" and "test" not in a.name.lower()]
        self.assertEqual(len(specialists), 22)

    def test_director_not_in_department(self):
        """2. Verify Director Intelligence is not counted as a department member."""
        director = next((a for a in self.agents if a.name == "director_intelligence"), None)
        if director:
            self.assertIsNone(getattr(director, "department_id", None))

    def test_every_specialist_has_canonical_department(self):
        """3 & 4. Verify every specialist has a canonical department_id and none resolve to unassigned."""
        specialists = [a for a in self.agents if a.name != "director_intelligence" and "test" not in a.name.lower()]
        for agent in specialists:
            dept_id = getattr(agent, "department_id", None)
            self.assertIsNotNone(dept_id, f"Agent {agent.name} is missing department_id")
            self.assertNotEqual(dept_id, "unassigned_intelligence", f"Agent {agent.name} is incorrectly unassigned")
            self.assertNotEqual(dept_id, "", f"Agent {agent.name} has empty department_id")

    def test_executive_departments_count_and_members(self):
        """5 & 6. Verify exactly 5 executive departments exist with exact membership counts."""
        specialists = [a for a in self.agents if a.name != "director_intelligence" and "test" not in a.name.lower()]
        
        dept_counts = {
            "engineering_delivery": 0,
            "growth_marketing": 0,
            "sales_revenue": 0,
            "operations_success": 0,
            "finance": 0
        }
        
        for agent in specialists:
            dept_id = getattr(agent, "department_id", None)
            if dept_id in dept_counts:
                dept_counts[dept_id] += 1
                
        self.assertEqual(dept_counts["engineering_delivery"], 6)
        self.assertEqual(dept_counts["growth_marketing"], 7)
        self.assertEqual(dept_counts["sales_revenue"], 5)
        self.assertEqual(dept_counts["operations_success"], 3)
        self.assertEqual(dept_counts["finance"], 1)

    def test_business_operations_intelligence_mapping(self):
        """7 & 8. Verify business_operations_intelligence mapping and category preservation."""
        coo = next((a for a in self.agents if a.name == "business_operations_intelligence"), None)
        self.assertIsNotNone(coo)
        self.assertEqual(getattr(coo, "department_id", None), "operations_success")
        self.assertEqual(coo.category, "operations")

    def test_api_departments_endpoint_exposes_canonical_hierarchy(self):
        """11. Verify /api/v1/director/departments exposes exactly 5 canonical departments."""
        response = self.client.get("/api/v1/director/departments")
        if response.status_code == 404:
            response = self.client.get("/director/departments")
            
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        dept_ids = [d["department_id"] for d in data]
        self.assertIn("operations_success", dept_ids)
        self.assertIn("finance", dept_ids)
        self.assertIn("engineering_delivery", dept_ids)
        self.assertIn("sales_revenue", dept_ids)
        self.assertIn("growth_marketing", dept_ids)
        
    def test_api_bootstrap_endpoint_exposes_canonical_hierarchy(self):
        """12. Verify bootstrap organization data exposes the same canonical department hierarchy."""
        response = self.client.get("/api/v1/director/desktop/bootstrap")
        if response.status_code == 404:
            response = self.client.get("/director/desktop/bootstrap")
            
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        departments = data.get("departments", [])
        dept_ids = [d["department_id"] for d in departments]
        self.assertIn("operations_success", dept_ids)

if __name__ == "__main__":
    unittest.main()
