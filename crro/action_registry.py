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
    "NO_ACTION": ActionSpec("NO_ACTION", False, 0, 0, 999, None),
    "RETRY_NOW": ActionSpec("RETRY_NOW", False, 2, 0, 3, 100000),
    "RETRY_LATER": ActionSpec("RETRY_LATER", False, 2, 30, 3, 100000),
    "PAYMENT_LINK": ActionSpec("PAYMENT_LINK", True, 5, 15, 2, 50000),
    "ALTERNATIVE_PAYMENT_METHOD": ActionSpec("ALTERNATIVE_PAYMENT_METHOD", True, 8, 15, 2, 50000),
    "CUSTOMER_NUDGE": ActionSpec("CUSTOMER_NUDGE", True, 3, 60, 2, 50000),
    "SMALL_INCENTIVE": ActionSpec("SMALL_INCENTIVE", True, 10, 30, 1, 20000, True, 0.05),
}
CONTROLLABLE_ACTIONS = [x for x in ACTION_REGISTRY if x != "NO_ACTION"]
