"""Fair policy evaluation with a real NO_ACTION control outcome.

The benchmark uses common random numbers so every policy sees the same
case-level stochastic draw. NO_ACTION is evaluated with the oracle's true
baseline recovery probability; it is not artificially forced to zero.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .action_registry import ACTION_REGISTRY, incentive_cost_inr
from .oracle import true_p_recovery


def add_common_random_numbers(df: pd.DataFrame, seed: int = 777) -> pd.DataFrame:
    out = df.copy()
    out["_u"] = np.random.default_rng(seed).uniform(0, 1, len(out))
    return out


def realize_policy_outcomes(
    test_df: pd.DataFrame,
    decisions: pd.DataFrame,
    shift: bool = True,
    approved_incentive_pct: float = 0.02,
) -> pd.DataFrame:
    merged = test_df.merge(decisions[["case_id", "action"]], on="case_id", how="left")
    merged["action"] = merged["action"].fillna("NO_ACTION")
    p = true_p_recovery(merged, merged["action"].to_numpy(), shift=shift)
    recovered = (merged["_u"].to_numpy() < p).astype(int)

    action_cost = merged["action"].map(lambda a: ACTION_REGISTRY[a].monetary_cost_inr).to_numpy()
    incentive_cost = np.array([
        incentive_cost_inr(amount, action, approved_incentive_pct)
        for amount, action in zip(merged["amount_inr"], merged["action"])
    ])
    recovered_amount = recovered * merged["amount_inr"].to_numpy()

    out = merged[[
        "case_id", "action", "amount_inr", "failure_reason",
        "payment_method", "merchant_category",
    ]].copy()
    out["recovery_probability_oracle"] = p
    out["recovered"] = recovered
    out["recovered_amount_inr"] = recovered_amount
    out["action_cost_inr"] = action_cost
    out["incentive_cost_inr"] = incentive_cost
    out["net_value_inr"] = recovered_amount - action_cost - incentive_cost
    return out


def summarize_policy(realized: pd.DataFrame, policy: str) -> dict:
    return {
        "policy": policy,
        "n_cases": int(len(realized)),
        "recovery_rate": float(realized["recovered"].mean()),
        "gross_recovered_inr": float(realized["recovered_amount_inr"].sum()),
        "total_action_cost_inr": float((realized["action_cost_inr"] + realized["incentive_cost_inr"]).sum()),
        "net_recovered_inr": float(realized["net_value_inr"].sum()),
        "actions_taken": int((realized["action"] != "NO_ACTION").sum()),
    }


def incremental_vs_control(summary: dict, control_summary: dict) -> float:
    """Incremental net value is measured against the same evaluated control batch."""
    return float(summary["net_recovered_inr"] - control_summary["net_recovered_inr"])


def calibration_report(y_true, y_prob, n_bins: int = 10) -> dict:
    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob)
    bins = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(y_prob, bins) - 1, 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        mask = idx == b
        if mask.any():
            rows.append({
                "bin": f"{bins[b]:.1f}-{bins[b + 1]:.1f}",
                "n": int(mask.sum()),
                "mean_predicted": float(y_prob[mask].mean()),
                "observed_rate": float(y_true[mask].mean()),
            })
    return {
        "brier_score": float(np.mean((y_prob - y_true) ** 2)),
        "bins": rows,
    }
