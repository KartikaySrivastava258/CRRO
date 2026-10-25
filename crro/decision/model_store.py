"""
Versioned model artifact: outputs/model/model.pkl + model_card.json.

The card records seed, training size, features, imputation defaults (training
medians) and held-out diagnostics. model_version embeds the artifact's SHA-256
prefix, and load() verifies the hash (integrity against accidental corruption /
mismatched files -- NOT a defence against an attacker who can write both files;
pickle must only be loaded from a trusted location).
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import time
from pathlib import Path

import sklearn

from ..data_generator import generate_dataset
from ..evaluate import calibration_report
from ..model import CATEGORICAL_FEATURES, FEATURES, make_model

DEFAULT_DIR = Path(os.environ.get("CRRO_MODEL_DIR", "outputs/model"))


def train_and_save(seed: int = 2026, n_train: int = 40000, n_holdout: int = 10000, kind: str = "pooled",
                   out_dir: Path = DEFAULT_DIR) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    train = generate_dataset(n_train, seed=seed * 1000 + 1)
    holdout = generate_dataset(n_holdout, seed=seed * 1000 + 2)          # in-distribution
    model = make_model(kind).fit(train)
    fit_s = time.perf_counter() - t0
    blob = pickle.dumps(model)
    sha = hashlib.sha256(blob).hexdigest()
    ctrl = holdout[holdout["assigned_action"] == "NO_ACTION"]
    card = {
        "model_version": f"{getattr(model, 'version', 'uplift_tlearner_v1')}-{sha[:10]}",
        "kind": kind, "artifact_sha256": sha, "trained_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "training_data": {"source": "SYNTHETIC (crro.data_generator, randomised action assignment)",
                          "seed": seed * 1000 + 1, "n_train": n_train,
                          "holdout_seed": seed * 1000 + 2, "n_holdout": n_holdout},
        "features": FEATURES, "categorical_features": CATEGORICAL_FEATURES,
        "known_categories": {c: sorted(str(v) for v in train[c].unique()) for c in CATEGORICAL_FEATURES},
        "imputation_defaults": {"customer_success_rate": float(train["customer_success_rate"].median()),
                                "customer_previous_recovery_rate": float(train["customer_previous_recovery_rate"].median()),
                                "merchant_category": "ecommerce"},
        "minutes_since_failure_training_range": [int(train["minutes_since_failure"].min()),
                                                 int(train["minutes_since_failure"].max())],
        "holdout_control_calibration": calibration_report(ctrl["outcome_recovered"].to_numpy(),
                                                          model.predict_proba(ctrl, "NO_ACTION")),
        "fit_seconds": round(fit_s, 2), "sklearn_version": sklearn.__version__,
        "uncertainty": "Not estimated (point predictions only).",
    }
    (out_dir / "model.pkl").write_bytes(blob)
    (out_dir / "model_card.json").write_text(json.dumps(card, indent=2))
    return card


def load(out_dir: Path = DEFAULT_DIR):
    out_dir = Path(out_dir)
    card = json.loads((out_dir / "model_card.json").read_text())
    blob = (out_dir / "model.pkl").read_bytes()
    if hashlib.sha256(blob).hexdigest() != card["artifact_sha256"]:
        raise RuntimeError("model artifact hash does not match model_card.json")
    return pickle.loads(blob), card


def load_or_train(out_dir: Path = DEFAULT_DIR, **train_kw):
    if not (Path(out_dir) / "model_card.json").exists():
        train_and_save(out_dir=out_dir, **train_kw)
    return load(out_dir)
