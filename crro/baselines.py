"""
Baseline policies. All deployable baselines see the SAME feasible-action sets
(eligibility + opt-out guardrails) and the SAME budgets as CRRO, and are priced
with crro/economics.py, so differences are attributable to the decision rule.

  CONTROL                      no action for anyone (real control outcome)
  FIXED_RETRY                  RETRY_NOW whenever feasible
  RULE_BASED                   hand-written failure-reason rules
  HISTORICAL_BEST_ACTION       action with best aggregate train recovery rate
  RAW_PROBABILITY (constrained) greedy on p_a*(A-D_a)-c_a: ignores the counterfactual
  UPLIFT_GREEDY (constrained)  greedy on expected incremental value (CRRO model, heuristic allocator)
  CRRO_EXACT (constrained)     exact MILP on expected incremental value  [in experiments.py]
  UPLIFT_UNCONSTRAINED         diagnostic only: per-case argmax, budgets ignored
  ORACLE_CONSTRAINED           simulator-only ceiling: exact allocation on TRUE values, same budgets
  ORACLE_UNCONSTRAINED         simulator-only ceiling, budgets ignored
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .action_registry import ACTION_REGISTRY
from .economics import action_cost_paise, incentive_discount_paise
from .optimizer import Budgets, exact_allocate, greedy_allocate
from .oracle import true_p_recovery


def control_policy(cases_df):
    return pd.DataFrame({"case_id": cases_df["case_id"].to_numpy(), "action": "NO_ACTION"})


def _fill(cases_df, preferred, budgets: Budgets, feasible_col="feasible_actions"):
    """Process cases in arrival order; take each case's preferred feasible action
    if budgets allow, else NO_ACTION. preferred(row) -> ordered list of actions."""
    contacts, inc, cust, act_n, rows = 0, 0, {}, {}, []
    for r in cases_df.itertuples(index=False):
        feas = set(getattr(r, feasible_col))
        chosen = "NO_ACTION"
        for a in preferred(r):
            if a == "NO_ACTION" or a not in feas:
                continue
            spec = ACTION_REGISTRY[a]
            amt = int(round(r.amount_inr * 100))
            d = incentive_discount_paise(a, amt)
            if spec.requires_customer_contact and (contacts >= budgets.daily_contacts or
                                                   cust.get(r.customer_id, 0) >= budgets.per_customer_daily_contact_cap):
                continue
            if inc + d > budgets.incentive_paise:
                continue
            cap = budgets.per_action_daily_cap.get(a)
            if cap is not None and act_n.get(a, 0) >= cap:
                continue
            chosen = a
            inc += d
            act_n[a] = act_n.get(a, 0) + 1
            if spec.requires_customer_contact:
                contacts += 1
                cust[r.customer_id] = cust.get(r.customer_id, 0) + 1
            break
        rows.append({"case_id": r.case_id, "action": chosen})
    return pd.DataFrame(rows)


def fixed_retry_policy(cases_df, budgets, feasible_col="feasible_actions"):
    return _fill(cases_df, lambda r: ["RETRY_NOW"], budgets, feasible_col)


_RULES = {"card_expired": ["ALTERNATIVE_PAYMENT_METHOD", "PAYMENT_LINK"], "risk_block": ["PAYMENT_LINK"],
          "insufficient_funds": ["RETRY_LATER"], "network_error": ["RETRY_NOW"], "otp_timeout": ["RETRY_NOW"],
          "bank_decline": ["RETRY_LATER", "PAYMENT_LINK"]}


def rule_based_policy(cases_df, budgets, feasible_col="feasible_actions"):
    return _fill(cases_df, lambda r: _RULES.get(r.failure_reason, ["RETRY_NOW"]), budgets, feasible_col)


def historical_best_action_policy(cases_df, train_df, budgets, feasible_col="feasible_actions"):
    rates = (train_df[train_df["assigned_action"] != "NO_ACTION"].groupby("assigned_action")["outcome_recovered"]
             .mean().sort_values(ascending=False))
    ranked = [str(a) for a in rates.index]
    return _fill(cases_df, lambda r: ranked, budgets, feasible_col), {k: round(float(v), 4) for k, v in rates.items()}


def raw_probability_policy(scored_df, budgets):
    """Budget-constrained greedy on raw expected collection p_a*(A-D_a)-c_a,
    i.e. ignoring what would have happened without the action."""
    s = scored_df.copy()
    s["expected_incremental_value_paise"] = (s["recovery_probability"] * (s["amount_paise"] - s["incentive_discount_paise"])
                                             - s["action_cost_paise"])
    alloc, usage = greedy_allocate(s, budgets)
    return alloc[["case_id", "action"]], usage


def uplift_greedy_policy(scored_df, budgets):
    alloc, usage = greedy_allocate(scored_df, budgets)
    return alloc[["case_id", "action"]], usage


def uplift_unconstrained_policy(scored_df):
    if len(scored_df) == 0:
        return pd.DataFrame(columns=["case_id", "action"])
    best = scored_df.sort_values(["expected_incremental_value_paise", "action"], ascending=[False, True]) \
        .drop_duplicates("case_id")
    best = best.assign(action=np.where(best["expected_incremental_value_paise"] > 0, best["action"], "NO_ACTION"))
    return best[["case_id", "action"]].reset_index(drop=True)


def oracle_scored(cases_df, feasible_col="feasible_actions", shift=True):
    """TRUE expected incremental value for every feasible (case, action). Simulator only."""
    df = cases_df.reset_index(drop=True)
    p0 = true_p_recovery(df, np.full(len(df), "NO_ACTION", dtype=object), shift=shift)
    amt = np.round(df["amount_inr"].to_numpy(dtype=float) * 100).astype("int64")
    rows = []
    for a in [x for x in ACTION_REGISTRY if x != "NO_ACTION"]:
        mask = np.fromiter((a in f for f in df[feasible_col]), dtype=bool, count=len(df))
        if not mask.any():
            continue
        pa = true_p_recovery(df[mask], np.full(int(mask.sum()), a, dtype=object), shift=shift)
        d = np.array([incentive_discount_paise(a, int(x)) for x in amt[mask]], dtype="int64")
        c = action_cost_paise(a)
        rows.append(pd.DataFrame({
            "case_id": df["case_id"].to_numpy()[mask], "action": a, "customer_id": df["customer_id"].to_numpy()[mask],
            "recovery_probability": pa, "baseline_probability": p0[mask], "estimated_uplift": pa - p0[mask],
            "amount_paise": amt[mask], "incentive_discount_paise": d, "action_cost_paise": c,
            "requires_customer_contact": ACTION_REGISTRY[a].requires_customer_contact,
            "expected_incremental_value_paise": (pa - p0[mask]) * amt[mask] - pa * d - c}))
    return pd.concat(rows, ignore_index=True)


def oracle_constrained_policy(cases_df, budgets, feasible_col="feasible_actions", shift=True):
    alloc, usage = exact_allocate(oracle_scored(cases_df, feasible_col, shift), budgets)
    return alloc[["case_id", "action"]], usage


def oracle_unconstrained_policy(cases_df, feasible_col="feasible_actions", shift=True):
    return uplift_unconstrained_policy(oracle_scored(cases_df, feasible_col, shift))
