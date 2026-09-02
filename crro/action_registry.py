"""Merchant-approved action vocabulary and immutable execution metadata."""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ActionSpec:
    name: str
    requires_customer_contact: bool
    monetary_cost_inr: float
    min_cooldown_minutes: int
    max_attempts: int
    requires_approval_above_amount: Optional[float]
    is_incentive: bool = False
    max_incentive_pct: float = 0.0


ACTION_REGISTRY = {
    "NO_ACTION": ActionSpec("NO_ACTION", False, 0.0, 0, 999, None),
    "RETRY_NOW": ActionSpec("RETRY_NOW", False, 2.0, 0, 3, 100000),
    "RETRY_LATER": ActionSpec("RETRY_LATER", False, 2.0, 30, 3, 100000),
    "PAYMENT_LINK": ActionSpec("PAYMENT_LINK", True, 5.0, 15, 2, 50000),
    "ALTERNATIVE_PAYMENT_METHOD": ActionSpec("ALTERNATIVE_PAYMENT_METHOD", True, 8.0, 15, 2, 50000),
    "CUSTOMER_NUDGE": ActionSpec("CUSTOMER_NUDGE", True, 3.0, 60, 2, 50000),
    "SMALL_INCENTIVE": ActionSpec("SMALL_INCENTIVE", True, 10.0, 30, 1, 20000, True, 0.05),
}

CONTROLLABLE_ACTIONS = [name for name in ACTION_REGISTRY if name != "NO_ACTION"]
REASON_CODES = [
    "POLICY_BLOCKED", "CUSTOMER_OPTED_OUT", "MAX_ATTEMPTS", "NO_POSITIVE_UPLIFT",
    "EXECUTION_TIMEOUT", "PROVIDER_ERROR", "APPROVAL_PENDING", "MISSING_DATA",
]


def incentive_cost_inr(amount_inr: float, action: str, approved_pct: float = 0.02) -> float:
    """Return the configured merchant offer cost, never an LLM-invented amount."""
    spec = ACTION_REGISTRY[action]
    if not spec.is_incentive:
        return 0.0
    pct = min(max(float(approved_pct), 0.0), spec.max_incentive_pct)
    return float(amount_inr) * pct
