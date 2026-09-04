"""
Constrained Portfolio Optimizer v2 (Required Changes Sections 6, 7, 9, 10)
--------------------------------------------------------------------------
Two interchangeable allocators behind the same interface:

  greedy_allocate(...)  - fast heuristic (kept for baseline / large N)
  exact_allocate(...)   - OR-Tools CP-SAT exact/near-exact 0/1 assignment:

      maximize   sum_i sum_a x[i,a] * value[i,a]
      subject to sum_a x[i,a] <= 1                       for every case i
                 sum contact_i,a * x[i,a] <= contact_budget
                 sum incentive_i,a * x[i,a] <= incentive_budget
                 sum_i x[i,a] <= action_capacity[a]       for capped actions
                 sum_a x[i,a] <= per_customer_contact_cap  per customer
                 x[i,a] in {0,1}

Expanded economics (Section 10):
  expected_incremental_value
  = incremental_recovery_value
  - action_cost - contact_cost - incentive_cost - expected_penalty

`expected_penalty` and `expected_customer_friction_cost` are explicitly
zeroed with a documented reason (Section 10: "do not invent fake
precision") -- the data model carries the fields so they can be
populated once a real penalty/friction model exists.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional
import pandas as pd
from .action_registry import ACTION_REGISTRY
from .offers import best_eligible_offer, incentive_cost_for_offer


@dataclass
class Budgets:
    daily_contacts: int
    daily_incentive_inr: float
    per_action_daily_cap: Dict[str, int] = field(default_factory=dict)
    per_customer_daily_contact_cap: int = 1


def compute_expected_incremental_value(scored_df, cases_df):
    """
    scored_df: case_id, action, estimated_uplift, recovery_probability,
               baseline_probability
    cases_df: case_id, amount_inr, customer_id (for offer eligibility)

    Populates separate economic columns per Section 10:
      action_cost_inr, contact_cost_inr, incentive_cost_inr,
      expected_penalty_inr, expected_incremental_value_inr
    """
    df = scored_df.merge(
        cases_df[["case_id", "amount_inr", "customer_id"]], on="case_id", how="left"
    )

    action_cost = df["action"].map(lambda a: ACTION_REGISTRY[a].monetary_cost_inr)
    requires_contact = df["action"].map(lambda a: ACTION_REGISTRY[a].requires_customer_contact)
    is_incentive = df["action"].map(lambda a: ACTION_REGISTRY[a].is_incentive)
    max_incentive_pct = df["action"].map(lambda a: ACTION_REGISTRY[a].max_incentive_pct)

    df["action_cost_inr"] = action_cost
    df["contact_cost_inr"] = 0.0

    incentive_cost = []
    for _, row in df.iterrows():
        if not is_incentive.loc[row.name]:
            incentive_cost.append(0.0)
            continue
        offer = best_eligible_offer(
            {"amount_inr": row["amount_inr"]}, max_incentive_pct.loc[row.name]
        )
        incentive_cost.append(incentive_cost_for_offer(offer, row["amount_inr"]))
    df["incentive_cost_inr"] = incentive_cost

    df["expected_penalty_inr"] = 0.0
    df["expected_customer_friction_cost_inr"] = 0.0

    df["recoverable_amount_inr"] = df["amount_inr"]
    df["incremental_recovery_value_inr"] = df["estimated_uplift"] * df["amount_inr"]
    df["expected_incremental_value_inr"] = (
        df["incremental_recovery_value_inr"]
        - df["action_cost_inr"]
        - df["contact_cost_inr"]
        - df["incentive_cost_inr"]
        - df["expected_penalty_inr"]
        - df["expected_customer_friction_cost_inr"]
    )
    df["requires_customer_contact"] = requires_contact
    return df


def _positive_candidates(scored_df):
    return scored_df[scored_df["expected_incremental_value_inr"] > 0].copy()


def greedy_allocate(scored_df, budgets: Budgets):
    """MVP heuristic allocator (Section 6: kept available as optimizer_mode='greedy')."""
    df = scored_df.copy()
    candidates = _positive_candidates(df).sort_values(
        "expected_incremental_value_inr", ascending=False
    )

    selected = {}
    contacts_used = 0
    incentive_used = 0.0
    action_counts = {a: 0 for a in budgets.per_action_daily_cap}
    customer_contacts = {}

    for _, row in candidates.iterrows():
        cid = row["case_id"]
        if cid in selected:
            continue
        cust = row["customer_id"]

        if row["requires_customer_contact"]:
            if contacts_used >= budgets.daily_contacts:
                continue
            if customer_contacts.get(cust, 0) >= budgets.per_customer_daily_contact_cap:
                continue
        if incentive_used + row["incentive_cost_inr"] > budgets.daily_incentive_inr:
            continue
        cap = budgets.per_action_daily_cap.get(row["action"])
        if cap is not None and action_counts.get(row["action"], 0) >= cap:
            continue

        selected[cid] = row.to_dict()
        if row["requires_customer_contact"]:
            contacts_used += 1
            customer_contacts[cust] = customer_contacts.get(cust, 0) + 1
        incentive_used += row["incentive_cost_inr"]
        if row["action"] in action_counts:
            action_counts[row["action"]] += 1

    return _finalize_allocation(df, selected), {
        "contacts_used": contacts_used, "contacts_budget": budgets.daily_contacts,
        "incentive_used_inr": round(incentive_used, 2),
        "incentive_budget_inr": budgets.daily_incentive_inr,
        "solver": "greedy", "status": "HEURISTIC",
    }


def exact_allocate(scored_df, budgets: Budgets, time_limit_seconds=30, max_candidates=250000):
    """
    OR-Tools CP-SAT exact 0/1 assignment (Section 6). Falls back to
    greedy automatically if OR-Tools is unavailable or the candidate
    set is too large for the time budget, with the fallback clearly
    reported in the returned usage dict (never silently degrades).
    """
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        alloc, usage = greedy_allocate(scored_df, budgets)
        usage["solver"] = "greedy_fallback_no_ortools"
        return alloc, usage

    df = scored_df.copy()
    candidates = _positive_candidates(df).reset_index(drop=True)
    if len(candidates) == 0:
        return _finalize_allocation(df, {}), {
            "contacts_used": 0, "contacts_budget": budgets.daily_contacts,
            "incentive_used_inr": 0.0, "incentive_budget_inr": budgets.daily_incentive_inr,
            "solver": "cp_sat", "status": "NO_POSITIVE_CANDIDATES",
        }
    if len(candidates) > max_candidates:
        alloc, usage = greedy_allocate(scored_df, budgets)
        usage["solver"] = f"greedy_fallback_too_large_{len(candidates)}_candidates"
        return alloc, usage

    model = cp_model.CpModel()
    x = {i: model.NewBoolVar(f"x_{i}") for i in candidates.index}

    for case_id, idxs in candidates.groupby("case_id").groups.items():
        model.Add(sum(x[i] for i in idxs) <= 1)

    contact_idxs = candidates.index[candidates["requires_customer_contact"]]
    if len(contact_idxs) > 0:
        model.Add(sum(x[i] for i in contact_idxs) <= budgets.daily_contacts)

    SCALE = 100
    incentive_terms = [(x[i], int(round(candidates.loc[i, "incentive_cost_inr"] * SCALE)))
                        for i in candidates.index if candidates.loc[i, "incentive_cost_inr"] > 0]
    if incentive_terms:
        model.Add(sum(v * c for v, c in incentive_terms) <= int(round(budgets.daily_incentive_inr * SCALE)))

    for action, cap in budgets.per_action_daily_cap.items():
        idxs = candidates.index[candidates["action"] == action]
        if len(idxs) > 0:
            model.Add(sum(x[i] for i in idxs) <= cap)

    for cust, idxs in candidates.groupby("customer_id").groups.items():
        contact_sub = [i for i in idxs if candidates.loc[i, "requires_customer_contact"]]
        if len(contact_sub) > 1:
            model.Add(sum(x[i] for i in contact_sub) <= budgets.per_customer_daily_contact_cap)

    VALUE_SCALE = 100
    model.Maximize(sum(
        x[i] * int(round(candidates.loc[i, "expected_incremental_value_inr"] * VALUE_SCALE))
        for i in candidates.index
    ))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)

    status_name = solver.StatusName(status)
    selected = {}
    contacts_used = 0
    incentive_used = 0.0
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for i in candidates.index:
            if solver.Value(x[i]) == 1:
                row = candidates.loc[i]
                selected[row["case_id"]] = row.to_dict()
                if row["requires_customer_contact"]:
                    contacts_used += 1
                incentive_used += row["incentive_cost_inr"]

    usage = {
        "contacts_used": contacts_used, "contacts_budget": budgets.daily_contacts,
        "incentive_used_inr": round(incentive_used, 2),
        "incentive_budget_inr": budgets.daily_incentive_inr,
        "solver": "cp_sat", "status": status_name,
        "objective_value_inr": round(solver.ObjectiveValue() / VALUE_SCALE, 2)
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        "best_objective_bound_inr": round(solver.BestObjectiveBound() / VALUE_SCALE, 2)
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        "wall_time_seconds": round(solver.WallTime(), 3),
    }
    return _finalize_allocation(df, selected), usage


def _finalize_allocation(df, selected):
    result_rows = []
    for cid, all_case_rows in df.groupby("case_id"):
        if cid in selected:
            r = selected[cid]
            r["rejected_reason"] = None
            result_rows.append(r)
        else:
            best = all_case_rows.sort_values(
                "expected_incremental_value_inr", ascending=False
            ).iloc[0]
            reason = "NO_POSITIVE_UPLIFT" if best["expected_incremental_value_inr"] <= 0 \
                else "POLICY_BLOCKED"
            row = {
                "case_id": cid, "action": "NO_ACTION",
                "estimated_uplift": 0.0, "expected_incremental_value_inr": 0.0,
                "action_cost_inr": 0.0, "contact_cost_inr": 0.0, "incentive_cost_inr": 0.0,
                "expected_penalty_inr": 0.0, "expected_customer_friction_cost_inr": 0.0,
                "requires_customer_contact": False,
                "rejected_reason": reason,
            }
            result_rows.append(row)
    return pd.DataFrame(result_rows)


def allocate(scored_df, budgets: Budgets, mode="exact", **kwargs):
    """Single entry point. mode: 'exact' (default) or 'greedy'."""
    if mode == "greedy":
        return greedy_allocate(scored_df, budgets)
    return exact_allocate(scored_df, budgets, **kwargs)
