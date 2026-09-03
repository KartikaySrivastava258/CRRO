import numpy as np
import pandas as pd

from .action_registry import ACTION_REGISTRY
from .data_generator import generate_dataset, eligible_actions_for
from .model import UpliftModel
from .optimizer import Budgets, compute_expected_incremental_value, exact_allocate
from .evaluation import add_common_random_numbers, realize_policy_outcomes, summarize_policy


def _prepare(seed=2026, n=2000):
    train = generate_dataset(n=max(10000, n), seed=seed)
    test = generate_dataset(n=n, seed=seed + 1, shift=True)
    for d in (train, test):
        d["eligible_actions"] = d.apply(eligible_actions_for, axis=1)
    model = UpliftModel().fit(train)
    scored = model.score_all_actions(test).copy()
    customer_map = test.set_index("case_id")["customer_id"]
    scored["customer_id"] = scored["case_id"].map(customer_map)
    economics = compute_expected_incremental_value(
        scored, test[["case_id", "amount_inr"]], incentive_pct=0.02
    )
    economics["customer_id"] = economics["case_id"].map(customer_map)
    return test, economics


def budget_response_curve(
    seed=2026,
    n=2000,
    contact_budgets=(50, 100, 200, 400, 800),
    incentive_budgets=(2500, 5000, 10000, 20000, 40000),
):
    """Measure portfolio value as recovery capacity changes using common random numbers."""
    test, scored = _prepare(seed, n)
    test = add_common_random_numbers(test, seed=seed + 99)
    rows = []
    for contacts in contact_budgets:
        for incentive_budget in incentive_budgets:
            allocation, usage = exact_allocate(
                scored,
                Budgets(
                    daily_contacts=int(contacts),
                    daily_incentive_inr=float(incentive_budget),
                ),
            )
            realized = realize_policy_outcomes(test, allocation, shift=True)
            summary = summarize_policy(realized, "CRRO")
            rows.append(
                {
                    "contact_budget": int(contacts),
                    "incentive_budget_inr": float(incentive_budget),
                    "net_recovered_inr": summary["net_recovered_inr"],
                    "gross_recovered_inr": summary["gross_recovered_inr"],
                    "recovery_rate": summary["recovery_rate"],
                    "actions_taken": summary["actions_taken"],
                    "contacts_used": int(usage["contacts_used"]),
                    "incentive_used_inr": float(usage["incentive_used_inr"]),
                    "solver": usage.get("solver", "unknown"),
                    "objective_value": float(
                        usage.get("objective_inr", usage.get("objective_value", 0.0))
                    ),
                }
            )
    return pd.DataFrame(rows)


def action_response_curve(seed=2026, n=2000):
    """Compare each action against a matched NO_ACTION cohort."""
    test, scored = _prepare(seed, n)
    test = add_common_random_numbers(test, seed=seed + 199)
    rows = []
    for action in [a for a in ACTION_REGISTRY if a != "NO_ACTION"]:
        cohort = test[test["eligible_actions"].map(lambda xs: action in xs)].copy()
        if cohort.empty:
            continue
        assignment = pd.DataFrame({"case_id": cohort.case_id, "action": action})
        control = pd.DataFrame({"case_id": cohort.case_id, "action": "NO_ACTION"})
        realized = realize_policy_outcomes(cohort, assignment, shift=True)
        control_realized = realize_policy_outcomes(cohort, control, shift=True)
        summary = summarize_policy(realized, action)
        control_summary = summarize_policy(control_realized, "CONTROL")
        action_scores = scored.loc[scored.action == action, "estimated_uplift"]
        mean_uplift = float(action_scores.mean()) if not action_scores.empty else np.nan
        rows.append(
            {
                "action": action,
                "eligible_cases": int(len(cohort)),
                "recovered_cases": int(realized.recovered.sum()),
                "control_recovered_cases": int(control_realized.recovered.sum()),
                "recovery_rate": summary["recovery_rate"],
                "control_recovery_rate": control_summary["recovery_rate"],
                "gross_recovered_inr": summary["gross_recovered_inr"],
                "action_cost_inr": round(float(realized.action_cost_inr.sum()), 2),
                "incentive_cost_inr": round(float(realized.incentive_cost_inr.sum()), 2),
                "net_recovered_inr": summary["net_recovered_inr"],
                "control_net_recovered_inr": control_summary["net_recovered_inr"],
                "incremental_net_vs_control_inr": round(
                    float(summary["net_recovered_inr"] - control_summary["net_recovered_inr"]),
                    2,
                ),
                "mean_estimated_uplift": round(mean_uplift, 6)
                if pd.notna(mean_uplift)
                else np.nan,
            }
        )
    return pd.DataFrame(rows).sort_values(
        "incremental_net_vs_control_inr", ascending=False
    ).reset_index(drop=True)
