"""
Feasibility Layer (Required Changes, Section 7)
----------------------------------------------------
Guardrails must constrain the OPTIMIZER's search space, not just reject
its output afterward. This module computes, for every case, the set of
actions that are feasible BEFORE optimization runs:

  Case -> base dataset eligibility (amount / attempt-count rules)
       -> minus actions blocked by customer opt-out
       -> minus actions blocked by persistent cooldown / max-attempts
          (via CustomerStateStore)
       = feasible_actions(case)

The optimizer then only ever considers (case, action) pairs drawn from
this feasible set. A defense-in-depth policy check still runs after
optimization (crro/policy.py) because state can change between
optimization and execution (Section 7: "state can change between
optimization and execution").
"""

from .action_registry import ACTION_REGISTRY


def compute_feasible_actions(cases_df, eligible_col, state_store):
    """
    Returns cases_df with a new column `feasible_actions`: the base
    eligible-action list minus anything blocked by opt-out/cooldown/
    max-attempts at THIS point in time, per the state store.
    """
    def _feasible(row):
        base = list(row[eligible_col])
        cid = row["customer_id"]
        case_id = row["case_id"]
        out = []
        for a in base:
            if a == "NO_ACTION":
                out.append(a)
                continue
            spec = ACTION_REGISTRY[a]
            if spec.requires_customer_contact and state_store.is_opted_out(cid):
                continue
            if state_store.attempts_used(case_id) >= spec.max_attempts:
                continue
            if state_store.in_cooldown(cid, a, spec.min_cooldown_minutes):
                continue
            out.append(a)
        if "NO_ACTION" not in out:
            out.append("NO_ACTION")
        return out

    cases_df = cases_df.copy()
    cases_df["feasible_actions"] = cases_df.apply(_feasible, axis=1)
    return cases_df
