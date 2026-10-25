#!/usr/bin/env python3
"""Exact (HiGHS / CP-SAT) vs greedy on identical scored portfolios of growing size.
Objective = model-estimated expected incremental value (paise). 3 repeats per size."""
import json, os, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from crro.decision.model_store import load  # noqa: E402
from crro.experiments import default_budgets, prepare  # noqa: E402
from crro.optimizer import check_feasibility, exact_allocate, greedy_allocate  # noqa: E402
from scripts.benchmarks._env import environment  # noqa: E402

model, card = load("outputs/model")
rows = []
for n in (500, 2000, 5000, 10000, 20000):
    ctx = prepare(900 + n, 0, n, model=model)
    b = default_budgets(n)
    cust = ctx["test"].set_index("case_id")["customer_id"]
    reps = []
    for rep in range(3):
        ea, eu = exact_allocate(ctx["scored"], b, time_limit_seconds=60)
        ga, gu = greedy_allocate(ctx["scored"], b)
        reps.append({"exact_ms": eu["wall_time_ms"], "greedy_ms": gu["wall_time_ms"], "status": eu["status"],
                     "exact_obj": eu["objective_expected_value_paise"], "greedy_obj": gu["objective_expected_value_paise"],
                     "violations": len(check_feasibility(ea, b, cust)) + len(check_feasibility(ga, b, cust)),
                     "candidates": eu["candidates"]})
    r = {"n_cases": n, "candidates": reps[0]["candidates"], "status": sorted({x["status"] for x in reps}),
         "exact_ms_median": statistics.median(x["exact_ms"] for x in reps),
         "greedy_ms_median": statistics.median(x["greedy_ms"] for x in reps),
         "exact_objective_paise": reps[0]["exact_obj"], "greedy_objective_paise": reps[0]["greedy_obj"],
         "gap_pct": round(100 * (reps[0]["exact_obj"] / reps[0]["greedy_obj"] - 1), 3) if reps[0]["greedy_obj"] else None,
         "deterministic_objective": len({x["exact_obj"] for x in reps}) == 1,
         "feasibility_violations": sum(x["violations"] for x in reps), "raw": reps}
    rows.append(r)
    print(json.dumps({k: v for k, v in r.items() if k != "raw"}), flush=True)
os.makedirs("docs/benchmarks/results", exist_ok=True)
json.dump({"environment": environment(), "model_version": card["model_version"], "label": "SYNTHETIC_SIMULATION",
           "budgets": "default_budgets(n): contacts n/10, incentive ₹5*n, ALT_PAYMENT cap n/25, 1 contact/customer",
           "repeats": 3, "results": rows}, open("docs/benchmarks/results/optimizer_scaling.json", "w"), indent=1)
