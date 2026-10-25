"""
Merchant-Approved Offer Registry (Required Changes, Section 9)
-------------------------------------------------------------------
The optimizer must evaluate ACTUAL candidate offers rather than a
hard-coded incentive percentage. The LLM must never invent an offer;
it may only reference offer_ids from this table.
"""

from dataclasses import dataclass
from typing import Optional, Callable


@dataclass(frozen=True)
class Offer:
    offer_id: str
    offer_type: str            # e.g. "percent_discount"
    discount_pct: float
    max_discount_inr: float
    eligibility_rule: Callable[[dict], bool]
    approval_required: bool
    active: bool


def _eligible_default(case: dict) -> bool:
    return case.get("amount_inr", 0) < 20000


OFFER_REGISTRY = {
    "NO_INCENTIVE": Offer(
        offer_id="NO_INCENTIVE", offer_type="none",
        discount_pct=0.0, max_discount_inr=0.0,
        eligibility_rule=lambda c: True,
        approval_required=False, active=True,
    ),
    "OFFER_2PCT": Offer(
        offer_id="OFFER_2PCT", offer_type="percent_discount",
        discount_pct=0.02, max_discount_inr=300.0,
        eligibility_rule=_eligible_default,
        approval_required=False, active=True,
    ),
    "OFFER_5PCT": Offer(
        offer_id="OFFER_5PCT", offer_type="percent_discount",
        discount_pct=0.05, max_discount_inr=750.0,
        eligibility_rule=_eligible_default,
        approval_required=True,   # matches action_registry SMALL_INCENTIVE cap
        active=True,
    ),
}

ACTIVE_INCENTIVE_OFFERS = [o for o in OFFER_REGISTRY.values()
                           if o.active and o.offer_type != "none"]


def incentive_cost_for_offer(offer: Offer, amount_inr: float) -> float:
    return min(offer.discount_pct * amount_inr, offer.max_discount_inr)


def best_eligible_offer(case: dict, max_pct: float):
    """
    Returns the highest-discount ACTIVE, ELIGIBLE offer that does not
    exceed the action registry's max_incentive_pct cap for SMALL_INCENTIVE.
    Never invents a new offer -- only selects from OFFER_REGISTRY.
    """
    candidates = [o for o in ACTIVE_INCENTIVE_OFFERS
                  if o.eligibility_rule(case) and o.discount_pct <= max_pct]
    if not candidates:
        return OFFER_REGISTRY["NO_INCENTIVE"]
    return max(candidates, key=lambda o: o.discount_pct)
