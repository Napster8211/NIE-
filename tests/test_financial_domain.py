import unittest
import os
import tempfile
from decimal import Decimal
from app.schemas.finance import (
    ObjectiveBudget, MissionBudgetAllocation, BudgetReservation,
    FinancialCommitment, ActualSpend, BudgetStatus, safe_money, normalize_currency
)
from app.repositories.finance_repository import FinanceRepository, BudgetInvariantError

class TestBudgetLedger(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_path = os.path.join(self.temp_dir.name, "finance.json")
        self.repo = FinanceRepository(storage_path=self.storage_path)
        
    def tearDown(self):
        self.temp_dir.cleanup()

    # --- MONEY MODEL TESTS ---
    def test_money_model_rejects_float_errors(self):
        with self.assertRaises(ValueError):
            safe_money(-100.50)
        with self.assertRaises(ValueError):
            safe_money("NaN")
        with self.assertRaises(ValueError):
            safe_money("Infinity")
        self.assertEqual(safe_money(100.129), Decimal("100.12"))
        
    def test_currency_normalization(self):
        self.assertEqual(normalize_currency("  usd  "), "USD")

    # --- OBJECTIVE BUDGET TESTS ---
    def test_create_objective_budget(self):
        budget = self.repo.create_objective_budget("obj_1", "ghs", 5000)
        self.assertEqual(budget.currency, "GHS")
        self.assertEqual(budget.total_budget, Decimal("5000.00"))
        self.assertEqual(budget.available_amount, Decimal("5000.00"))
        self.assertEqual(budget.status, BudgetStatus.AVAILABLE)
        
    def test_duplicate_objective_budget_rejected(self):
        self.repo.create_objective_budget("obj_1", "ghs", 5000)
        with self.assertRaisesRegex(BudgetInvariantError, "OBJECTIVE_BUDGET_ALREADY_EXISTS"):
            self.repo.create_objective_budget("obj_1", "ghs", 5000)

    # --- MISSION ALLOCATION TESTS ---
    def test_allocate_mission_budget(self):
        self.repo.create_objective_budget("obj_1", "GHS", 5000)
        alloc = self.repo.allocate_to_mission("obj_1", "mis_1", 2000)
        
        self.assertEqual(alloc.allocated_amount, Decimal("2000.00"))
        self.assertEqual(alloc.available_amount, Decimal("2000.00"))
        
        budget = self.repo.get_objective_budget("obj_1")
        self.assertEqual(budget.allocated_amount, Decimal("2000.00"))
        self.assertEqual(budget.available_amount, Decimal("3000.00"))

    def test_allocate_beyond_objective_limit_rejected(self):
        self.repo.create_objective_budget("obj_1", "GHS", 5000)
        self.repo.allocate_to_mission("obj_1", "mis_1", 4000)
        with self.assertRaisesRegex(BudgetInvariantError, "OBJECTIVE_LIMIT_EXCEEDED"):
            self.repo.allocate_to_mission("obj_1", "mis_2", 2000)

    # --- RESERVATION TESTS ---
    def test_reserve_within_mission_budget(self):
        self.repo.create_objective_budget("obj_1", "GHS", 5000)
        self.repo.allocate_to_mission("obj_1", "mis_1", 2000)
        
        res = self.repo.reserve("obj_1", "mis_1", 500, "Campaign A")
        self.assertEqual(res.amount, Decimal("500.00"))
        
        alloc = self.repo.get_mission_allocation("obj_1", "mis_1")
        self.assertEqual(alloc.reserved_amount, Decimal("500.00"))
        self.assertEqual(alloc.available_amount, Decimal("1500.00"))

    def test_reserve_beyond_mission_budget_rejected(self):
        self.repo.create_objective_budget("obj_1", "GHS", 5000)
        self.repo.allocate_to_mission("obj_1", "mis_1", 2000)
        
        with self.assertRaisesRegex(BudgetInvariantError, "INSUFFICIENT_MISSION_BUDGET"):
            self.repo.reserve("obj_1", "mis_1", 2500, "Campaign A")

    def test_release_reservation_restores_availability(self):
        self.repo.create_objective_budget("obj_1", "GHS", 5000)
        self.repo.allocate_to_mission("obj_1", "mis_1", 2000)
        res = self.repo.reserve("obj_1", "mis_1", 500, "Campaign A")
        
        self.assertTrue(self.repo.release_reservation(res.reservation_id))
        
        alloc = self.repo.get_mission_allocation("obj_1", "mis_1")
        self.assertEqual(alloc.reserved_amount, Decimal("0.00"))
        self.assertEqual(alloc.available_amount, Decimal("2000.00"))
        
        with self.assertRaisesRegex(BudgetInvariantError, "RESERVATION_NOT_ACTIVE"):
            self.repo.release_reservation(res.reservation_id)

    # --- COMMITMENT TESTS ---
    def test_convert_reservation_to_commitment(self):
        self.repo.create_objective_budget("obj_1", "GHS", 5000)
        self.repo.allocate_to_mission("obj_1", "mis_1", 2000)
        res = self.repo.reserve("obj_1", "mis_1", 500, "Campaign A")
        
        com = self.repo.convert_reservation_to_commitment(res.reservation_id)
        self.assertEqual(com.amount, Decimal("500.00"))
        
        alloc = self.repo.get_mission_allocation("obj_1", "mis_1")
        # Converting does not double subtract
        self.assertEqual(alloc.reserved_amount, Decimal("0.00"))
        self.assertEqual(alloc.committed_amount, Decimal("500.00"))
        self.assertEqual(alloc.available_amount, Decimal("1500.00"))
        
    def test_direct_commitment_currency_mismatch(self):
        self.repo.create_objective_budget("obj_1", "GHS", 5000)
        self.repo.allocate_to_mission("obj_1", "mis_1", 2000)
        with self.assertRaisesRegex(BudgetInvariantError, "CURRENCY_MISMATCH"):
            self.repo.record_direct_commitment("obj_1", "mis_1", 500, "Ads", "USD")

    # --- SPEND TESTS ---
    def test_commitment_to_partial_spend(self):
        self.repo.create_objective_budget("obj_1", "GHS", 5000)
        self.repo.allocate_to_mission("obj_1", "mis_1", 2000)
        com = self.repo.record_direct_commitment("obj_1", "mis_1", 500, "Ads", "GHS")
        
        spend = self.repo.record_spend(com.commitment_id, 450, "Final invoice")
        self.assertEqual(spend.amount, Decimal("450.00"))
        
        alloc = self.repo.get_mission_allocation("obj_1", "mis_1")
        self.assertEqual(alloc.committed_amount, Decimal("0.00"))
        self.assertEqual(alloc.spent_amount, Decimal("450.00"))
        # Unused 50 goes back to availability
        self.assertEqual(alloc.available_amount, Decimal("1550.00"))

    def test_spend_above_commitment_rejected(self):
        self.repo.create_objective_budget("obj_1", "GHS", 5000)
        self.repo.allocate_to_mission("obj_1", "mis_1", 2000)
        com = self.repo.record_direct_commitment("obj_1", "mis_1", 500, "Ads", "GHS")
        
        with self.assertRaisesRegex(BudgetInvariantError, "SPEND_EXCEEDS_COMMITMENT"):
            self.repo.record_spend(com.commitment_id, 550, "Final invoice")

if __name__ == "__main__":
    unittest.main()