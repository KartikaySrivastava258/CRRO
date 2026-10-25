"""
Synthetic Causal Dataset Generator (Spec Sections 15-19)
----------------------------------------------------------
Generates failed one-time payment cases with a HIDDEN causal outcome
model. Actions are randomly assigned (RCT-style) so the resulting
dataset supports unbiased uplift estimation.

Design choices to avoid "synthetic cheating" (Section 17):
  - Outcome depends on LATENT customer traits, not just observed features.
  - Observed features are noisy proxies of the latent traits.
  - There are genuine action x segment interactions (Section 16) so a
    trivial "always pick action X" policy cannot win.
  - The held-out test set includes a mild distribution shift (Section 77 /
    75 "distribution-shift test") so the model is evaluated for
    generalization, not memorization.
"""

import numpy as np
import pandas as pd
from .oracle import true_p_recovery

FAILURE_REASONS = ["insufficient_funds", "bank_decline", "network_error",
                    "risk_block", "otp_timeout", "card_expired"]
PAYMENT_METHODS = ["upi", "card", "netbanking", "wallet"]
MERCHANT_CATEGORIES = ["ecommerce", "subscription", "travel", "food_delivery", "edtech"]
ACTIONS = ["NO_ACTION", "RETRY_NOW", "RETRY_LATER", "PAYMENT_LINK",
           "ALTERNATIVE_PAYMENT_METHOD", "CUSTOMER_NUDGE", "SMALL_INCENTIVE"]


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def generate_dataset(n, seed, shift=False, n_merchants=8, n_customers=4000):
    """
    Generate n synthetic failed-payment cases with randomized action
    assignment and a hidden ground-truth causal outcome model.

    shift=True introduces a mild covariate/effect shift, used for the
    final held-out distribution-shift check (spec section 75/77).
    """
    rng = np.random.default_rng(seed)

    merchant_id = rng.integers(0, n_merchants, size=n)
    customer_id = rng.integers(0, n_customers, size=n)

    customer_latent_table = rng.normal(0, 1, size=n_customers)
    latent_trait = customer_latent_table[customer_id]

    failure_reason = rng.choice(FAILURE_REASONS, size=n,
                                 p=[0.30, 0.22, 0.14, 0.10, 0.14, 0.10])
    payment_method = rng.choice(PAYMENT_METHODS, size=n, p=[0.45, 0.30, 0.15, 0.10])
    merchant_category = rng.choice(MERCHANT_CATEGORIES, size=n)

    amount_inr = np.round(np.exp(rng.normal(8.2, 0.9, size=n))).clip(100, 200000)
    attempt_count = rng.integers(1, 4, size=n)
    minutes_since_failure = rng.integers(1, 4320, size=n)  # up to 3 days
    time_of_day = rng.integers(0, 24, size=n)

    customer_success_rate = np.clip(_sigmoid(latent_trait + rng.normal(0, 0.4, size=n)), 0.02, 0.98)
    customer_previous_recovery_rate = np.clip(
        _sigmoid(0.7 * latent_trait + rng.normal(0, 0.5, size=n)), 0.02, 0.98
    )

    action = np.empty(n, dtype=object)
    for i in range(n):
        pool = ["NO_ACTION", "RETRY_NOW", "RETRY_LATER", "PAYMENT_LINK",
                "ALTERNATIVE_PAYMENT_METHOD", "CUSTOMER_NUDGE"]
        if amount_inr[i] < 20000:
            pool = pool + ["SMALL_INCENTIVE"]
        action[i] = rng.choice(pool)

    df_partial = pd.DataFrame({
        "failure_reason": failure_reason,
        "payment_method": payment_method,
        "amount_inr": amount_inr,
        "attempt_count": attempt_count,
        "minutes_since_failure": minutes_since_failure,
        "_latent_trait": latent_trait,
    })
    p_recovery = true_p_recovery(df_partial, action, shift=shift)
    outcome = (rng.uniform(0, 1, size=n) < p_recovery).astype(int)

    df = pd.DataFrame({
        # Dataset-unique ids: previously every dataset used R-000000.., so a train row
        # and a test row could share a case_id (a join-leakage hazard).
        "case_id": [f"S{seed}{'X' if shift else ''}-{i:06d}" for i in range(n)],
        "merchant_id": [f"merchant_{m:03d}" for m in merchant_id],
        "customer_id": [f"cust_{c:05d}" for c in customer_id],
        "amount_inr": amount_inr.round(2),
        "payment_method": payment_method,
        "failure_reason": failure_reason,
        "attempt_count": attempt_count,
        "minutes_since_failure": minutes_since_failure,
        "customer_success_rate": customer_success_rate.round(4),
        "customer_previous_recovery_rate": customer_previous_recovery_rate.round(4),
        "merchant_category": merchant_category,
        "time_of_day": time_of_day,
        "assigned_action": action,       # only present in TRAIN/DEV (RCT logs), not at inference time
        "outcome_recovered": outcome,    # ground truth (train/dev/eval only, never at inference)
        "_latent_trait": latent_trait,   # hidden; benchmark harness only, never fed to the model
        "_true_p_recovery": p_recovery,  # hidden diagnostic, for benchmark validation only
        "_shift": shift,
    })
    return df


def eligible_actions_for(row):
    """Deterministic eligibility policy (Section 9-10): not model output."""
    pool = ["NO_ACTION", "RETRY_NOW", "RETRY_LATER", "PAYMENT_LINK",
            "ALTERNATIVE_PAYMENT_METHOD", "CUSTOMER_NUDGE"]
    if row["amount_inr"] < 20000:
        pool = pool + ["SMALL_INCENTIVE"]
    if row["attempt_count"] >= 3:
        pool = [a for a in pool if a not in ("RETRY_NOW", "RETRY_LATER")]
    return pool


if __name__ == "__main__":
    train = generate_dataset(60000, seed=1)
    val = generate_dataset(10000, seed=2)
    test = generate_dataset(20000, seed=3, shift=True)
    print(train.head())
    print("Recovery rate by action (train, RCT-unbiased):")
    print(train.groupby("assigned_action")["outcome_recovered"].mean().sort_values(ascending=False))
