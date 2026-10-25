"""
Action Registry (Spec Section 9-10)
------------------------------------
The action registry is CONFIGURATION, not LLM output. The AI must never
invent a new action; it may only select from this pre-approved vocabulary.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ActionSpec:
    name: str
    requires_customer_contact: bool
    monetary_cost_inr: float          # fixed cost per execution (processing/contact)
    min_cooldown_minutes: int
    max_attempts: int
    requires_approval_above_amount: Optional[float]
    is_incentive: bool = False
    max_incentive_pct: float = 0.0    # cap on incentive as % of amount, if is_incentive


ACTION_REGISTRY = {
    "NO_ACTION": ActionSpec(
        name="NO_ACTION",
        requires_customer_contact=False,
        monetary_cost_inr=0,
        min_cooldown_minutes=0,
        max_attempts=999,
        requires_approval_above_amount=None,
    ),
    "RETRY_NOW": ActionSpec(
        name="RETRY_NOW",
        requires_customer_contact=False,
        monetary_cost_inr=2,          # gateway processing cost
        min_cooldown_minutes=0,
        max_attempts=3,
        requires_approval_above_amount=100000,
    ),
    "RETRY_LATER": ActionSpec(
        name="RETRY_LATER",
        requires_customer_contact=False,
        monetary_cost_inr=2,
        min_cooldown_minutes=30,
        max_attempts=3,
        requires_approval_above_amount=100000,
    ),
    "PAYMENT_LINK": ActionSpec(
        name="PAYMENT_LINK",
        requires_customer_contact=True,
        monetary_cost_inr=5,
        min_cooldown_minutes=15,
        max_attempts=2,
        requires_approval_above_amount=50000,
    ),
    "ALTERNATIVE_PAYMENT_METHOD": ActionSpec(
        name="ALTERNATIVE_PAYMENT_METHOD",
        requires_customer_contact=True,
        monetary_cost_inr=8,
        min_cooldown_minutes=15,
        max_attempts=2,
        requires_approval_above_amount=50000,
    ),
    "CUSTOMER_NUDGE": ActionSpec(
        name="CUSTOMER_NUDGE",
        requires_customer_contact=True,
        monetary_cost_inr=3,
        min_cooldown_minutes=60,
        max_attempts=2,
        requires_approval_above_amount=50000,
    ),
    "SMALL_INCENTIVE": ActionSpec(
        name="SMALL_INCENTIVE",
        requires_customer_contact=True,
        monetary_cost_inr=10,
        min_cooldown_minutes=30,
        max_attempts=1,
        requires_approval_above_amount=20000,
        is_incentive=True,
        max_incentive_pct=0.05,   # up to 5% of amount, merchant-approved cap
    ),
}

CONTROLLABLE_ACTIONS = [a for a in ACTION_REGISTRY if a != "NO_ACTION"]

REASON_CODES = [
    "POLICY_BLOCKED",
    "CUSTOMER_OPTED_OUT",
    "MAX_ATTEMPTS",
    "NO_POSITIVE_UPLIFT",
    "EXECUTION_TIMEOUT",
    "PROVIDER_ERROR",
    "APPROVAL_PENDING",
    "MISSING_DATA",
]
