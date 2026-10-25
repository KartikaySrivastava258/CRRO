"""Budget / action response curves (synthetic simulation). Previously crashed
(wrong score_all_actions signature, unknown `incentive_pct` kwarg); now built on
the shared experiment protocol so economics match the optimizer and evaluator."""
from __future__ import annotations

import pandas as pd

from .action_registry import ACTION_REGISTRY
from .evaluate import incremental_vs_control, realize_policy_outcomes, summarize_policy
from .experiments import prepare, run_policies
from .optimizer import Budgets


def budget_response_curve(seed=2026, n=2000, contact_budgets=(50, 100, 200, 400),
                          incentive_budgets=(2500, 10000, 40000), n_train=20000, ctx=None):
    ctx = ctx or prepare(seed, n_train, n)
    rows = []
    for c in contact_budgets:
        for inc in incentive_budgets:
            r = run_policies(ctx, Budgets(int(c), float(inc)), include_oracle=False)
            crro = next(p for p in r["policies"] if p["policy"] == "CRRO_EXACT")
            rows.append({"contact_budget": int(c), "incentive_budget_inr": float(inc),
                         "incremental_expected_net_paise": crro["incremental_expected_net_paise"],
                         "incremental_net_paise": crro["incremental_net_paise"],
                         "contacts_used": crro["contacts_used"], "status": r["solver_usage"]["CRRO_EXACT"]["status"]})
    return pd.DataFrame(rows)


def action_response_curve(seed=2026, n=2000, n_train=20000, ctx=None):
    """Each action applied to every case where it is feasible vs. NO_ACTION on the same cases (CRN)."""
    ctx = ctx or prepare(seed, n_train, n)
    test = ctx["test"]
    rows = []
    for a in [x for x in ACTION_REGISTRY if x != "NO_ACTION"]:
        cohort = test[test["feasible_actions"].map(lambda xs: a in xs)]
        if cohort.empty:
            continue
        treated = realize_policy_outcomes(cohort, pd.DataFrame({"case_id": cohort.case_id, "action": a}))
        control = realize_policy_outcomes(cohort, pd.DataFrame({"case_id": cohort.case_id, "action": "NO_ACTION"}))
        rows.append({"action": a, "eligible_cases": len(cohort), **summarize_policy(treated, a),
                     **incremental_vs_control(treated, control)})
    return pd.DataFrame(rows).sort_values("incremental_expected_net_paise", ascending=False).reset_index(drop=True)
