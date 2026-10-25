#!/usr/bin/env python3
"""
Multi-seed policy benchmark (SYNTHETIC SIMULATION).

Protocol (docs/BENCHMARKS.md): for each seed in --seeds, fresh train/test draws,
model fit on train only, identical feasible sets/budgets/CRN for every policy.
Seeds 101+ are disjoint from development seeds 1-2 used for model selection.
Writes raw per-seed JSON + a summary with Student-t 95% CIs across seeds.
"""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import numpy as np  # noqa: E402
from crro.evaluate import mean_ci95  # noqa: E402
from crro.experiments import default_budgets, model_quality, prepare, run_policies  # noqa: E402
from crro.model import make_model  # noqa: E402
from crro.optimizer import Budgets, compute_expected_incremental_value  # noqa: E402
from scripts.benchmarks._env import environment  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="101,102,103,104,105,106,107,108,109,110")
    ap.add_argument("--n-train", type=int, default=40000)
    ap.add_argument("--n-test", type=int, default=10000)
    ap.add_argument("--out", default="docs/benchmarks/results")
    ap.add_argument("--fresh", action="store_true", help="recompute seeds even if raw results exist")
    a = ap.parse_args()
    seeds = [int(s) for s in a.seeds.split(",")]
    os.makedirs(a.out, exist_ok=True)
    base_b = default_budgets(a.n_test)
    meta = {"environment": environment(), "label": "SYNTHETIC_SIMULATION", "seeds": seeds, "n_train": a.n_train,
            "n_test": a.n_test, "test_distribution_shift": True, "opt_out_rate": 0.03,
            "budgets": {"daily_contacts": base_b.daily_contacts, "daily_incentive_inr": base_b.daily_incentive_inr,
                        "per_action_daily_cap": base_b.per_action_daily_cap, "per_customer_daily_contact_cap": 1},
            "model_default": "pooled (S-learner, HistGradientBoosting)", "ci_method": "Student t, 95%, across seeds"}
    runs = []
    for s in seeds:
        path = f"{a.out}/policy_seed_{s}.json"
        if os.path.exists(path) and not a.fresh:      # resumable: reuse completed seeds' raw results
            runs.append(json.load(open(path)))
            print(f"seed {s}: loaded existing raw result", flush=True)
            continue
        t0 = time.perf_counter()
        ctx = prepare(s, a.n_train, a.n_test)
        main_res = run_policies(ctx, base_b, include_oracle=True, include_segments=True)
        main_res.pop("_allocation")
        quality = model_quality(ctx)
        # In-distribution diagnostics (same model, unshifted held-out draw).
        ctx_in = prepare(s, 0, a.n_test, shift=False, model=ctx["model"])
        ctx_in["test"]["assigned_action"] = ctx_in["test"]["assigned_action"]
        quality_in = model_quality(ctx_in, "in_distribution_holdout")
        # Model comparison: original T-learner on the SAME train/test/budgets.
        t1 = time.perf_counter()
        tl = make_model("tlearner").fit(ctx["train"])
        tl_fit = time.perf_counter() - t1
        ctx_t = dict(ctx, model=tl, scored=compute_expected_incremental_value(
            tl.score_all_actions(ctx["test"], "feasible_actions"), ctx["test"]))
        tl_res = run_policies(ctx_t, base_b, include_oracle=False)
        tl_crro = next(p for p in tl_res["policies"] if p["policy"] == "CRRO_EXACT")
        # Sensitivity (exact allocator, same scored data).
        sens = {"contact_budget": [], "incentive_budget": [], "action_cost_multiplier": []}
        for f in (0.25, 0.5, 1.0, 2.0, 4.0):
            r = run_policies(ctx, Budgets(int(base_b.daily_contacts * f), base_b.daily_incentive_inr,
                                          base_b.per_action_daily_cap, 1), include_oracle=False)
            c = next(p for p in r["policies"] if p["policy"] == "CRRO_EXACT")
            sens["contact_budget"].append({"factor": f, "budget": int(base_b.daily_contacts * f),
                                           "incremental_expected_net_paise": c["incremental_expected_net_paise"],
                                           "contacts_used": c["contacts_used"]})
        for f in (0.0, 0.5, 1.0, 2.0):
            r = run_policies(ctx, Budgets(base_b.daily_contacts, base_b.daily_incentive_inr * f,
                                          base_b.per_action_daily_cap, 1), include_oracle=False)
            c = next(p for p in r["policies"] if p["policy"] == "CRRO_EXACT")
            sens["incentive_budget"].append({"factor": f, "incremental_expected_net_paise": c["incremental_expected_net_paise"],
                                             "incentive_exposure_paise": c["incentive_exposure_paise"]})
        for m in (1.0, 5.0, 20.0):
            r = run_policies(ctx, base_b, include_oracle=False, cost_multiplier=m)
            c = next(p for p in r["policies"] if p["policy"] == "CRRO_EXACT")
            sens["action_cost_multiplier"].append({"multiplier": m, "incremental_expected_net_paise": c["incremental_expected_net_paise"],
                                                   "actions_taken": c["actions_taken"]})
        run = {"seed": s, "policies": main_res, "model_quality_shifted": quality,
               "model_quality_in_distribution": {k: quality_in[k] for k in ("per_action", "control_calibration")},
               "tlearner_crro_exact": {"incremental_expected_net_paise": tl_crro["incremental_expected_net_paise"],
                                       "incremental_net_paise": tl_crro["incremental_net_paise"], "fit_s": round(tl_fit, 2)},
               "sensitivity": sens, "timings_ms": ctx["timings_ms"], "seed_wall_s": round(time.perf_counter() - t0, 1)}
        json.dump(run, open(f"{a.out}/policy_seed_{s}.json", "w"), indent=1, default=str)
        runs.append(run)
        crro = next(p for p in main_res["policies"] if p["policy"] == "CRRO_EXACT")
        print(f"seed {s}: CRRO exact inc. expected net ₹{crro['incremental_expected_net_paise']/100:,.0f} "
              f"({run['seed_wall_s']}s)", flush=True)

    names = [p["policy"] for p in runs[0]["policies"]["policies"]]
    def col(name, key):
        return [next(p for p in r["policies"]["policies"] if p["policy"] == name)[key] for r in runs]
    summary = {"meta": meta, "policies": {}}
    for n in names:
        summary["policies"][n] = {k: mean_ci95(col(n, k)) for k in
                                  ("incremental_expected_net_paise", "incremental_net_paise", "incremental_gross_paise",
                                   "incremental_recovered_cases", "net_value_paise", "action_cost_paise",
                                   "incentive_cost_paise", "actions_taken", "contacts_used", "incentive_exposure_paise",
                                   "recovery_rate")}
        summary["policies"][n]["feasibility_violations_total"] = (
            sum(len(v or []) for v in col(n, "feasibility_violations")))
    crro_e = np.array(col("CRRO_EXACT", "incremental_expected_net_paise"))
    summary["paired_differences_crro_exact_minus"] = {
        n: mean_ci95(list(crro_e - np.array(col(n, "incremental_expected_net_paise"))))
        for n in names if n != "CRRO_EXACT"}
    oracle = np.array(col("ORACLE_CONSTRAINED (simulator-only)", "incremental_expected_net_paise"))
    summary["crro_share_of_constrained_oracle"] = mean_ci95(list(crro_e / oracle))
    summary["tlearner_vs_pooled_crro_exact"] = mean_ci95(
        [r["tlearner_crro_exact"]["incremental_expected_net_paise"] for r in runs])
    summary["exact_vs_greedy"] = {
        "objective_gain_pct": mean_ci95([100 * (r["policies"]["exact_vs_greedy"]["exact_objective_paise"] /
                                                r["policies"]["exact_vs_greedy"]["greedy_objective_paise"] - 1) for r in runs]),
        "exact_wall_ms": mean_ci95([r["policies"]["exact_vs_greedy"]["exact_wall_ms"] for r in runs]),
        "greedy_wall_ms": mean_ci95([r["policies"]["exact_vs_greedy"]["greedy_wall_ms"] for r in runs]),
        "statuses": sorted({r["policies"]["exact_vs_greedy"]["exact_status"] for r in runs}),
        "solver": sorted({r["policies"]["exact_vs_greedy"]["exact_solver"] for r in runs})}
    summary["model_quality"] = {
        "control_brier_shifted": mean_ci95([r["model_quality_shifted"]["control_calibration"]["brier_score"] for r in runs]),
        "control_ece_shifted": mean_ci95([r["model_quality_shifted"]["control_calibration"]["ece"] for r in runs]),
        "control_brier_in_distribution": mean_ci95([r["model_quality_in_distribution"]["control_calibration"]["brier_score"] for r in runs]),
        "control_ece_in_distribution": mean_ci95([r["model_quality_in_distribution"]["control_calibration"]["ece"] for r in runs]),
        "qini_rct_observed": {a: mean_ci95([r["model_quality_shifted"]["qini_rct_observed"][a]["qini_coefficient"] for r in runs])
                              for a in runs[0]["model_quality_shifted"]["qini_rct_observed"]},
        "oracle_uplift_top20pct (simulator-only)": mean_ci95(
            [r["model_quality_shifted"]["oracle_uplift_at_k (simulator-only)"]["top_20pct"] for r in runs]),
        "oracle_uplift_all (simulator-only)": mean_ci95(
            [r["model_quality_shifted"]["oracle_uplift_at_k (simulator-only)"]["all"] for r in runs])}
    summary["sensitivity_mean_over_seeds"] = {
        k: [{**{kk: vv for kk, vv in pt.items() if kk not in ("incremental_expected_net_paise",)},
             "incremental_expected_net_paise": mean_ci95([r["sensitivity"][k][i]["incremental_expected_net_paise"] for r in runs])}
            for i, pt in enumerate(runs[0]["sensitivity"][k])] for k in runs[0]["sensitivity"]}
    summary["crro_segments_seed_mean"] = {
        by: {seg["segment"]: {"n_mean": float(np.mean([next(x for x in r["policies"]["crro_segments"][by] if x["segment"] == seg["segment"])["n"] for r in runs])),
                              "incremental_net_paise": mean_ci95([next(x for x in r["policies"]["crro_segments"][by] if x["segment"] == seg["segment"])["incremental_net_paise"] for r in runs])}
             for seg in runs[0]["policies"]["crro_segments"][by]} for by in runs[0]["policies"]["crro_segments"]}
    json.dump(summary, open(f"{a.out}/policy_benchmark_summary.json", "w"), indent=1, default=str)
    print("summary written", flush=True)


if __name__ == "__main__":
    main()
