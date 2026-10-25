"""
Single source of truth for recovery-action economics.

Previously three modules priced incentives differently (optimizer: best
offer = 5%, charged unconditionally; evaluate.py: hard-coded 2%;
evaluation.py: min(2%, cap)). The optimizer therefore optimised a different
objective from the one it was scored on. Everything now prices through here.

Convention (docs/FINANCIAL_SEMANTICS.md):
  A  = invoice amount of the failed payment (paise)
  c_a = modelled operational cost of executing action a (paise; incurred
        whenever the action is executed, regardless of outcome)
  D_a = incentive discount conceded if the customer pays after action a
        (paise; incurred ONLY on recovery)
  p_a, p_0 = P(recovery | x, a), P(recovery | x, NO_ACTION)

  E[net | a]                   = p_a * (A - D_a) - c_a
  expected incremental net (a) = E[net | a] - E[net | NO_ACTION]
                               = (p_a - p_0) * A - p_a * D_a - c_a

Budget feasibility uses the worst-case incentive exposure D_a (the discount
is committed when the offer is sent), while value uses its expectation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .action_registry import ACTION_REGISTRY
from .money import percent_of_paise
from .offers import OFFER_REGISTRY

NO_ACTION = "NO_ACTION"


def action_cost_paise(action: str, cost_multiplier: float = 1.0) -> int:
    if cost_multiplier < 0:
        raise ValueError("cost_multiplier must be non-negative")
    return int(round(ACTION_REGISTRY[action].monetary_cost_inr * 100 * cost_multiplier))


def auto_offer_for(action: str, amount_paise: int):
    """Highest-discount active offer that is eligible, within the action's cap,
    and does NOT require human approval. Returns (offer_id, discount_paise)."""
    spec = ACTION_REGISTRY[action]
    if not spec.is_incentive:
        return None, 0
    case = {"amount_inr": amount_paise / 100}
    best = None
    for o in OFFER_REGISTRY.values():
        if not o.active or o.offer_type == "none" or o.approval_required:
            continue
        if o.discount_pct > spec.max_incentive_pct or not o.eligibility_rule(case):
            continue
        if best is None or o.discount_pct > best.discount_pct:
            best = o
    if best is None:
        return None, 0
    discount = min(percent_of_paise(amount_paise, best.discount_pct),
                   int(round(best.max_discount_inr * 100)))
    return best.offer_id, int(discount)


def incentive_discount_paise(action: str, amount_paise: int) -> int:
    return auto_offer_for(action, amount_paise)[1]


def expected_incremental_net_paise(p_action: float, p_control: float, amount_paise: int,
                                   action: str) -> float:
    if action == NO_ACTION:
        return 0.0
    d = incentive_discount_paise(action, amount_paise)
    return (p_action - p_control) * amount_paise - p_action * d - action_cost_paise(action)


def add_economics(scored: pd.DataFrame, amount_paise: pd.Series, cost_multiplier: float = 1.0) -> pd.DataFrame:
    """Vectorised economics for a long (case, action) frame.

    scored must contain case_id, action, recovery_probability,
    baseline_probability. amount_paise is indexed by case_id.
    """
    df = scored.copy()
    df["amount_paise"] = df["case_id"].map(amount_paise).astype("int64")
    actions = df["action"].to_numpy()
    df["action_cost_paise"] = np.array([action_cost_paise(a, cost_multiplier) for a in actions], dtype="int64")
    df["incentive_discount_paise"] = np.array(
        [incentive_discount_paise(a, int(amt)) for a, amt in zip(actions, df["amount_paise"].to_numpy())],
        dtype="int64")
    df["requires_customer_contact"] = [ACTION_REGISTRY[a].requires_customer_contact for a in actions]
    p_a = df["recovery_probability"].to_numpy(dtype=float)
    p_0 = df["baseline_probability"].to_numpy(dtype=float)
    amt = df["amount_paise"].to_numpy(dtype=float)
    df["estimated_uplift"] = p_a - p_0
    df["expected_gross_incremental_paise"] = (p_a - p_0) * amt
    df["expected_incentive_cost_paise"] = p_a * df["incentive_discount_paise"].to_numpy(dtype=float)
    df["expected_incremental_value_paise"] = (
        df["expected_gross_incremental_paise"] - df["expected_incentive_cost_paise"]
        - df["action_cost_paise"].to_numpy(dtype=float))
    # Presentation-only rupee mirror (kept for backwards-compatible readers).
    df["expected_incremental_value_inr"] = df["expected_incremental_value_paise"] / 100.0
    return df


def realized_economics(action: str, amount_paise: int, recovered: bool) -> dict:
    """Realised accounting for one case under one action (used by simulation
    evaluation and mirrored by live reconciliation)."""
    executed = action != NO_ACTION
    cost = action_cost_paise(action) if executed else 0
    discount = incentive_discount_paise(action, amount_paise) if (executed and recovered) else 0
    gross = amount_paise if recovered else 0
    return {"gross_recovered_paise": gross, "incentive_cost_paise": discount,
            "action_cost_paise": cost, "cash_collected_paise": gross - discount,
            "net_value_paise": gross - discount - cost}
