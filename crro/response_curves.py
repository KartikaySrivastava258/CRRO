import numpy as np
import pandas as pd

from .action_registry import ACTION_REGISTRY
from .data_generator import generate_dataset
from .model import UpliftModel
from .optimizer import Budgets, compute_expected_incremental_value, exact_allocate
from .evaluation import add_common_random_numbers, realize_policy_outcomes, summarize_policy


def _prepare(seed=2026, n=2000):
    train = generate_dataset(n=n, seed=seed)
    test = generate_dataset(n=n, seed=seed + 1, shift=True)
    model = UpliftModel().fit(train)
    scored = model.score_all_actions(test).copy()
    scored["customer_id"] = test["customer_id"].to_numpy()[scored.index]
    economics = compute_expected_incremental_value(
        scored,
        test[["case_id", "amount_inr"]],
        incentive_pct=0.02,
    )
    economics["customer_id"] = scored["customer_id"].to_numpy()
    return test, economics


def budget_response_curve(
    seed=2026,
    n=2000,
    contact_budgets=(50, 100, 200, 400, 800),
    incentive_budgets=(2500, 5000, 10000, 20000, 40000),
):
    """Evaluate CRRO as scarce recovery capacity changes.

    The test population and random uniforms are held fixed across every point,
    so movement in the curve is attributable to the resource constraint rather
    than a new random sample.
    """
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
            rows.append({
                "contact_budget": int(contacts),
                "incentive_budget_inr": float(incentive_budget),
                "net_recovered_inr": summary["net_recovered_inr"],
                "gross_recovered_inr": summary["gross_recovered_inr"],
                "recovery_rate": summary["recovery_rate"],
                "actions_taken": summary["actions_taken"],
                "contacts_used": int(usage["contacts_used"]),
                "incentive_used_inr": float(usage["incentive_used_inr"]),
                "solver": usage.get("solver", "unknown"),
                "objective_value": float(usage.get("objective_value", 0.0)),
            })
    return pd.DataFrame(rows)


def action_response_curve(seed=2026, n=2000):
    """Compare each action's standalone response on the same held-out cases."""
    test, scored = _prepare(seed, n)
    test = add_common_random_numbers(test, seed=seed + 199)
    actions = [a for a in ACTION_REGISTRY if a != "NO_ACTION"]
    rows = []
    control = pd.DataFrame({"case_id": test.case_id, "action": "NO_ACTION"})
    control_realized = realize_policy_outcomes(test, control, shift=True)
    control_summary = summarize_policy(control_realized, "CONTROL")
    control_net = control_summary["net_recovered_inr"]

    for action in actions:
        eligible = test[test["eligible_actions"].map(lambda xs: action in xs)].copy()
        assignment = pd.DataFrame({"case_id": eligible.case_id, "action": action})
        realized = realize_policy_outcomes(test, assignment, shift=True)
        summary = summarize_policy(realized, action)
        action_cost = realized.loc[realized.action == action, "action_cost_inr"].sum()
        incentive_cost = realized.loc[realized.action == action, "incentive_cost_inr"].sum()
        rows.append({
            "action": action,
            "eligible_cases": int(len(eligible)),
            "recovered_cases": int(realized.recovered.sum()),
            "recovery_rate": summary["recovery_rate"],
            "gross_recovered_inr": summary["gross_recovered_inr"],
            "action_cost_inr": round(float(action_cost), 2),
            "incentive_cost_inr": round(float(incentive_cost), 2),
            "net_recovered_inr": summary["net_recovered_inr"],
            "incremental_net_vs_control_inr": round(float(summary["net_recovered_inr"] - control_net), 2),
            "mean_estimated_uplift": round(float(scored.loc[scored.action == action, "uplift"].mean()), 6) if "uplift" in scored else np.nan,
        })
    return pd.DataFrame(rows).sort_values("net_recovered_inr", ascending=False).reset_index(drop=True)
