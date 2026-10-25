#!/usr/bin/env python3
"""Single-seed offline evaluation (quick look). For reported numbers use the
multi-seed protocol: python3 scripts/benchmarks/policy_benchmark.py
All values are SYNTHETIC SIMULATION, not merchant revenue."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from crro.experiments import default_budgets, model_quality, prepare, run_policies  # noqa: E402

seed, n_train, n_test = int(os.environ.get("SEED", 2026)), 40000, 10000
ctx = prepare(seed, n_train, n_test)
res = run_policies(ctx, default_budgets(n_test), include_segments=True)
res.pop("_allocation")
print(f"seed={seed} n_train={n_train} n_test={n_test} (SYNTHETIC_SIMULATION, values in INR)")
for p in res["policies"]:
    print(f"  {p['policy'][:40]:40s} incremental net (realised) {p['incremental_net_paise']/100:>14,.2f}"
          f"  (expected) {p['incremental_expected_net_paise']/100:>14,.2f}  actions={p['actions_taken']}")
os.makedirs("outputs", exist_ok=True)
json.dump({"seed": seed, "policies": res, "model_quality": model_quality(ctx)},
          open("outputs/run_demo_single_seed.json", "w"), indent=2, default=str)
