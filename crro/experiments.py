"""
Experiment protocol shared by scripts/benchmarks/policy_benchmark.py and the
API's synthetic optimizer runs.

Per seed s (independent draws):
  train = generate(n_train, seed=1000*s+1)                     -> model fit ONLY here
  test  = generate(n_test,  seed=1000*s+3, shift=True)         -> held-out, distribution-shifted
  opt-outs: random 3% of test customers (seeded)               -> same feasible sets for all policies
  CRN:  u_i ~ U(0,1) seeded 1000*s+7                           -> shared by all policies
Budgets are identical across policies within a seed. The oracle is used only to
realise outcomes and for simulator-only ceilings/diagnostics.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from .baselines import (control_policy, fixed_retry_policy, historical_best_action_policy,
                        oracle_constrained_policy, oracle_unconstrained_policy, raw_probability_policy,
                        rule_based_policy, uplift_greedy_policy, uplift_unconstrained_policy)
from .data_generator import eligible_actions_for, generate_dataset
from .evaluate import (add_common_random_numbers, calibration_report, incremental_vs_control,
                       oracle_uplift_at_k, qini_rct, realize_policy_outcomes, segment_performance,
                       summarize_policy)
from .feasibility import compute_feasible_actions
from .model import FEATURES, make_model
from .optimizer import Budgets, check_feasibility, compute_expected_incremental_value, exact_allocate, \
    greedy_allocate
from .state import CustomerStateStore

DEPLOYABLE = ["FIXED_RETRY", "RULE_BASED", "HISTORICAL_BEST_ACTION", "RAW_PROBABILITY", "UPLIFT_GREEDY",
              "CRRO_EXACT"]


def default_budgets(n_test: int) -> Budgets:
    """Scarce capacity: contacts for 10% of cases, ₹5 of incentive exposure per case."""
    return Budgets(daily_contacts=max(1, n_test // 10), daily_incentive_inr=5.0 * n_test,
                   per_action_daily_cap={"ALTERNATIVE_PAYMENT_METHOD": max(1, n_test // 25)},
                   per_customer_daily_contact_cap=1)


def prepare(seed: int, n_train: int, n_test: int, shift=True, opt_out_rate=0.03, model=None,
            model_kind: str = "pooled"):
    t0 = time.perf_counter()
    train = generate_dataset(n_train, seed=1000 * seed + 1) if model is None else None
    test = generate_dataset(n_test, seed=1000 * seed + 3, shift=shift)
    test["eligible_actions"] = test.apply(eligible_actions_for, axis=1)
    rng = np.random.default_rng(1000 * seed + 5)
    custs = test["customer_id"].unique()
    opted = set(rng.choice(custs, size=int(opt_out_rate * len(custs)), replace=False)) if len(custs) else set()
    test = compute_feasible_actions(test, "eligible_actions", CustomerStateStore(opted_out_customers=opted))
    fit_ms = None
    if model is None:
        t1 = time.perf_counter()
        model = make_model(model_kind).fit(train)
        fit_ms = round((time.perf_counter() - t1) * 1000, 1)
    t2 = time.perf_counter()
    scored = compute_expected_incremental_value(model.score_all_actions(test, "feasible_actions"), test)
    score_ms = round((time.perf_counter() - t2) * 1000, 1)
    test = add_common_random_numbers(test, seed=1000 * seed + 7)
    return {"seed": seed, "train": train, "test": test, "model": model, "scored": scored,
            "opted_out": opted, "timings_ms": {"fit": fit_ms, "score": score_ms,
                                                "prepare_total": round((time.perf_counter() - t0) * 1000, 1)}}


def run_policies(ctx, budgets: Budgets, include_oracle=True, cost_multiplier=1.0, include_segments=False):
    test, scored = ctx["test"], ctx["scored"]
    if cost_multiplier != 1.0:
        scored = compute_expected_incremental_value(scored[["case_id", "action", "recovery_probability",
                                                            "baseline_probability"]], test, cost_multiplier)
    policies, usage = {"CONTROL": control_policy(test)}, {}
    policies["FIXED_RETRY"] = fixed_retry_policy(test, budgets)
    policies["RULE_BASED"] = rule_based_policy(test, budgets)
    if ctx.get("train") is not None:
        policies["HISTORICAL_BEST_ACTION"], _ = historical_best_action_policy(test, ctx["train"], budgets)
    policies["RAW_PROBABILITY"], usage["RAW_PROBABILITY"] = raw_probability_policy(scored, budgets)
    policies["UPLIFT_GREEDY"], usage["UPLIFT_GREEDY"] = uplift_greedy_policy(scored, budgets)
    exact_alloc, usage["CRRO_EXACT"] = exact_allocate(scored, budgets)
    policies["CRRO_EXACT"] = exact_alloc[["case_id", "action"]]
    policies["UPLIFT_UNCONSTRAINED (diagnostic)"] = uplift_unconstrained_policy(scored)
    if include_oracle:
        policies["ORACLE_CONSTRAINED (simulator-only)"], usage["ORACLE_CONSTRAINED"] = \
            oracle_constrained_policy(test, budgets)
        policies["ORACLE_UNCONSTRAINED (simulator-only)"] = oracle_unconstrained_policy(test)
    realized = {k: realize_policy_outcomes(test, v, cost_multiplier=cost_multiplier) for k, v in policies.items()}
    ctrl = realized["CONTROL"]
    customer_of = test.set_index("case_id")["customer_id"]
    rows = []
    for name, r in realized.items():
        row = {**summarize_policy(r, name), **incremental_vs_control(r, ctrl)}
        constrained = "UNCONSTRAINED" not in name
        alloc_view = r.assign(requires_customer_contact=r["requires_contact"],
                              incentive_discount_paise=r["incentive_exposure_paise"])
        row["constrained"] = constrained
        row["feasibility_violations"] = check_feasibility(alloc_view, budgets, customer_of) if constrained else None
        rows.append(row)
    reasons = exact_alloc.loc[exact_alloc["action"] == "NO_ACTION", "rejected_reason"].map(
        lambda x: x.split(":")[0] if isinstance(x, str) else x).value_counts().to_dict()
    blocked_detail = exact_alloc.loc[exact_alloc["rejected_reason"].fillna("").str.startswith("BLOCKED_BY"),
                                     "rejected_reason"].value_counts().to_dict()
    out = {"policies": rows, "solver_usage": usage, "crro_no_action_reasons": reasons,
           "crro_blocked_by": blocked_detail,
           "binding_constraints": _binding(usage["CRRO_EXACT"], budgets, exact_alloc),
           "exact_vs_greedy": {
               "exact_objective_paise": usage["CRRO_EXACT"]["objective_expected_value_paise"],
               "greedy_objective_paise": usage["UPLIFT_GREEDY"]["objective_expected_value_paise"],
               "exact_status": usage["CRRO_EXACT"]["status"], "exact_solver": usage["CRRO_EXACT"]["solver"],
               "exact_wall_ms": usage["CRRO_EXACT"]["wall_time_ms"],
               "greedy_wall_ms": usage["UPLIFT_GREEDY"]["wall_time_ms"]}}
    if include_segments:
        crro = realized["CRRO_EXACT"]
        out["crro_segments"] = {by: segment_performance(crro, ctrl, by)
                                for by in ("failure_reason", "payment_method", "merchant_category", "amount_band")}
    out["_allocation"] = exact_alloc
    return out


def _binding(u, b: Budgets, alloc):
    """A constraint is reported binding iff at least one case with a positive-value
    candidate was left at NO_ACTION because that constraint blocked it."""
    blocked = alloc["rejected_reason"].fillna("")
    return {"contact_budget": {"used": u["contacts_used"], "limit": b.daily_contacts,
                               "binding": bool(blocked.str.contains("CONTACT_BUDGET").any())},
            "incentive_budget": {"used_paise": u["incentive_committed_paise"], "limit_paise": b.incentive_paise,
                                 "binding": bool(blocked.str.contains("INCENTIVE_BUDGET").any())},
            "action_caps": {"binding": bool(blocked.str.contains("ACTION_CAP").any())},
            "customer_contact_cap": {"binding": bool(blocked.str.contains("CUSTOMER_CONTACT_CAP").any())}}


def model_quality(ctx, shift_label="shifted_holdout"):
    """Model diagnostics on the held-out RCT test set (factual outcomes)."""
    test, model, scored = ctx["test"], ctx["model"], ctx["scored"]
    out = {"evaluated_on": shift_label, "n_test": int(len(test)), "per_action": []}
    for a in model.actions:
        sub = test[test["assigned_action"] == a]
        if len(sub) == 0:
            continue
        cal = calibration_report(sub["outcome_recovered"].to_numpy(), model.predict_proba(sub, a))
        out["per_action"].append({"action": a, "n": cal["n"], "brier": cal["brier_score"], "ece": cal["ece"],
                                  "positive_rate": cal["positive_rate"]})
    ctrl = test[test["assigned_action"] == "NO_ACTION"]
    out["control_calibration"] = calibration_report(ctrl["outcome_recovered"].to_numpy(),
                                                    model.predict_proba(ctrl, "NO_ACTION"))
    qini = {}
    p0_all = model.predict_proba(test, "NO_ACTION")
    for a in [x for x in model.actions if x != "NO_ACTION"]:
        mask = test["assigned_action"].isin([a, "NO_ACTION"]).to_numpy()
        sub = test[mask]
        if len(sub) == 0:
            continue
        score = model.predict_proba(sub, a) - p0_all[mask]
        qini[a] = qini_rct((sub["assigned_action"] == a).to_numpy(), sub["outcome_recovered"].to_numpy(), score)
        qini[a].pop("curve")
    out["qini_rct_observed"] = qini
    out["oracle_uplift_at_k (simulator-only)"] = oracle_uplift_at_k(test, scored)
    out["features"] = list(FEATURES)
    return out
