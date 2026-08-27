import os
import json
import logging
import uuid
import tempfile
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from app.schemas.finance import (
    normalize_currency,
    ObjectiveBudget, MissionBudgetAllocation, BudgetReservation, 
    FinancialCommitment, ActualSpend, FinancialLedgerEntry, FinancialLedgerEntryType,
    BudgetStatus, safe_money
)
from app.engine.mission_engine import _cross_process_registry_lock

logger = logging.getLogger(__name__)

class BudgetInvariantError(ValueError): pass
class BudgetPersistenceError(RuntimeError): pass
class BudgetVersionConflict(RuntimeError): pass

class BudgetLedgerState(BaseModel):
    objectives: Dict[str, ObjectiveBudget] = Field(default_factory=dict)
    allocations: Dict[str, MissionBudgetAllocation] = Field(default_factory=dict)
    reservations: Dict[str, BudgetReservation] = Field(default_factory=dict)
    commitments: Dict[str, FinancialCommitment] = Field(default_factory=dict)
    spends: Dict[str, ActualSpend] = Field(default_factory=dict)
    ledger: List[FinancialLedgerEntry] = Field(default_factory=list)

class FinanceRepository:
    def __init__(self, storage_path: str = ".napstertec_finance.json"):
        self.storage_path = storage_path
        self._state = BudgetLedgerState()

    def _read_from_disk(self) -> BudgetLedgerState:
        if not os.path.exists(self.storage_path):
            return BudgetLedgerState()
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return BudgetLedgerState(**data)
        except Exception as e:
            logger.error(f"[FinanceRepository] Failed to read ledger: {e}")
            return BudgetLedgerState()

    def _persist(self, state: BudgetLedgerState) -> None:
        directory = os.path.dirname(os.path.abspath(self.storage_path)) or "."
        os.makedirs(directory, exist_ok=True)
        try:
            payload = state.model_dump(mode="json")
            fd, temp_path = tempfile.mkstemp(prefix=".finance-", suffix=".tmp", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.storage_path)
        except Exception as exc:
            if 'temp_path' in locals() and os.path.exists(temp_path):
                try: os.unlink(temp_path)
                except: pass
            raise BudgetPersistenceError(f"BUDGET_PERSISTENCE_WRITE_FAILED: {exc}") from exc

    def create_objective_budget(self, objective_id: str, currency: str, total_budget: float) -> ObjectiveBudget:
        with _cross_process_registry_lock(self.storage_path):
            state = self._read_from_disk()
            if objective_id in state.objectives:
                raise BudgetInvariantError("OBJECTIVE_BUDGET_ALREADY_EXISTS")
                
            budget = ObjectiveBudget(
                objective_id=objective_id,
                currency=currency,
                total_budget=safe_money(total_budget)
            )
            state.objectives[objective_id] = budget
            state.ledger.append(FinancialLedgerEntry(
                entry_id=f"led_{uuid.uuid4().hex[:8]}",
                objective_id=objective_id,
                entry_type=FinancialLedgerEntryType.BUDGET_CREATED,
                amount=budget.total_budget,
                currency=budget.currency,
                related_entity_id=objective_id,
                reason="Initial Objective Budget creation."
            ))
            self._persist(state)
            return budget

    def get_objective_budget(self, objective_id: str) -> Optional[ObjectiveBudget]:
        with _cross_process_registry_lock(self.storage_path):
            return self._read_from_disk().objectives.get(objective_id)

    def allocate_to_mission(self, objective_id: str, mission_id: str, amount: float) -> MissionBudgetAllocation:
        with _cross_process_registry_lock(self.storage_path):
            state = self._read_from_disk()
            budget = state.objectives.get(objective_id)
            if not budget:
                raise BudgetInvariantError("OBJECTIVE_BUDGET_NOT_FOUND")
                
            amount_dec = safe_money(amount)
            if budget.available_amount < amount_dec:
                raise BudgetInvariantError("OBJECTIVE_LIMIT_EXCEEDED")
                
            allocation_key = f"alloc_{objective_id}_{mission_id}"
            if allocation_key in state.allocations:
                alloc = state.allocations[allocation_key]
                alloc.allocated_amount += amount_dec
                alloc.version += 1
            else:
                alloc = MissionBudgetAllocation(
                    allocation_id=allocation_key,
                    objective_id=objective_id,
                    mission_id=mission_id,
                    currency=budget.currency,
                    allocated_amount=amount_dec
                )
                state.allocations[allocation_key] = alloc
                
            budget.allocated_amount += amount_dec
            budget.version += 1
            
            budget = ObjectiveBudget(**budget.model_dump())
            state.objectives[objective_id] = budget
            
            state.ledger.append(FinancialLedgerEntry(
                entry_id=f"led_{uuid.uuid4().hex[:8]}",
                objective_id=objective_id,
                mission_id=mission_id,
                entry_type=FinancialLedgerEntryType.MISSION_ALLOCATED,
                amount=amount_dec,
                currency=budget.currency,
                related_entity_id=alloc.allocation_id,
                reason=f"Allocated {amount_dec} {budget.currency} to Mission {mission_id}."
            ))
            self._persist(state)
            return alloc

    def get_mission_allocation(self, objective_id: str, mission_id: str) -> Optional[MissionBudgetAllocation]:
        with _cross_process_registry_lock(self.storage_path):
            key = f"alloc_{objective_id}_{mission_id}"
            return self._read_from_disk().allocations.get(key)

    def reserve(self, objective_id: str, mission_id: str, amount: float, purpose: str, action_fingerprint: str = None) -> BudgetReservation:
        with _cross_process_registry_lock(self.storage_path):
            state = self._read_from_disk()
            budget = state.objectives.get(objective_id)
            alloc_key = f"alloc_{objective_id}_{mission_id}"
            alloc = state.allocations.get(alloc_key)
            
            if not budget or not alloc:
                raise BudgetInvariantError("BUDGET_OR_ALLOCATION_NOT_FOUND")
                
            amount_dec = safe_money(amount)
            if alloc.available_amount < amount_dec:
                raise BudgetInvariantError("INSUFFICIENT_MISSION_BUDGET")
                
            res = BudgetReservation(
                reservation_id=f"res_{uuid.uuid4().hex[:8]}",
                objective_id=objective_id,
                mission_id=mission_id,
                amount=amount_dec,
                currency=budget.currency,
                purpose=purpose,
                action_fingerprint=action_fingerprint
            )
            
            alloc.reserved_amount += amount_dec
            alloc.version += 1
            budget.reserved_amount += amount_dec
            budget.version += 1
            
            state.reservations[res.reservation_id] = res
            state.allocations[alloc_key] = MissionBudgetAllocation(**alloc.model_dump())
            state.objectives[objective_id] = ObjectiveBudget(**budget.model_dump())
            
            state.ledger.append(FinancialLedgerEntry(
                entry_id=f"led_{uuid.uuid4().hex[:8]}",
                objective_id=objective_id,
                mission_id=mission_id,
                entry_type=FinancialLedgerEntryType.RESERVATION_CREATED,
                amount=amount_dec,
                currency=budget.currency,
                related_entity_id=res.reservation_id,
                reason=purpose
            ))
            self._persist(state)
            return res

    def release_reservation(self, reservation_id: str) -> bool:
        with _cross_process_registry_lock(self.storage_path):
            state = self._read_from_disk()
            res = state.reservations.get(reservation_id)
            if not res or res.status != "ACTIVE":
                raise BudgetInvariantError("RESERVATION_NOT_ACTIVE")
                
            budget = state.objectives.get(res.objective_id)
            alloc_key = f"alloc_{res.objective_id}_{res.mission_id}"
            alloc = state.allocations.get(alloc_key)
            
            res.status = "RELEASED"
            alloc.reserved_amount -= res.amount
            alloc.version += 1
            budget.reserved_amount -= res.amount
            budget.version += 1
            
            state.reservations[res.reservation_id] = res
            state.allocations[alloc_key] = MissionBudgetAllocation(**alloc.model_dump())
            state.objectives[budget.objective_id] = ObjectiveBudget(**budget.model_dump())
            
            state.ledger.append(FinancialLedgerEntry(
                entry_id=f"led_{uuid.uuid4().hex[:8]}",
                objective_id=res.objective_id,
                mission_id=res.mission_id,
                entry_type=FinancialLedgerEntryType.RESERVATION_RELEASED,
                amount=res.amount,
                currency=res.currency,
                related_entity_id=res.reservation_id,
                reason="Reservation released."
            ))
            self._persist(state)
            return True

    def convert_reservation_to_commitment(self, reservation_id: str, approval_id: Optional[str] = None) -> FinancialCommitment:
        with _cross_process_registry_lock(self.storage_path):
            state = self._read_from_disk()
            res = state.reservations.get(reservation_id)
            if not res or res.status != "ACTIVE":
                raise BudgetInvariantError("RESERVATION_NOT_ACTIVE")
                
            budget = state.objectives.get(res.objective_id)
            alloc_key = f"alloc_{res.objective_id}_{res.mission_id}"
            alloc = state.allocations.get(alloc_key)
            
            # Double entry transition: RESERVED down, COMMITTED up.
            alloc.reserved_amount -= res.amount
            alloc.committed_amount += res.amount
            alloc.version += 1
            
            budget.reserved_amount -= res.amount
            budget.committed_amount += res.amount
            budget.version += 1
            
            res.status = "CONVERTED"
            
            com = FinancialCommitment(
                commitment_id=f"com_{uuid.uuid4().hex[:8]}",
                objective_id=res.objective_id,
                mission_id=res.mission_id,
                reservation_id=res.reservation_id,
                amount=res.amount,
                currency=res.currency,
                purpose=res.purpose,
                approval_id=approval_id,
                action_fingerprint=res.action_fingerprint
            )
            
            state.commitments[com.commitment_id] = com
            state.reservations[res.reservation_id] = res
            state.allocations[alloc_key] = MissionBudgetAllocation(**alloc.model_dump())
            state.objectives[budget.objective_id] = ObjectiveBudget(**budget.model_dump())
            
            state.ledger.append(FinancialLedgerEntry(
                entry_id=f"led_{uuid.uuid4().hex[:8]}",
                objective_id=res.objective_id,
                mission_id=res.mission_id,
                entry_type=FinancialLedgerEntryType.RESERVATION_CONVERTED,
                amount=res.amount,
                currency=res.currency,
                related_entity_id=com.commitment_id,
                reason="Reservation converted to commitment."
            ))
            self._persist(state)
            return com

    def record_direct_commitment(self, objective_id: str, mission_id: str, amount: float, purpose: str, currency: str) -> FinancialCommitment:
        with _cross_process_registry_lock(self.storage_path):
            state = self._read_from_disk()
            budget = state.objectives.get(objective_id)
            alloc_key = f"alloc_{objective_id}_{mission_id}"
            alloc = state.allocations.get(alloc_key)
            
            if not budget or not alloc:
                raise BudgetInvariantError("BUDGET_OR_ALLOCATION_NOT_FOUND")
                
            currency = normalize_currency(currency)
            if budget.currency != currency:
                raise BudgetInvariantError("CURRENCY_MISMATCH")
                
            amount_dec = safe_money(amount)
            if alloc.available_amount < amount_dec:
                raise BudgetInvariantError("INSUFFICIENT_MISSION_BUDGET")
                
            com = FinancialCommitment(
                commitment_id=f"com_{uuid.uuid4().hex[:8]}",
                objective_id=objective_id,
                mission_id=mission_id,
                amount=amount_dec,
                currency=currency,
                purpose=purpose
            )
            
            alloc.committed_amount += amount_dec
            alloc.version += 1
            budget.committed_amount += amount_dec
            budget.version += 1
            
            state.commitments[com.commitment_id] = com
            state.allocations[alloc_key] = MissionBudgetAllocation(**alloc.model_dump())
            state.objectives[objective_id] = ObjectiveBudget(**budget.model_dump())
            
            state.ledger.append(FinancialLedgerEntry(
                entry_id=f"led_{uuid.uuid4().hex[:8]}",
                objective_id=objective_id,
                mission_id=mission_id,
                entry_type=FinancialLedgerEntryType.COMMITMENT_CREATED,
                amount=amount_dec,
                currency=currency,
                related_entity_id=com.commitment_id,
                reason=purpose
            ))
            self._persist(state)
            return com

    def record_spend(self, commitment_id: str, actual_amount: float, description: str) -> ActualSpend:
        with _cross_process_registry_lock(self.storage_path):
            state = self._read_from_disk()
            com = state.commitments.get(commitment_id)
            if not com:
                raise BudgetInvariantError("COMMITMENT_NOT_FOUND")
                
            if com.status == "SPENT":
                raise BudgetInvariantError("COMMITMENT_ALREADY_SPENT")
                
            budget = state.objectives.get(com.objective_id)
            alloc_key = f"alloc_{com.objective_id}_{com.mission_id}"
            alloc = state.allocations.get(alloc_key)
            
            actual_dec = safe_money(actual_amount)
            
            # Policy: Partial spend returns unused commitment to availability. 
            if actual_dec > com.amount:
                raise BudgetInvariantError("SPEND_EXCEEDS_COMMITMENT")
                
            alloc.committed_amount -= com.amount
            alloc.spent_amount += actual_dec
            alloc.version += 1
            
            budget.committed_amount -= com.amount
            budget.spent_amount += actual_dec
            budget.version += 1
            
            com.status = "SPENT"
            
            spend = ActualSpend(
                spend_id=f"spn_{uuid.uuid4().hex[:8]}",
                objective_id=com.objective_id,
                mission_id=com.mission_id,
                commitment_id=com.commitment_id,
                amount=actual_dec,
                currency=com.currency,
                description=description
            )
            
            state.spends[spend.spend_id] = spend
            state.commitments[com.commitment_id] = com
            state.allocations[alloc_key] = MissionBudgetAllocation(**alloc.model_dump())
            state.objectives[budget.objective_id] = ObjectiveBudget(**budget.model_dump())
            
            state.ledger.append(FinancialLedgerEntry(
                entry_id=f"led_{uuid.uuid4().hex[:8]}",
                objective_id=com.objective_id,
                mission_id=com.mission_id,
                entry_type=FinancialLedgerEntryType.SPEND_RECORDED,
                amount=actual_dec,
                currency=com.currency,
                related_entity_id=spend.spend_id,
                reason=description
            ))
            self._persist(state)
            return spend

finance_repository = FinanceRepository()