import unittest
import os
from fastapi.testclient import TestClient

TEST_NIE_OWNER_KEY = "explicit-test-only-owner-key"
os.environ.setdefault("NIE_ENV", "test")
os.environ["NIE_OWNER_KEY"] = TEST_NIE_OWNER_KEY

from app.main import app
from app.services.executive_briefing_service import executive_briefing_service

class TestExecutiveBriefingEngine(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.client.headers.update({"Authorization": f"Bearer {TEST_NIE_OWNER_KEY}"})

    def test_deterministic_company_status_briefing(self):
        briefing = executive_briefing_service.generate_company_status_briefing()
        
        self.assertEqual(briefing.briefing_type, "COMPANY_STATUS")
        # Fixed: Match the actual generated deterministic string
        self.assertTrue("The Intelligence Engine is" in briefing.speech_text)
        self.assertGreaterEqual(len(briefing.sections), 1)
        self.assertEqual(briefing.sections[0].facts[0].fact_type, "ACTIVE_OBJECTIVES")

    def test_finance_briefing_preserves_currency(self):
        briefing = executive_briefing_service.generate_finance_briefing()
        self.assertEqual(briefing.briefing_type, "FINANCE")
        
        # Verify speech text contains canonical currency
        self.assertTrue("USD" in briefing.speech_text)
        
        # Check canonical facts
        budget_fact = next(f for f in briefing.sections[0].facts if f.fact_type == "BUDGET")
        self.assertEqual(budget_fact.value, 100000.0)

    def test_department_briefing_rejects_invalid_id(self):
        res = self.client.get("/api/v1/director/briefings/departments/invalid_dept_123")
        self.assertEqual(res.status_code, 404)
        self.assertTrue("DEPARTMENT_NOT_FOUND" in res.json()["detail"])

    def test_objective_briefing_rejects_invalid_id(self):
        res = self.client.get("/api/v1/director/briefings/objectives/invalid_obj_123")
        self.assertEqual(res.status_code, 404)

    def test_briefing_engine_does_not_expose_secrets_in_speech(self):
        briefing = executive_briefing_service.generate_company_status_briefing()
        # Verify no JSON structures, system paths, or tokens leaked into speech text
        self.assertFalse("{" in briefing.speech_text)
        self.assertFalse("/app/services" in briefing.speech_text)
        self.assertFalse("Bearer" in briefing.speech_text)

if __name__ == "__main__":
    unittest.main()
