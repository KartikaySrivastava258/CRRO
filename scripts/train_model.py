#!/usr/bin/env python3
"""Train and persist the live decision model + model card (synthetic data)."""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crro.decision.model_store import train_and_save  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--seed", type=int, default=2026)
ap.add_argument("--n-train", type=int, default=40000)
ap.add_argument("--kind", choices=["pooled", "tlearner"], default="pooled")
ap.add_argument("--out", default=os.environ.get("CRRO_MODEL_DIR", "outputs/model"))
a = ap.parse_args()
card = train_and_save(seed=a.seed, n_train=a.n_train, kind=a.kind, out_dir=a.out)
print(json.dumps({k: card[k] for k in ("model_version", "kind", "fit_seconds")} |
                 {"holdout_control_brier": card["holdout_control_calibration"]["brier_score"],
                  "holdout_control_ece": card["holdout_control_calibration"]["ece"]}, indent=2))
