"""
Baseline Policies v2 (Required Changes, Section 5)
------------------------------------------------------
The comparison ladder the evaluator must be able to show:

  CONTROL
  FIXED_RETRY              (existing operational habit)
  RULE_BASED                (existing operational habit)
  HISTORICAL_BEST_ACTION     Baseline A -- population-level action averages
  RAW_PROBABILITY_MAXIMIZER  Baseline B -- argmax P(recovery), not uplift
  UPLIFT_ONLY_NO_PORTFOLIO    Baseline C -- argmax uplift, no budget limits
  ORACLE_UPPER_BOUND          Baseline D -- uses the HIDDEN true effect;
                               never deployable, upper bound only
  CRRO                        uplift + portfolio optimizer + guardrails
"""

import numpy as np
import pandas as pd
from .action_registry import ACTION_REGISTRY
from .oracle import true_p_recovery


def control_policy(cases_df):
    return pd.DataFrame({"case_id": cases_df["case_id"], "action": "NO_ACTION"})


def fixed_retry_policy(cases_df, eligible_col="eligible_actions", budgets=None, opted_out=None):
    opted_out = opted_out or set()
    rows = []
    contacts_used = 0
    customer_contacts = {}
    cap = budgets.per_customer_daily_contact_cap if budgets is not None else None
    for _, row in cases_df.iterrows():
        elig = row[eligible_col]
        action = "RETRY_NOW" if "RETRY_NOW" in elig else "NO_ACTION"
        spec = ACTION_REGISTRY[action]
        cust = row["customer_id"]
        if spec.requires_customer_contact and cust in opted_out:
            action = "NO_ACTION"
            spec = ACTION_REGISTRY[action]
        if spec.requires_customer_contact and cap is not None and customer_contacts.get(cust, 0) >= cap:
            action = "NO_ACTION"
            spec = ACTION_REGISTRY[action]
        if budgets is not None and spec.requires_customer_contact:
            if contacts_used >= budgets.daily_contacts:
                action = "NO_ACTION"
            else:
                contacts_used += 1
                customer_contacts[cust] = customer_contacts.get(cust, 0) + 1
        rows.append({"case_id": row["case_id"], "action": action})
    return pd.DataFrame(rows)


def rule_based_policy(cases_df, eligible_col="eligible_actions", budgets=None, opted_out=None):
    """Plausible hand-written heuristic; no uplift estimation at all."""
    opted_out = opted_out or set()
    rows = []
    contacts_used = 0
    incentive_used = 0.0
    customer_contacts = {}
    cap = budgets.per_customer_daily_contact_cap if budgets is not None else None
    for _, row in cases_df.iterrows():
        elig = row[eligible_col]
        fr = row["failure_reason"]
        cust = row["customer_id"]
        if fr == "card_expired" and "ALTERNATIVE_PAYMENT_METHOD" in elig:
            action = "ALTERNATIVE_PAYMENT_METHOD"
        elif fr == "risk_block" and "PAYMENT_LINK" in elig:
            action = "PAYMENT_LINK"
        elif fr == "insufficient_funds" and "RETRY_LATER" in elig:
            action = "RETRY_LATER"
        elif "RETRY_NOW" in elig:
            action = "RETRY_NOW"
        else:
            action = "NO_ACTION"

        spec = ACTION_REGISTRY[action]
        if spec.requires_customer_contact and cust in opted_out:
            action = "NO_ACTION"
            spec = ACTION_REGISTRY[action]
        if spec.requires_customer_contact and cap is not None and customer_contacts.get(cust, 0) >= cap:
            action = "NO_ACTION"
            spec = ACTION_REGISTRY[action]
        if budgets is not None:
            if spec.requires_customer_contact and contacts_used >= budgets.daily_contacts:
                action = "NO_ACTION"
                spec = ACTION_REGISTRY[action]
            elif spec.is_incentive and incentive_used + row["amount_inr"] * 0.02 > budgets.daily_incentive_inr:
                action = "NO_ACTION"
                spec = ACTION_REGISTRY[action]
        if spec.requires_customer_contact:
            contacts_used += 1
            customer_contacts[cust] = customer_contacts.get(cust, 0) + 1
        if spec.is_incentive:
            incentive_used += row["amount_inr"] * 0.02
        rows.append({"case_id": row["case_id"], "action": action})
    return pd.DataFrame(rows)


def historical_best_action_policy(cases_df, train_df, eligible_col="eligible_actions",
                                    budgets=None, opted_out=None):
    """
    Baseline A. Choose the single action with the highest AGGREGATE
    historical recovery rate (computed from the RCT train set), applied
    uniformly to every case where it's eligible. Demonstrates why
    population-level averages under-perform segment-aware uplift.
    """
    opted_out = opted_out or set()
    hist_rates = (
        train_df[train_df["assigned_action"] != "NO_ACTION"]
        .groupby("assigned_action")["outcome_recovered"].mean()
        .sort_values(ascending=False)
    )
    ranked_actions = list(hist_rates.index)

    rows = []
    contacts_used = 0
    incentive_used = 0.0
    customer_contacts = {}
    cap = budgets.per_customer_daily_contact_cap if budgets is not None else None
    for _, row in cases_df.iterrows():
        elig = row[eligible_col]
        action = "NO_ACTION"
        for a in ranked_actions:
            if a in elig:
                action = a
                break
        spec = ACTION_REGISTRY[action]
        cust = row["customer_id"]
        if spec.requires_customer_contact and cust in opted_out:
            action = "NO_ACTION"
            spec = ACTION_REGISTRY[action]
        if spec.requires_customer_contact and cap is not None and customer_contacts.get(cust, 0) >= cap:
            action = "NO_ACTION"
            spec = ACTION_REGISTRY[action]
        if budgets is not None:
            if spec.requires_customer_contact and contacts_used >= budgets.daily_contacts:
                action = "NO_ACTION"
                spec = ACTION_REGISTRY[action]
            elif spec.is_incentive and incentive_used + row["amount_inr"] * 0.02 > budgets.daily_incentive_inr:
                action = "NO_ACTION"
                spec = ACTION_REGISTRY[action]
        if spec.requires_customer_contact:
            contacts_used += 1
            customer_contacts[cust] = customer_contacts.get(cust, 0) + 1
        if spec.is_incentive:
            incentive_used += row["amount_inr"] * 0.02
        rows.append({"case_id": row["case_id"], "action": action})
    return pd.DataFrame(rows), dict(hist_rates.round(4))


def raw_probability_maximizer_policy(scored_df):
    """
    Baseline B. argmax P(recovery | X, action) -- NOT uplift. Chosen
    per case, unconstrained by budget, so the comparison isolates the
    value of using UPLIFT instead of raw probability (Section 5,
    Baseline B: "one of the most important comparison baselines").
    """
    idx = scored_df.groupby("case_id")["recovery_probability"].idxmax()
    best = scored_df.loc[idx, ["case_id", "action"]].reset_index(drop=True)
    return best


def uplift_only_no_portfolio_policy(scored_df):
    """
    Baseline C. argmax expected_incremental_value_inr per case, but with
    NO portfolio/budget constraints at all. Isolates the value of the
    optimizer layer on top of the uplift model.
    """
    idx = scored_df.groupby("case_id")["expected_incremental_value_inr"].idxmax()
    best = scored_df.loc[idx, ["case_id", "action", "expected_incremental_value_inr"]].copy()
    best.loc[best["expected_incremental_value_inr"] <= 0, "action"] = "NO_ACTION"
    return best[["case_id", "action"]]


def oracle_upper_bound_policy(cases_df, eligible_col="eligible_actions", shift=True):
    """
    Baseline D. Uses the HIDDEN true treatment effect (not the trained
    model) to pick, per case, the action with the highest TRUE uplift
    net of action cost. This is a benchmark ceiling only -- never
    presented as a deployable policy (Section 5: "Do not present this
    as a deployable policy"). Vectorized across actions for performance.
    """
    df = cases_df.reset_index(drop=True)
    p_control = true_p_recovery(df, np.full(len(df), "NO_ACTION", dtype=object), shift=shift)

    all_actions = [a for a in ACTION_REGISTRY if a != "NO_ACTION"]
    value_by_action = {}
    for a in all_actions:
        p_a = true_p_recovery(df, np.full(len(df), a, dtype=object), shift=shift)
        uplift = p_a - p_control
        cost = ACTION_REGISTRY[a].monetary_cost_inr
        value_by_action[a] = uplift * df["amount_inr"].values - cost

    best_action = np.full(len(df), "NO_ACTION", dtype=object)
    best_val = np.zeros(len(df))
    for i, row_elig in enumerate(df[eligible_col]):
        for a in row_elig:
            if a == "NO_ACTION":
                continue
            v = value_by_action[a][i]
            if v > best_val[i]:
                best_val[i] = v
                best_action[i] = a

    return pd.DataFrame({"case_id": df["case_id"], "action": best_action})
