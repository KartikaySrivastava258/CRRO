"""
Counterfactual / Uplift Model (Spec Sections 20-22)
-----------------------------------------------------
T-learner: one calibrated action-conditioned probability model
P(recovery | X, action) per action (including NO_ACTION as control).

uplift(i, a) = P(recovery | X_i, a) - P(recovery | X_i, NO_ACTION)

This is a standard, explainable MVP approach per Section 20 ("MVP:
train an action-conditioned probability model ... gradient-boosted
trees ... Calibrate probabilities").
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline

NUMERIC_FEATURES = [
    "amount_inr", "attempt_count", "minutes_since_failure",
    "customer_success_rate", "customer_previous_recovery_rate", "time_of_day",
]
CATEGORICAL_FEATURES = ["payment_method", "failure_reason", "merchant_category"]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES

MODEL_VERSION = "uplift_tlearner_v1"


def _build_pipeline():
    pre = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
    ], remainder="passthrough")
    base = GradientBoostingClassifier(
        n_estimators=120, max_depth=3, learning_rate=0.08, random_state=0
    )
    calibrated = CalibratedClassifierCV(base, method="isotonic", cv=3)
    return Pipeline([("pre", pre), ("clf", calibrated)])


class UpliftModel:
    """One calibrated classifier per action (T-learner)."""

    def __init__(self):
        self.models = {}
        self.actions = []

    def fit(self, train_df):
        if "NO_ACTION" not in set(train_df["assigned_action"]):
            raise ValueError("training data must contain NO_ACTION (control) rows")
        self.actions = [str(a) for a in sorted(train_df["assigned_action"].unique())]
        for a in self.actions:
            sub = train_df[train_df["assigned_action"] == a]
            X = sub[FEATURES]
            y = sub["outcome_recovered"]
            pipe = _build_pipeline()
            pipe.fit(X, y)
            self.models[a] = pipe
        return self

    def predict_proba(self, df, action):
        """P(recovery | X, action) for a batch of cases."""
        if action not in self.models:
            raise ValueError(f"Unknown/untrained action: {action}")
        X = df[FEATURES]
        return self.models[action].predict_proba(X)[:, 1]

    def predict_actions(self, row_df, actions):
        return {a: float(self.predict_proba(row_df, a)[0]) for a in actions}

    def score_all_actions(self, df, eligible_actions_col="eligible_actions"):
        """Long frame: one row per (case, eligible non-control action) with
        recovery_probability, baseline_probability (NO_ACTION) and estimated_uplift.
        Uses only FEATURES; never reads outcome, assigned_action or hidden columns."""
        baseline_p = self.predict_proba(df, "NO_ACTION")
        case_ids = df["case_id"].to_numpy()
        elig = df[eligible_actions_col].tolist()
        frames = []
        for a in self.actions:
            if a == "NO_ACTION":
                continue
            mask = np.fromiter((a in lst for lst in elig), dtype=bool, count=len(elig))
            if not mask.any():
                continue
            p = self.predict_proba(df[mask], a)
            frames.append(pd.DataFrame({
                "case_id": case_ids[mask], "action": a, "recovery_probability": p,
                "baseline_probability": baseline_p[mask], "estimated_uplift": p - baseline_p[mask],
                "model_version": getattr(self, "version", MODEL_VERSION)}))
        if not frames:
            return pd.DataFrame(columns=["case_id", "action", "recovery_probability", "baseline_probability",
                                         "estimated_uplift", "model_version"])
        return pd.concat(frames, ignore_index=True)


class PooledUpliftModel:
    """S-learner: ONE gradient-boosted classifier on features + action indicator.

    Chosen over the per-action T-learner after a development comparison on seeds
    1-2 (docs/DECISION_SCIENCE.md#model-selection): pooling lets the model share
    the baseline-propensity structure across actions instead of fitting seven
    models on 1/7 of the data each. Reported benchmarks use disjoint seeds.
    Unknown categories at inference map to code -1, which HistGradientBoosting
    treats as missing (no crash, recorded as an imputation by the live engine).
    """
    version = "pooled_hgb_v2"

    def __init__(self, random_state=0):
        self.random_state = random_state
        self.actions, self.categories, self.clf = [], {}, None

    def _X(self, df, actions):
        X = df[NUMERIC_FEATURES].astype(float).copy()
        for c in CATEGORICAL_FEATURES:
            X[c] = pd.Categorical(df[c], categories=self.categories[c]).codes
        X["action"] = pd.Categorical(np.asarray(actions, dtype=object), categories=self.actions).codes
        return X

    def fit(self, train_df):
        from sklearn.ensemble import HistGradientBoostingClassifier
        if "NO_ACTION" not in set(train_df["assigned_action"]):
            raise ValueError("training data must contain NO_ACTION (control) rows")
        self.actions = [str(a) for a in sorted(train_df["assigned_action"].unique())]
        self.categories = {c: sorted(str(v) for v in train_df[c].unique()) for c in CATEGORICAL_FEATURES}
        X = self._X(train_df, train_df["assigned_action"].to_numpy())
        cat_idx = [X.columns.get_loc(c) for c in CATEGORICAL_FEATURES + ["action"]]
        self.clf = HistGradientBoostingClassifier(
            max_iter=300, learning_rate=0.06, max_leaf_nodes=31, l2_regularization=1.0,
            categorical_features=cat_idx, random_state=self.random_state)
        self.clf.fit(X, train_df["outcome_recovered"].to_numpy())
        return self

    def predict_proba(self, df, action):
        if action not in self.actions:
            raise ValueError(f"Unknown/untrained action: {action}")
        return self.clf.predict_proba(self._X(df, np.full(len(df), action, dtype=object)))[:, 1]

    score_all_actions = UpliftModel.score_all_actions

    def predict_actions(self, row_df, actions):
        """Score ONE case under several actions in a single model call (live path).
        Profiling showed per-call preprocessing dominated latency; batching the
        actions as rows gives identical probabilities (see tests)."""
        acts = [a for a in actions]
        for a in acts:
            if a not in self.actions:
                raise ValueError(f"Unknown/untrained action: {a}")
        rep = row_df.loc[row_df.index.repeat(len(acts))].reset_index(drop=True)
        p = self.clf.predict_proba(self._X(rep, np.asarray(acts, dtype=object)))[:, 1]
        return dict(zip(acts, p.tolist()))


def make_model(kind: str = "pooled"):
    if kind == "pooled":
        return PooledUpliftModel()
    if kind == "tlearner":
        return UpliftModel()
    raise ValueError(f"unknown model kind {kind!r}")
