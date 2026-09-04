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
        self.actions = sorted(train_df["assigned_action"].unique())
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

    def score_all_actions(self, df, eligible_actions_col):
        """
        Returns a long-format DataFrame: one row per (case, eligible action)
        with recovery_probability, baseline_probability, estimated_uplift.
        """
        rows = []
        baseline_p = self.predict_proba(df, "NO_ACTION")
        for a in self.actions:
            if a == "NO_ACTION":
                continue
            p = self.predict_proba(df, a)
            mask = df[eligible_actions_col].apply(lambda lst, a=a: a in lst)
            for idx in np.where(mask.values)[0]:
                rows.append({
                    "case_id": df.iloc[idx]["case_id"],
                    "action": a,
                    "recovery_probability": float(p[idx]),
                    "baseline_probability": float(baseline_p[idx]),
                    "estimated_uplift": float(p[idx] - baseline_p[idx]),
                    "model_version": MODEL_VERSION,
                })
        return pd.DataFrame(rows)
