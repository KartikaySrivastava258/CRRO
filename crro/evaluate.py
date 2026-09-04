"""
Evaluation Harness v2 (Required Changes Sections 4, 11, 12)
------------------------------------------------------------------
KEY FIX (Section 4): NO_ACTION now gets a REAL outcome from the same
hidden oracle used for every other action, instead of being forced to
`recovered = 0`. This makes the central claim -- "CRRO creates
incremental recovery vs. doing nothing" -- measurable rather than
assumed. Every policy's recovered value is compared against the
CONTROL policy's *actually realized* (not fabricated-zero) recovery,
using common random numbers so the comparison isn't confounded by
different random draws.
"""

import numpy as np
import pandas as pd
from .action_registry import ACTION_REGISTRY
from .oracle import true_p_recovery


def add_common_random_numbers(df, seed=777):
    rng = np.random.default_rng(seed)
    df = df.copy()
    df["_u"] = rng.uniform(0, 1, size=len(df))
    return df


def realize_policy_outcomes(test_df, action_assignment_df, shift=True):
    """
    Realizes outcomes for EVERY action, including NO_ACTION, from the
    same hidden oracle (Section 4 fix). test_df must carry `_u` (see
    add_common_random_numbers) so all policies share the same draws.
    """
    merged = test_df.merge(action_assignment_df[["case_id", "action"]],
                            on="case_id", how="left")
    merged["action"] = merged["action"].fillna("NO_ACTION")

    p = true_p_recovery(merged, merged["action"].values, shift=shift)
    recovered = (merged["_u"].values < p).astype(int)   # NO_ACTION uses its real p too

    action_cost = merged["action"].map(lambda a: ACTION_REGISTRY[a].monetary_cost_inr).values
    is_incentive = merged["action"].map(lambda a: ACTION_REGISTRY[a].is_incentive).values
    incentive_cost = merged["amount_inr"].values * 0.02 * is_incentive.astype(float)
    incentive_cost = np.where(merged["action"].values == "NO_ACTION", 0.0, incentive_cost)
    action_cost = np.where(merged["action"].values == "NO_ACTION", 0.0, action_cost)

    recovered_amount = np.where(recovered == 1, merged["amount_inr"].values, 0.0)
    net_value = recovered_amount - action_cost - incentive_cost

    out = merged[["case_id", "action", "amount_inr", "failure_reason",
                  "payment_method", "merchant_category"]].copy()
    out["recovered"] = recovered
    out["true_p_recovery_this_action"] = p
    out["recovered_amount_inr"] = recovered_amount
    out["action_cost_inr"] = action_cost
    out["incentive_cost_inr"] = incentive_cost
    out["net_value_inr"] = net_value
    return out


def summarize_policy(realized_df, policy_name):
    n = len(realized_df)
    return {
        "policy": policy_name,
        "n_cases": n,
        "recovery_rate": round(realized_df["recovered"].mean(), 4),
        "gross_recovered_inr": round(realized_df["recovered_amount_inr"].sum(), 2),
        "total_action_cost_inr": round((realized_df["action_cost_inr"] +
                                          realized_df["incentive_cost_inr"]).sum(), 2),
        "net_recovered_inr": round(realized_df["net_value_inr"].sum(), 2),
        "actions_taken": int((realized_df["action"] != "NO_ACTION").sum()),
    }


def incremental_vs_control(realized_df, control_realized_df):
    """
    Section 4's required incremental metrics, PAIRED by case_id (both
    frames realized under the same common random numbers so the
    comparison is apples-to-apples per case, not just in aggregate).
    """
    a = realized_df.set_index("case_id")
    c = control_realized_df.set_index("case_id")
    common = a.index.intersection(c.index)
    a, c = a.loc[common], c.loc[common]

    incremental_recovered_cases = int(a["recovered"].sum() - c["recovered"].sum())
    incremental_recovered_inr = round(a["recovered_amount_inr"].sum() - c["recovered_amount_inr"].sum(), 2)
    incremental_net_value_inr = round(a["net_value_inr"].sum() - c["net_value_inr"].sum(), 2)
    incremental_recovery_rate = round(a["recovered"].mean() - c["recovered"].mean(), 4)
    return {
        "incremental_recovered_cases": incremental_recovered_cases,
        "incremental_recovered_inr": incremental_recovered_inr,
        "incremental_recovery_rate": incremental_recovery_rate,
        "incremental_net_value_inr": incremental_net_value_inr,
    }


def policy_comparison_table(policies_realized: dict, control_name="CONTROL (no action)"):
    """
    Produces the Section 4 acceptance-criterion table:
      Policy | Gross | Incremental | Net Incremental
    """
    control_realized = policies_realized[control_name]
    rows = []
    for name, realized in policies_realized.items():
        summ = summarize_policy(realized, name)
        inc = incremental_vs_control(realized, control_realized)
        rows.append({**summ, **inc})
    return pd.DataFrame(rows)


def segment_performance(realized_df, by="failure_reason"):
    return realized_df.groupby(by).agg(
        n=("case_id", "count"),
        recovery_rate=("recovered", "mean"),
        net_value_inr=("net_value_inr", "sum"),
    ).round(4).reset_index()


def calibration_report(y_true, y_prob, n_bins=10):
    bins = np.linspace(0, 1, n_bins + 1)
    idx = np.digitize(y_prob, bins) - 1
    idx = np.clip(idx, 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        mask = idx == b
        if mask.sum() == 0:
            continue
        rows.append({
            "bin": f"{bins[b]:.1f}-{bins[b+1]:.1f}",
            "n": int(mask.sum()),
            "mean_predicted": round(float(np.mean(y_prob[mask])), 4),
            "observed_rate": round(float(np.mean(np.asarray(y_true)[mask])), 4),
        })
    brier = float(np.mean((np.asarray(y_prob) - np.asarray(y_true)) ** 2))
    return {"brier_score": round(brier, 4), "bins": rows}



def uplift_at_k(test_df, scored_df, k_fracs=(0.1, 0.2, 0.3, 0.5), shift=True):
    """
    uplift@k: taking the top-k% of cases ranked by estimated uplift for
    their best action, what is the REALIZED average uplift (true oracle
    p(action) - true oracle p(NO_ACTION)) for that subset? Demonstrates
    the model's ranking quality using ground truth, independent of the
    optimizer/budgets.
    """
    idx = scored_df.groupby("case_id")["estimated_uplift"].idxmax()
    best = scored_df.loc[idx, ["case_id", "action", "estimated_uplift"]]
    best = best.sort_values("estimated_uplift", ascending=False).reset_index(drop=True)

    merged = best.merge(test_df, on="case_id", how="left")
    p_control = true_p_recovery(merged, np.full(len(merged), "NO_ACTION", dtype=object), shift=shift)
    p_action = true_p_recovery(merged, merged["action"].values, shift=shift)
    merged["true_uplift"] = p_action - p_control

    n = len(merged)
    results = {}
    for frac in k_fracs:
        top_n = max(1, int(n * frac))
        results[f"uplift@{int(frac*100)}pct"] = round(float(merged["true_uplift"].head(top_n).mean()), 4)
    results["uplift@100pct_overall"] = round(float(merged["true_uplift"].mean()), 4)
    return results


def auuc_qini(test_df, scored_df, shift=True, n_points=20):
    """
    Approximate AUUC (Area Under the Uplift Curve) and Qini coefficient.
    Cases ranked by estimated uplift; at each cutoff we compute the
    cumulative TRUE incremental recovered amount (oracle-based, so this
    is a benchmark-only diagnostic, same caveat as the oracle baseline).
    """
    idx = scored_df.groupby("case_id")["estimated_uplift"].idxmax()
    best = scored_df.loc[idx, ["case_id", "action", "estimated_uplift"]]
    best = best.sort_values("estimated_uplift", ascending=False).reset_index(drop=True)

    merged = best.merge(test_df, on="case_id", how="left")
    p_control = true_p_recovery(merged, np.full(len(merged), "NO_ACTION", dtype=object), shift=shift)
    p_action = true_p_recovery(merged, merged["action"].values, shift=shift)
    merged["true_incremental_inr"] = (p_action - p_control) * merged["amount_inr"].values

    n = len(merged)
    cum_gain = np.cumsum(merged["true_incremental_inr"].values)
    total_gain = cum_gain[-1] if n else 0.0

    avg_per_case = total_gain / n if n else 0.0
    random_line = np.array([(i + 1) * avg_per_case for i in range(n)])

    auuc = float(np.trapezoid(cum_gain) / n) if n else 0.0
    qini = float(np.trapezoid(cum_gain - random_line) / n) if n else 0.0

    checkpoints = []
    for frac in np.linspace(0.05, 1.0, n_points):
        i = max(1, int(n * frac)) - 1
        checkpoints.append({
            "top_fraction": round(float(frac), 3),
            "cumulative_true_incremental_inr": round(float(cum_gain[i]), 2),
        })
    return {
        "auuc_inr_per_case": round(auuc, 2),
        "qini_coefficient_inr_per_case": round(qini, 2),
        "curve": checkpoints,
    }



def policy_value_curve(test_with_u, scored_df, budgets_factory, allocate_fn,
                        contact_budgets, control_realized_df, mode="greedy"):
    """
    Re-runs allocation at increasing contact_budget levels and reports
    incremental net value at each level -- the "recovered value vs.
    scarce contact budget" proof of the portfolio-optimization thesis
    (Section 12).
    `budgets_factory(contacts)` -> Budgets instance
    `allocate_fn(scored_df, budgets, mode=mode)` -> (alloc_df, usage)
    """
    points = []
    for contacts in contact_budgets:
        budgets = budgets_factory(contacts)
        alloc, usage = allocate_fn(scored_df, budgets, mode=mode)
        realized = realize_policy_outcomes(test_with_u, alloc, shift=True)
        inc = incremental_vs_control(realized, control_realized_df)
        points.append({
            "contact_budget": contacts,
            "incremental_net_value_inr": inc["incremental_net_value_inr"],
            "incremental_recovered_inr": inc["incremental_recovered_inr"],
            "contacts_used": usage["contacts_used"],
        })
    return points



def mean_ci95(values):
    arr = np.asarray(values, dtype=float)
    mean = float(arr.mean())
    if len(arr) > 1:
        se = float(arr.std(ddof=1) / np.sqrt(len(arr)))
        ci95 = 1.96 * se
    else:
        ci95 = 0.0
    return {"mean": round(mean, 2), "ci95_halfwidth": round(ci95, 2),
            "n_seeds": len(arr), "values": [round(v, 2) for v in arr]}
