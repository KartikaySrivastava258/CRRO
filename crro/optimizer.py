from dataclasses import dataclass, field
import pandas as pd
from .action_registry import ACTION_REGISTRY


@dataclass
class Budgets:
    daily_contacts: int = 4000
    daily_incentive_inr: float = 100000
    per_action_daily_cap: dict = field(default_factory=lambda: {"ALTERNATIVE_PAYMENT_METHOD": 1000})
    per_customer_daily_cap: int = 1


def _action_spec(action):
    try:
        return ACTION_REGISTRY[action]
    except KeyError as exc:
        raise ValueError(f"Unknown recovery action: {action}") from exc


def _requires_contact(action):
    return bool(_action_spec(action).requires_customer_contact)


def compute_expected_incremental_value(scored_df, case_amounts, incentive_pct=.02):
    df = scored_df.merge(case_amounts, on="case_id", how="left")
    if df["amount_inr"].isna().any():
        raise ValueError("Every scored case must have amount_inr")
    df["action_cost_inr"] = df["action"].map(lambda a: _action_spec(a).monetary_cost_inr)
    df["recoverable_amount_inr"] = df["amount_inr"]
    df["incentive_pct"] = df["action"].map(
        lambda a: min(incentive_pct, _action_spec(a).max_incentive_pct)
        if _action_spec(a).is_incentive else 0.0
    )
    df["incentive_cost_inr"] = df["amount_inr"] * df["incentive_pct"]
    df["contact_cost_inr"] = df["action"].map(lambda a: 1.0 if _requires_contact(a) else 0.0)
    df["requires_customer_contact"] = df["action"].map(_requires_contact)
    df["expected_incremental_value_inr"] = (
        df["estimated_uplift"] * df["amount_inr"]
        - df["action_cost_inr"]
        - df["incentive_cost_inr"]
    )
    return df


def _fallback_greedy(df, budgets):
    selected = {}
    contacts = 0
    incentive = 0.0
    caps = {a: 0 for a in budgets.per_action_daily_cap}
    customers = {}
    ranked = df[df["expected_incremental_value_inr"] > 0].sort_values(
        "expected_incremental_value_inr", ascending=False
    )
    for _, r in ranked.iterrows():
        case_id = r["case_id"]
        if case_id in selected:
            continue
        action = str(r["action"])
        customer = str(r.get("customer_id", case_id))
        needs_contact = _requires_contact(action)
        incentive_cost = float(r["incentive_cost_inr"])
        if customers.get(customer, 0) >= budgets.per_customer_daily_cap:
            continue
        if needs_contact and contacts >= budgets.daily_contacts:
            continue
        if incentive + incentive_cost > budgets.daily_incentive_inr:
            continue
        cap = budgets.per_action_daily_cap.get(action)
        if cap is not None and caps.get(action, 0) >= cap:
            continue
        row = r.to_dict()
        row["requires_customer_contact"] = needs_contact
        selected[case_id] = row
        customers[customer] = customers.get(customer, 0) + 1
        contacts += int(needs_contact)
        incentive += incentive_cost
        caps[action] = caps.get(action, 0) + 1
    return selected, contacts, incentive, caps, "greedy_fallback"


def _no_action_row(case_id, reason="NO_POSITIVE_UPLIFT_OR_BUDGET"):
    return {
        "case_id": case_id,
        "action": "NO_ACTION",
        "estimated_uplift": 0.0,
        "expected_incremental_value_inr": 0.0,
        "action_cost_inr": 0.0,
        "incentive_cost_inr": 0.0,
        "requires_customer_contact": False,
        "rejected_reason": reason,
    }


def _finalize_allocation(scored_df, chosen, usage):
    rows = []
    for case_id, _group in scored_df.groupby("case_id"):
        if case_id in chosen:
            row = dict(chosen[case_id])
            row["rejected_reason"] = None
            rows.append(row)
        else:
            rows.append(_no_action_row(case_id))
    return pd.DataFrame(rows), usage


def exact_allocate(scored_df, budgets, time_limit_seconds=10):
    """Exact 0/1 portfolio allocation with CP-SAT; NO_ACTION is implicit."""
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        selected, contacts, incentive, caps, solver = _fallback_greedy(scored_df, budgets)
        return _finalize_allocation(scored_df, selected, {
            "contacts_used": contacts, "contacts_budget": budgets.daily_contacts,
            "incentive_used_inr": round(incentive, 2),
            "incentive_budget_inr": budgets.daily_incentive_inr,
            "action_counts": caps, "solver": solver,
        })

    df = scored_df.copy().reset_index(drop=True)
    candidates = df[df["expected_incremental_value_inr"] > 0].copy()
    if candidates.empty:
        return _finalize_allocation(df, {}, {
            "contacts_used": 0, "contacts_budget": budgets.daily_contacts,
            "incentive_used_inr": 0.0, "incentive_budget_inr": budgets.daily_incentive_inr,
            "action_counts": {a: 0 for a in budgets.per_action_daily_cap},
            "solver": "cp-sat", "solver_status": "EMPTY", "objective_inr": 0.0,
        })

    model = cp_model.CpModel()
    candidate_indices = list(candidates.index)
    vars_by_index = {i: model.NewBoolVar(f"allocate_{i}") for i in candidate_indices}

    contact_terms = []
    incentive_terms = []
    for i in candidate_indices:
        row = df.loc[i]
        contact_terms.append(int(_requires_contact(row["action"])) * vars_by_index[i])
        incentive_terms.append(int(round(float(row["incentive_cost_inr"]) * 100)) * vars_by_index[i])
    model.Add(sum(contact_terms) <= int(budgets.daily_contacts))
    model.Add(sum(incentive_terms) <= int(round(budgets.daily_incentive_inr * 100)))

    for action, cap in budgets.per_action_daily_cap.items():
        action_vars = [vars_by_index[i] for i in candidate_indices if df.loc[i, "action"] == action]
        if action_vars:
            model.Add(sum(action_vars) <= int(cap))

    customer_column = candidates["customer_id"] if "customer_id" in candidates.columns else candidates["case_id"]
    for _customer, group in candidates.groupby(customer_column, sort=False):
        model.Add(sum(vars_by_index[i] for i in group.index) <= int(budgets.per_customer_daily_cap))

    coefficients = [int(round(float(df.loc[i, "expected_incremental_value_inr"]) * 100)) for i in candidate_indices]
    model.Maximize(sum(coeff * vars_by_index[i] for coeff, i in zip(coefficients, candidate_indices)))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_seconds)
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        selected, contacts, incentive, caps, fallback = _fallback_greedy(df, budgets)
        return _finalize_allocation(df, selected, {
            "contacts_used": contacts, "contacts_budget": budgets.daily_contacts,
            "incentive_used_inr": round(incentive, 2),
            "incentive_budget_inr": budgets.daily_incentive_inr,
            "action_counts": caps, "solver": fallback, "solver_status": solver.StatusName(status),
        })

    chosen = {}
    contacts_used = 0
    incentive_used = 0.0
    caps = {a: 0 for a in budgets.per_action_daily_cap}
    for i in candidate_indices:
        if solver.Value(vars_by_index[i]):
            row = df.loc[i].to_dict()
            row["requires_customer_contact"] = _requires_contact(row["action"])
            chosen[row["case_id"]] = row
            contacts_used += int(row["requires_customer_contact"])
            incentive_used += float(row["incentive_cost_inr"])
            action = row["action"]
            caps[action] = caps.get(action, 0) + 1

    return _finalize_allocation(df, chosen, {
        "contacts_used": contacts_used, "contacts_budget": budgets.daily_contacts,
        "incentive_used_inr": round(incentive_used, 2),
        "incentive_budget_inr": budgets.daily_incentive_inr,
        "action_counts": caps, "solver": "cp-sat", "solver_status": solver.StatusName(status),
        "objective_inr": round(solver.ObjectiveValue() / 100, 2),
    })


def greedy_allocate(scored_df, budgets):
    selected, contacts, incentive, caps, solver = _fallback_greedy(scored_df, budgets)
    return _finalize_allocation(scored_df, selected, {
        "contacts_used": contacts, "contacts_budget": budgets.daily_contacts,
        "incentive_used_inr": round(incentive, 2),
        "incentive_budget_inr": budgets.daily_incentive_inr,
        "action_counts": caps, "solver": solver,
    })
