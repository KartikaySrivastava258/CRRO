"""Simulator-only causal diagnostics. `_case_truth` previously passed a pandas
row (Series) into the vectorised oracle and crashed; it is now vectorised."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .oracle import true_p_recovery


def _case_truth(test, eligible_col="eligible_actions", shift=True):
    df = test.reset_index(drop=True)
    base = true_p_recovery(df, np.full(len(df), "NO_ACTION", dtype=object), shift=shift)
    best = np.zeros(len(df))
    actions = sorted({a for lst in df[eligible_col] for a in lst if a != "NO_ACTION"})
    for a in actions:
        mask = np.fromiter((a in lst for lst in df[eligible_col]), dtype=bool, count=len(df))
        if mask.any():
            u = true_p_recovery(df[mask], np.full(int(mask.sum()), a, dtype=object), shift=shift) - base[mask]
            best[mask] = np.maximum(best[mask], u)
    return pd.DataFrame({"case_id": df["case_id"], "true_base": base, "true_best_uplift": best})


def policy_regret(policy_df, test, eligible_col="eligible_actions", shift=True):
    """Per-case regret (paise, oracle expectation) of a policy vs the per-case best
    TRUE uplift, ignoring costs. Simulator-only diagnostic."""
    truth = _case_truth(test, eligible_col, shift).set_index("case_id")
    m = test.merge(policy_df[["case_id", "action"]], on="case_id", how="left").fillna({"action": "NO_ACTION"})
    chosen = true_p_recovery(m, m["action"].to_numpy(), shift=shift) - truth.loc[m["case_id"], "true_base"].to_numpy()
    reg = (truth.loc[m["case_id"], "true_best_uplift"].to_numpy() - chosen) * m["amount_inr"].to_numpy() * 100
    return {"total_regret_paise": float(reg.sum()), "mean_regret_paise": float(reg.mean()) if len(reg) else None,
            "zero_regret_rate": float((np.abs(reg) < 1e-6).mean()) if len(reg) else None}
