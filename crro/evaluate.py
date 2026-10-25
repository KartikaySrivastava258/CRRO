"""
Synthetic-simulation evaluation harness.

What is simulated vs. estimated (docs/DECISION_SCIENCE.md):
  * The hidden oracle (crro/oracle.py) defines TRUE P(recovery | x, action) in the
    synthetic world. It is used ONLY here, to realise outcomes of whatever action a
    policy chose and to compute simulator-only diagnostics (oracle uplift@k,
    regret). It is never a model feature and never selects deployed actions.
  * Common random numbers (CRN): case i gets one uniform u_i shared by all
    policies; recovered = u_i < p_true(i, a). This makes paired policy
    differences low-variance. It does NOT make the simulator more realistic and
    says nothing about real-world effect sizes.
  * Money is integer paise, priced with crro/economics.py -- the same functions
    the optimizer and the live engine use.
All outputs are SYNTHETIC SIMULATION values, never merchant revenue.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from .economics import action_cost_paise, incentive_discount_paise
from .oracle import true_p_recovery

SYNTHETIC_LABEL = "SYNTHETIC_SIMULATION"


def add_common_random_numbers(df, seed=777):
    out = df.copy()
    out["_u"] = np.random.default_rng(seed).uniform(0, 1, size=len(out))
    return out


def realize_policy_outcomes(test_df, assignment_df, shift=True, cost_multiplier=1.0):
    """Realise outcomes for each case under the policy's chosen action
    (missing case -> NO_ACTION). test_df must carry `_u`."""
    if "_u" not in test_df.columns:
        raise ValueError("test_df must carry common random numbers (_u); call add_common_random_numbers")
    m = test_df.merge(assignment_df[["case_id", "action"]], on="case_id", how="left")
    m["action"] = m["action"].fillna("NO_ACTION")
    acts = m["action"].to_numpy()
    p = true_p_recovery(m, acts, shift=shift)
    rec = (m["_u"].to_numpy() < p).astype(int)
    amt = np.round(m["amount_inr"].to_numpy(dtype=float) * 100).astype("int64")
    executed = acts != "NO_ACTION"
    cost = np.array([action_cost_paise(a, cost_multiplier) if e else 0 for a, e in zip(acts, executed)],
                    dtype="int64")
    disc = np.array([incentive_discount_paise(a, int(x)) if e else 0 for a, x, e in zip(acts, amt, executed)],
                    dtype="int64")
    out = m[["case_id", "action", "failure_reason", "payment_method", "merchant_category"]].copy()
    out["amount_paise"] = amt
    out["recovered"] = rec
    out["p_true"] = p
    out["gross_paise"] = rec * amt
    out["incentive_cost_paise"] = rec * disc
    out["action_cost_paise"] = cost
    out["net_paise"] = out["gross_paise"] - out["incentive_cost_paise"] - out["action_cost_paise"]
    out["expected_net_paise"] = p * (amt - disc) - cost      # oracle expectation (no sampling noise)
    out["requires_contact"] = [a not in ("NO_ACTION", "RETRY_NOW", "RETRY_LATER") for a in acts]
    out["incentive_exposure_paise"] = disc
    return out


def summarize_policy(r: pd.DataFrame, name: str) -> dict:
    n = len(r)
    return {
        "policy": name, "label": SYNTHETIC_LABEL, "n_cases": int(n),
        "recovery_rate": float(r["recovered"].mean()) if n else None,
        "gross_recovered_paise": int(r["gross_paise"].sum()),
        "action_cost_paise": int(r["action_cost_paise"].sum()),
        "incentive_cost_paise": int(r["incentive_cost_paise"].sum()),
        "net_value_paise": int(r["net_paise"].sum()),
        "expected_net_value_paise": float(r["expected_net_paise"].sum()),
        "actions_taken": int((r["action"] != "NO_ACTION").sum()),
        "contacts_used": int(r["requires_contact"].sum()),
        "incentive_exposure_paise": int(r["incentive_exposure_paise"].sum()),
    }


def incremental_vs_control(r: pd.DataFrame, control: pd.DataFrame) -> dict:
    """Paired by case_id (same cases, same CRN draws)."""
    a, c = r.set_index("case_id"), control.set_index("case_id")
    common = a.index.intersection(c.index)
    if len(common) != len(a) or len(common) != len(c):
        raise ValueError("policy and control must be evaluated on the same cases")
    a, c = a.loc[common], c.loc[common]
    return {
        "incremental_recovered_cases": int(a["recovered"].sum() - c["recovered"].sum()),
        "incremental_gross_paise": int(a["gross_paise"].sum() - c["gross_paise"].sum()),
        "incremental_net_paise": int(a["net_paise"].sum() - c["net_paise"].sum()),
        "incremental_expected_net_paise": float(a["expected_net_paise"].sum() - c["expected_net_paise"].sum()),
    }


def policy_comparison_table(realized: dict, control_name="CONTROL"):
    ctrl = realized[control_name]
    return pd.DataFrame([{**summarize_policy(r, n), **incremental_vs_control(r, ctrl)} for n, r in realized.items()])


def segment_performance(r: pd.DataFrame, control: pd.DataFrame, by="failure_reason") -> list:
    a = r.set_index("case_id")
    c = control.set_index("case_id").loc[a.index]
    seg = a[by] if by != "amount_band" else pd.cut(a["amount_paise"] / 100, [0, 1000, 5000, 20000, np.inf],
                                                    labels=["<1k", "1k-5k", "5k-20k", ">=20k"]).astype(str)
    rows = []
    for key, idx in a.groupby(seg, observed=True).groups.items():
        rows.append({"segment": str(key), "n": int(len(idx)),
                     "recovery_rate": float(a.loc[idx, "recovered"].mean()),
                     "control_recovery_rate": float(c.loc[idx, "recovered"].mean()),
                     "incremental_net_paise": int(a.loc[idx, "net_paise"].sum() - c.loc[idx, "net_paise"].sum()),
                     "actions_taken": int((a.loc[idx, "action"] != "NO_ACTION").sum())})
    return sorted(rows, key=lambda x: x["segment"])


def calibration_report(y_true, y_prob, n_bins=10) -> dict:
    y, p = np.asarray(y_true, dtype=float), np.asarray(y_prob, dtype=float)
    if len(y) == 0:
        return {"n": 0, "brier_score": None, "ece": None, "bins": []}
    if len(y) != len(p) or np.any((p < 0) | (p > 1)):
        raise ValueError("y_true/y_prob must be equal length with probabilities in [0, 1]")
    edges = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, n_bins - 1)
    bins, ece = [], 0.0
    for b in range(n_bins):
        mask = idx == b
        if not mask.any():
            continue
        mp, orate = float(p[mask].mean()), float(y[mask].mean())
        ece += mask.sum() / len(y) * abs(mp - orate)
        bins.append({"bin": f"{edges[b]:.1f}-{edges[b + 1]:.1f}", "n": int(mask.sum()),
                     "mean_predicted": round(mp, 4), "observed_rate": round(orate, 4)})
    return {"n": int(len(y)), "brier_score": round(float(np.mean((p - y) ** 2)), 5),
            "ece": round(float(ece), 5), "positive_rate": round(float(y.mean()), 4), "bins": bins}


def qini_rct(treated_mask, outcome, score, n_points=20) -> dict:
    """Standard Qini on RANDOMISED held-out data, using OBSERVED outcomes only.

    treated_mask: 1 if the row received the action, 0 if it received NO_ACTION
    (rows with other actions must be excluded by the caller). score: estimated
    uplift. Rows are ranked by score (desc; ties broken by original order, i.e.
    stable). Qini(k) = Y_t(k) - Y_c(k) * N_t(k) / N_c(k). The coefficient is the
    area between the Qini curve and the straight line to Qini(N), divided by N.
    This needs no oracle -- it is what one could compute from a real RCT log.
    """
    t = np.asarray(treated_mask, dtype=int)
    y = np.asarray(outcome, dtype=float)
    s = np.asarray(score, dtype=float)
    n = len(t)
    if n == 0 or t.sum() == 0 or (1 - t).sum() == 0:
        return {"n": int(n), "qini_coefficient": None, "curve": [], "note": "needs treated and control rows"}
    order = np.argsort(-s, kind="mergesort")
    t, y = t[order], y[order]
    nt, nc = np.cumsum(t), np.cumsum(1 - t)
    yt, yc = np.cumsum(y * t), np.cumsum(y * (1 - t))
    with np.errstate(divide="ignore", invalid="ignore"):
        q = np.where(nc > 0, yt - yc * nt / np.maximum(nc, 1), yt)
    q = np.r_[0.0, q]
    x = np.arange(n + 1)
    random_line = q[-1] * x / n
    coef = float(np.trapezoid(q - random_line, x) / n)
    pts = []
    for f in np.linspace(0.05, 1.0, n_points):
        k = max(1, int(round(n * f)))
        pts.append({"fraction": round(float(f), 3), "qini": round(float(q[k]), 3)})
    top = max(1, int(round(n * 0.2)))
    t20, y20 = t[:top], y[:top]
    obs_uplift20 = (float(y20[t20 == 1].mean() - y20[t20 == 0].mean())
                    if t20.sum() and (1 - t20).sum() else None)
    return {"n": int(n), "n_treated": int(t.sum()), "n_control": int((1 - t).sum()),
            "qini_coefficient": round(coef, 4), "observed_uplift_top20pct": obs_uplift20, "curve": pts}


def oracle_uplift_at_k(test_df, scored_df, k_fracs=(0.1, 0.2, 0.5), shift=True) -> dict:
    """SIMULATOR-ONLY diagnostic: mean TRUE uplift (oracle) of each case's
    model-best action, among the top-k fraction ranked by estimated uplift."""
    if len(scored_df) == 0:
        return {f"top_{int(f * 100)}pct": None for f in k_fracs}
    best = scored_df.sort_values(["estimated_uplift", "case_id"], ascending=[False, True]) \
        .drop_duplicates("case_id")
    m = best[["case_id", "action", "estimated_uplift"]].merge(test_df, on="case_id", how="left")
    true_u = (true_p_recovery(m, m["action"].to_numpy(), shift=shift)
              - true_p_recovery(m, np.full(len(m), "NO_ACTION", dtype=object), shift=shift))
    out = {}
    for f in k_fracs:
        k = max(1, int(len(m) * f))
        out[f"top_{int(f * 100)}pct"] = round(float(true_u[:k].mean()), 5)
    out["all"] = round(float(true_u.mean()), 5)
    return out


def mean_ci95(values) -> dict:
    """Mean and two-sided 95% t-interval across independent seeds."""
    a = np.asarray(values, dtype=float)
    n = len(a)
    if n == 0:
        return {"mean": None, "ci95_low": None, "ci95_high": None, "n": 0}
    mean = float(a.mean())
    if n < 2:
        return {"mean": mean, "ci95_low": None, "ci95_high": None, "n": 1, "sd": None}
    sd = float(a.std(ddof=1))
    h = float(stats.t.ppf(0.975, n - 1) * sd / np.sqrt(n))
    return {"mean": mean, "ci95_low": mean - h, "ci95_high": mean + h, "n": n, "sd": sd,
            "method": "student_t_across_seeds"}


def policy_value_curve(test_with_u, scored_df, budgets_factory, allocate_fn, contact_budgets, control_realized,
                       mode="greedy"):
    pts = []
    for c in contact_budgets:
        alloc, usage = allocate_fn(scored_df, budgets_factory(c), mode=mode)
        r = realize_policy_outcomes(test_with_u, alloc)
        pts.append({"contact_budget": c, **incremental_vs_control(r, control_realized),
                    "contacts_used": usage["contacts_used"], "solver_status": usage["status"]})
    return pts
