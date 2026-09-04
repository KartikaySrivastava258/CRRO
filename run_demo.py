"""
CRRO End-to-End Demo Pipeline v2
===================================
Implements the P0 fixes from CRRO_Project_Required_Changes_1.md:
  4. Real NO_ACTION control outcome + incremental-value metrics
  5. Four additional baselines (historical, raw-probability, uplift-only,
     oracle upper bound)
  6. OR-Tools exact portfolio optimizer (greedy kept as fallback/comparison)
  7. Guardrails moved into the pre-optimization feasibility layer
  8. Persistent-shaped customer state (cooldown / max-attempts / opt-out)
  9. Offer registry (no more hard-coded 2% incentive cost)
 10. Expanded economic value components
 11. AUUC / Qini / uplift@k model-quality metrics
 12. Policy-value curve (recovered value vs. contact budget)
 19. Multi-seed statistical confidence interval on the headline number

Run: python3 run_demo.py
"""

import json
import time
import numpy as np
import pandas as pd

from crro.data_generator import generate_dataset, eligible_actions_for
from crro.model import UpliftModel
from crro.optimizer import Budgets, compute_expected_incremental_value, allocate, greedy_allocate
from crro.policy import evaluate_policy
from crro.feasibility import compute_feasible_actions
from crro.state import CustomerStateStore
from crro.executor import SimulatedExecutionAdapter, AuditLog, run_execution
from crro.baselines import (control_policy, fixed_retry_policy, rule_based_policy,
                             historical_best_action_policy, raw_probability_maximizer_policy,
                             uplift_only_no_portfolio_policy, oracle_upper_bound_policy)
from crro.evaluate import (add_common_random_numbers, realize_policy_outcomes,
                            policy_comparison_table, segment_performance, calibration_report,
                            uplift_at_k, auuc_qini, policy_value_curve, mean_ci95)

SEED = 2026
OPTIMIZER_MODE = "exact"   # "exact" (OR-Tools CP-SAT) or "greedy"


def timed(label, fn, *args, **kwargs):
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    dt_ms = (time.perf_counter() - t0) * 1000
    print(f"[{dt_ms:8.1f} ms] {label}")
    return result, dt_ms


def main():
    report = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
              "optimizer_mode": OPTIMIZER_MODE}
    latencies = {}

    print("=== Generating synthetic RCT datasets ===")
    train_df, _ = timed("generate train (60,000)", generate_dataset, 60000, SEED + 1)
    val_df, _ = timed("generate val (10,000)", generate_dataset, 10000, SEED + 2)
    test_df, _ = timed("generate held-out test (20,000, distribution shift)",
                        generate_dataset, 20000, SEED + 3, shift=True)

    for df in (train_df, val_df, test_df):
        df["eligible_actions"] = df.apply(eligible_actions_for, axis=1)

    report["dataset"] = {
        "train_cases": len(train_df), "val_cases": len(val_df), "test_cases": len(test_df),
        "held_out_test_has_distribution_shift": True,
    }

    print("=== Training uplift model (T-learner, calibrated) ===")
    model = UpliftModel()
    _, fit_ms = timed("fit uplift model on train", model.fit, train_df)
    latencies["model_fit_ms"] = fit_ms

    val_scored = model.score_all_actions(val_df, "eligible_actions")
    val_p_control_rows_df = val_df.loc[val_df["assigned_action"] == "NO_ACTION"]
    p_for_control_rows = model.predict_proba(val_p_control_rows_df, "NO_ACTION")
    report["calibration_control_action_validation_set"] = calibration_report(
        val_p_control_rows_df["outcome_recovered"].values, p_for_control_rows
    )

    print("=== Scoring held-out test set (counterfactual + uplift) ===")
    t0 = time.perf_counter()
    test_scored = model.score_all_actions(test_df, "eligible_actions")
    test_scored = compute_expected_incremental_value(test_scored, test_df)
    scoring_ms = (time.perf_counter() - t0) * 1000
    latencies["opportunity_evaluation_batch_ms_20000_cases"] = round(scoring_ms, 2)

    print("=== Building pre-optimization feasibility layer (guardrails-first) ===")
    rng = np.random.default_rng(SEED + 9)
    all_customers = test_df["customer_id"].unique()
    opted_out = set(rng.choice(all_customers, size=int(0.03 * len(all_customers)), replace=False))
    state_store = CustomerStateStore(opted_out_customers=opted_out)

    test_df_feas = compute_feasible_actions(test_df, "eligible_actions", state_store)
    feasible_lookup = test_df_feas.set_index("case_id")["feasible_actions"]
    test_scored_feasible = test_scored[
        test_scored.apply(lambda r: r["action"] in feasible_lookup.loc[r["case_id"]], axis=1)
    ].copy()

    report["feasibility"] = {
        "customers_opted_out": len(opted_out),
        "candidate_case_action_pairs_before_feasibility": int(len(test_scored)),
        "candidate_case_action_pairs_after_feasibility": int(len(test_scored_feasible)),
    }

    print(f"=== Running constrained portfolio optimizer (mode={OPTIMIZER_MODE}) ===")
    budgets = Budgets(
        daily_contacts=4000,
        daily_incentive_inr=100000,
        per_action_daily_cap={"ALTERNATIVE_PAYMENT_METHOD": 1000},
        per_customer_daily_contact_cap=1,
    )
    t0 = time.perf_counter()
    crro_allocation, usage = allocate(test_scored_feasible, budgets, mode=OPTIMIZER_MODE,
                                       time_limit_seconds=25)
    opt_ms = (time.perf_counter() - t0) * 1000
    latencies["portfolio_optimization_latency_ms_20000_cases"] = round(opt_ms, 2)
    report["budget_usage"] = usage
    print(f"    optimizer status: {usage.get('status')}, solver: {usage.get('solver')}, "
          f"wall_time={usage.get('wall_time_seconds')}s")

    greedy_alloc, greedy_usage = greedy_allocate(test_scored_feasible, budgets)
    report["greedy_vs_exact_optimizer_objective_inr"] = {
        "greedy_total_expected_value_inr": round(
            greedy_alloc.loc[greedy_alloc["action"] != "NO_ACTION",
                              "expected_incremental_value_inr"].sum(), 2),
        "exact_total_expected_value_inr": usage.get("objective_value_inr"),
        "exact_best_bound_inr": usage.get("best_objective_bound_inr"),
    }

    print("=== Applying defense-in-depth policy / guardrail gate ===")
    cases_idx = test_df.set_index("case_id")
    policy_decisions = {}
    final_actions = []
    for _, row in crro_allocation.iterrows():
        cid = row["case_id"]
        action = row["action"]
        case_row = cases_idx.loc[cid]
        decision = evaluate_policy(
            case_row, action,
            expected_incremental_value=row.get("expected_incremental_value_inr"),
            customer_state={"attempts_used": 0, "contacts_used": 0},
            opted_out_customers=opted_out,
        )
        policy_decisions[cid] = decision
        if action != "NO_ACTION" and not decision.approved:
            final_actions.append({"case_id": cid, "action": "NO_ACTION", "reason": decision.reason_code})
        else:
            final_actions.append({"case_id": cid, "action": action, "reason": None})
        state_store.record(case_row["customer_id"], cid, action,
                            status="PROPOSED" if action != "NO_ACTION" else "NOT_PROPOSED")

    final_action_df = pd.DataFrame(final_actions)
    n_blocked = int((final_action_df["reason"].notna()).sum())
    report["guardrails"] = {
        "customers_opted_out_in_batch": len(opted_out),
        "actions_blocked_by_defense_in_depth_gate": n_blocked,
        "note": ("Feasibility was already enforced BEFORE optimization "
                 "(Section 7); this second gate exists because state can "
                 "change between optimization and execution, and caught "
                 f"{n_blocked} cases this run."),
        "block_reason_breakdown": final_action_df["reason"].value_counts(dropna=True).to_dict(),
    }

    print("=== Executing (simulated Razorpay test-mode adapter) + audit trail ===")
    adapter = SimulatedExecutionAdapter(seed=SEED + 11)
    audit = AuditLog()
    exec_alloc = crro_allocation.merge(
        final_action_df[["case_id", "action"]].rename(columns={"action": "final_action"}),
        on="case_id"
    )
    exec_alloc["action"] = exec_alloc["final_action"]
    exec_results, exec_ms = timed(
        "execute allocated actions", run_execution,
        exec_alloc, test_df, adapter, audit, policy_decisions, "uplift_tlearner_v1"
    )
    latencies["action_execution_batch_ms"] = round(exec_ms, 2)

    dup_audit = AuditLog()
    dup_results = run_execution(exec_alloc, test_df, adapter, dup_audit, policy_decisions, "uplift_tlearner_v1")
    exec_status_counts = exec_results["exec_status"].value_counts().to_dict()
    attempted_mask = exec_results["exec_status"].isin(["EXECUTED", "PROVIDER_ERROR", "EXECUTION_TIMEOUT"])
    dup_within_attempted = (
        dup_results.loc[attempted_mask, "exec_status"] == "SKIPPED_DUPLICATE"
    ).mean() if attempted_mask.sum() else None
    report["execution_reliability"] = {
        "exec_status_breakdown": exec_status_counts,
        "duplicate_action_rate_within_previously_attempted_subset": (
            round(float(dup_within_attempted), 4) if dup_within_attempted is not None else None
        ),
        "event_processing_success_rate": round(
            float((exec_results["exec_status"].isin(["EXECUTED", "NOT_EXECUTED"])).mean()), 4
        ),
    }

    audit_df = audit.to_df()
    audit_df.to_csv("outputs/audit_log.csv", index=False)

    print("=== Evaluating CRRO against explicit baselines (real NO_ACTION control) ===")
    test_with_u = add_common_random_numbers(test_df, seed=SEED + 21)

    control_alloc = control_policy(test_with_u)
    fixed_alloc = fixed_retry_policy(test_with_u, budgets=budgets, opted_out=opted_out)
    rule_alloc = rule_based_policy(test_with_u, budgets=budgets, opted_out=opted_out)
    hist_alloc, hist_rates = historical_best_action_policy(test_with_u, train_df, budgets=budgets,
                                                             opted_out=opted_out)
    raw_prob_alloc = raw_probability_maximizer_policy(test_scored)
    uplift_only_alloc = uplift_only_no_portfolio_policy(test_scored)

    raw_prob_value_df = test_scored_feasible.copy()
    raw_prob_value_df["expected_incremental_value_inr"] = (
        raw_prob_value_df["recovery_probability"] * raw_prob_value_df["amount_inr"]
        - raw_prob_value_df["action_cost_inr"] - raw_prob_value_df["contact_cost_inr"]
        - raw_prob_value_df["incentive_cost_inr"]
    )
    raw_prob_constrained_alloc, raw_prob_constrained_usage = greedy_allocate(raw_prob_value_df, budgets)

    oracle_alloc, _ = timed("oracle upper-bound policy (vectorized)",
                             oracle_upper_bound_policy, test_with_u, "eligible_actions", True)
    crro_alloc_final = final_action_df[["case_id", "action"]]

    policies = {
        "CONTROL (no action)": control_alloc,
        "FIXED_RETRY": fixed_alloc,
        "RULE_BASED": rule_alloc,
        "HISTORICAL_BEST_ACTION": hist_alloc,
        "RAW_PROBABILITY_MAXIMIZER (unconstrained, diagnostic only)": raw_prob_alloc,
        "RAW_PROBABILITY_MAXIMIZER (budget-constrained, deployable)":
            raw_prob_constrained_alloc[["case_id", "action"]],
        "UPLIFT_ONLY_NO_PORTFOLIO_CONSTRAINT (unconstrained, diagnostic only)": uplift_only_alloc,
        "CRRO (uplift + portfolio optimizer + guardrails)": crro_alloc_final,
        "ORACLE_UPPER_BOUND (not deployable — benchmark ceiling)": oracle_alloc,
    }

    realized_frames = {name: realize_policy_outcomes(test_with_u, alloc, shift=True)
                        for name, alloc in policies.items()}
    comparison_df = policy_comparison_table(realized_frames, control_name="CONTROL (no action)")
    report["policy_comparison"] = comparison_df.to_dict(orient="records")
    report["historical_action_rates_used_by_baseline_a"] = hist_rates
    report["raw_probability_constrained_budget_usage"] = raw_prob_constrained_usage

    crro_realized = realized_frames["CRRO (uplift + portfolio optimizer + guardrails)"]
    report["crro_segment_performance_by_failure_reason"] = segment_performance(
        crro_realized, "failure_reason").to_dict(orient="records")
    report["crro_segment_performance_by_payment_method"] = segment_performance(
        crro_realized, "payment_method").to_dict(orient="records")

    print("=== Computing AUUC / Qini / uplift@k ===")
    report["uplift_at_k"] = uplift_at_k(test_df, test_scored, shift=True)
    report["auuc_qini"] = auuc_qini(test_df, test_scored, shift=True)

    print("=== Building policy-value curve (recovered value vs. contact budget) ===")
    def budgets_factory(contacts):
        return Budgets(daily_contacts=contacts, daily_incentive_inr=100000,
                        per_action_daily_cap={"ALTERNATIVE_PAYMENT_METHOD": 1000},
                        per_customer_daily_contact_cap=1)

    control_realized = realized_frames["CONTROL (no action)"]
    curve, curve_ms = timed(
        "policy value curve (greedy, 6 budget levels)",
        policy_value_curve, test_with_u, test_scored_feasible, budgets_factory, allocate,
        [0, 500, 1000, 2000, 4000, 8000], control_realized, "greedy"
    )
    report["policy_value_curve_contact_budget_vs_incremental_value"] = curve
    latencies["policy_value_curve_ms"] = round(curve_ms, 2)

    print("=== Multi-seed confidence interval on headline incremental value ===")
    seed_results = []
    N_MULTISEED = 5000
    for extra_seed in [101, 202, 303]:
        seed_test = generate_dataset(N_MULTISEED, SEED + 1000 + extra_seed, shift=True)
        seed_test["eligible_actions"] = seed_test.apply(eligible_actions_for, axis=1)
        seed_scored = model.score_all_actions(seed_test, "eligible_actions")
        seed_scored = compute_expected_incremental_value(seed_scored, seed_test)
        seed_budgets = Budgets(daily_contacts=int(4000 * N_MULTISEED / 20000),
                                daily_incentive_inr=100000 * N_MULTISEED / 20000,
                                per_action_daily_cap={"ALTERNATIVE_PAYMENT_METHOD": 250},
                                per_customer_daily_contact_cap=1)
        seed_alloc, _ = greedy_allocate(seed_scored, seed_budgets)
        seed_test_u = add_common_random_numbers(seed_test, seed=SEED + 2000 + extra_seed)
        seed_control_realized = realize_policy_outcomes(seed_test_u, control_policy(seed_test_u), shift=True)
        seed_crro_realized = realize_policy_outcomes(seed_test_u, seed_alloc[["case_id", "action"]], shift=True)
        from crro.evaluate import incremental_vs_control
        inc = incremental_vs_control(seed_crro_realized, seed_control_realized)
        seed_results.append(inc["incremental_net_value_inr"])

    report["multi_seed_confidence"] = {
        "note": (f"CRRO (greedy mode for speed) incremental net value vs. control, "
                 f"across 3 additional held-out seeds of {N_MULTISEED} cases each, "
                 f"scaled budgets proportionally."),
        **mean_ci95(seed_results),
    }

    report["performance_targets_measured"] = latencies

    with open("outputs/results.json", "w") as f:
        json.dump(report, f, indent=2, default=str)

    import pickle
    with open("outputs/model.pkl", "wb") as f:
        pickle.dump(model, f)
    test_df.drop(columns=["_latent_trait"]).to_parquet("outputs/test_cases.parquet", index=False)
    crro_alloc_final.to_parquet("outputs/crro_allocation.parquet", index=False)

    print("\n=== POLICY COMPARISON (Gross / Incremental / Net Incremental) ===")
    print(comparison_df[["policy", "gross_recovered_inr", "incremental_recovered_inr",
                          "incremental_net_value_inr"]].to_string(index=False))
    print("\n=== MULTI-SEED CONFIDENCE ===")
    print(report["multi_seed_confidence"])

    return report, comparison_df, audit_df


if __name__ == "__main__":
    import os
    os.makedirs("outputs", exist_ok=True)
    main()
