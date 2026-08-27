from pydantic import BaseModel, Field, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from enum import Enum

class BudgetStatus(str, Enum):
    UNSET = "UNSET"
    AVAILABLE = "AVAILABLE"
    WARNING = "WARNING"
    FULLY_ALLOCATED = "FULLY_ALLOCATED"
    EXHAUSTED = "EXHAUSTED"
    OVER_BUDGET = "OVER_BUDGET"
    BLOCKED = "BLOCKED"

class FinancialLedgerEntryType(str, Enum):
    BUDGET_CREATED = "BUDGET_CREATED"
    MISSION_ALLOCATED = "MISSION_ALLOCATED"
    RESERVATION_CREATED = "RESERVATION_CREATED"
    RESERVATION_RELEASED = "RESERVATION_RELEASED"
    RESERVATION_CONVERTED = "RESERVATION_CONVERTED"
    COMMITMENT_CREATED = "COMMITMENT_CREATED"
    SPEND_RECORDED = "SPEND_RECORDED"
    ADJUSTMENT = "ADJUSTMENT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"

def normalize_currency(currency: str) -> str:
    return str(currency).strip().upper()

def safe_money(amount: Any) -> Decimal:
    try:
        val = Decimal(str(amount))
        if val.is_nan() or val.is_infinite() or val < 0:
            raise ValueError(f"Invalid monetary amount: {amount}")
        return val.quantize(Decimal('.01'), rounding=ROUND_DOWN)
    except Exception:
        raise ValueError(f"Invalid monetary amount: {amount}")

class ObjectiveBudget(BaseModel):
    objective_id: str = Field(...)
    currency: str = Field(...)
    total_budget: Decimal = Field(default=Decimal("0.00"))
    allocated_amount: Decimal = Field(default=Decimal("0.00"))
    reserved_amount: Decimal = Field(default=Decimal("0.00"))
    committed_amount: Decimal = Field(default=Decimal("0.00"))
    spent_amount: Decimal = Field(default=Decimal("0.00"))
    available_amount: Decimal = Field(default=Decimal("0.00"))
    status: BudgetStatus = Field(default=BudgetStatus.UNSET)
    version: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    @model_validator(mode="after")
    def enforce_invariants(self) -> 'ObjectiveBudget':
        self.currency = normalize_currency(self.currency)
        self.total_budget = safe_money(self.total_budget)
        self.allocated_amount = safe_money(self.allocated_amount)
        self.reserved_amount = safe_money(self.reserved_amount)
        self.committed_amount = safe_money(self.committed_amount)
        self.spent_amount = safe_money(self.spent_amount)
        
        if self.allocated_amount > self.total_budget:
            raise ValueError("OBJECTIVE_BUDGET_EXCEEDED")
            
        derived_available = self.total_budget - self.allocated_amount - self.reserved_amount - self.committed_amount - self.spent_amount
        if derived_available < 0:
            self.status = BudgetStatus.OVER_BUDGET
            self.available_amount = Decimal("0.00")
        else:
            self.available_amount = safe_money(derived_available)
            if self.available_amount == 0 and self.total_budget > 0:
                self.status = BudgetStatus.EXHAUSTED
            elif self.total_budget > 0:
                self.status = BudgetStatus.AVAILABLE
            else:
                self.status = BudgetStatus.UNSET
                
        return self

class MissionBudgetAllocation(BaseModel):
    allocation_id: str = Field(...)
    objective_id: str = Field(...)
    mission_id: str = Field(...)
    currency: str = Field(...)
    allocated_amount: Decimal = Field(default=Decimal("0.00"))
    reserved_amount: Decimal = Field(default=Decimal("0.00"))
    committed_amount: Decimal = Field(default=Decimal("0.00"))
    spent_amount: Decimal = Field(default=Decimal("0.00"))
    available_amount: Decimal = Field(default=Decimal("0.00"))
    status: BudgetStatus = Field(default=BudgetStatus.AVAILABLE)
    version: int = Field(default=1, ge=1)
    
    @model_validator(mode="after")
    def enforce_invariants(self) -> 'MissionBudgetAllocation':
        self.currency = normalize_currency(self.currency)
        self.allocated_amount = safe_money(self.allocated_amount)
        self.reserved_amount = safe_money(self.reserved_amount)
        self.committed_amount = safe_money(self.committed_amount)
        self.spent_amount = safe_money(self.spent_amount)
        
        derived_available = self.allocated_amount - self.reserved_amount - self.committed_amount - self.spent_amount
        if derived_available < 0:
            raise ValueError("MISSION_BUDGET_OVERDRAWN")
            
        self.available_amount = safe_money(derived_available)
        if self.available_amount == 0 and self.allocated_amount > 0:
            self.status = BudgetStatus.EXHAUSTED
        elif self.allocated_amount > 0:
            self.status = BudgetStatus.AVAILABLE
            
        return self

class BudgetReservation(BaseModel):
    reservation_id: str = Field(...)
    objective_id: str = Field(...)
    mission_id: str = Field(...)
    amount: Decimal = Field(...)
    currency: str = Field(...)
    purpose: str = Field(...)
    action_fingerprint: Optional[str] = None
    status: str = Field(default="ACTIVE")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    @model_validator(mode="after")
    def enforce_money(self) -> 'BudgetReservation':
        self.currency = normalize_currency(self.currency)
        self.amount = safe_money(self.amount)
        if self.amount <= 0:
            raise ValueError("RESERVATION_AMOUNT_MUST_BE_POSITIVE")
        return self

class FinancialCommitment(BaseModel):
    commitment_id: str = Field(...)
    objective_id: str = Field(...)
    mission_id: str = Field(...)
    reservation_id: Optional[str] = None
    amount: Decimal = Field(...)
    currency: str = Field(...)
    purpose: str = Field(...)
    approval_id: Optional[str] = None
    action_fingerprint: Optional[str] = None
    status: str = Field(default="COMMITTED")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @model_validator(mode="after")
    def enforce_money(self) -> 'FinancialCommitment':
        self.currency = normalize_currency(self.currency)
        self.amount = safe_money(self.amount)
        if self.amount <= 0:
            raise ValueError("COMMITMENT_AMOUNT_MUST_BE_POSITIVE")
        return self

class ActualSpend(BaseModel):
    spend_id: str = Field(...)
    objective_id: str = Field(...)
    mission_id: str = Field(...)
    commitment_id: Optional[str] = None
    amount: Decimal = Field(...)
    currency: str = Field(...)
    vendor: Optional[str] = None
    operation_id: Optional[str] = None
    description: str = Field(...)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @model_validator(mode="after")
    def enforce_money(self) -> 'ActualSpend':
        self.currency = normalize_currency(self.currency)
        self.amount = safe_money(self.amount)
        if self.amount <= 0:
            raise ValueError("SPEND_AMOUNT_MUST_BE_POSITIVE")
        return self

class FinancialLedgerEntry(BaseModel):
    entry_id: str = Field(...)
    objective_id: str = Field(...)
    mission_id: Optional[str] = None
    entry_type: FinancialLedgerEntryType = Field(...)
    amount: Decimal = Field(...)
    currency: str = Field(...)
    related_entity_id: str = Field(...)
    reason: str = Field(...)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class FinancialGovernanceSnapshot(BaseModel):
    snapshot_id: str = Field(...)
    objective_id: str = Field(...)
    mission_id: Optional[str] = None
    currency: str = Field(...)
    total_budget: Decimal = Field(default=Decimal("0.00"))
    allocated_amount: Decimal = Field(default=Decimal("0.00"))
    reserved_amount: Decimal = Field(default=Decimal("0.00"))
    committed_amount: Decimal = Field(default=Decimal("0.00"))
    spent_amount: Decimal = Field(default=Decimal("0.00"))
    available_amount: Decimal = Field(default=Decimal("0.00"))
    utilization_percent: float = Field(default=0.0)
    warning_threshold_percent: float = Field(default=80.0)
    budget_status: BudgetStatus = Field(...)
    ledger_version: int = Field(default=1)
    captured_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    data_quality: str = Field(default="VERIFIED")

class CFOFinancialAssessment(BaseModel):
    assessment_id: str = Field(...)
    objective_id: str = Field(...)
    snapshot_id: str = Field(...)
    financial_status: str = Field(...)
    risk_level: str = Field(...)
    utilization_percent: float = Field(...)
    available_amount: str = Field(...)
    currency: str = Field(...)
    findings: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    requires_director_attention: bool = Field(default=False)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())