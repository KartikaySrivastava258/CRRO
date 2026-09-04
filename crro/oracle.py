"""
Hidden Causal Oracle (Spec Sections 16-19)
---------------------------------------------
Single source of truth for P(recovery | X, action) under the synthetic
environment's hidden causal structure. Used to:
  1. label the RCT training/validation/test datasets (given the randomly
     assigned action), and
  2. realize ACTUAL outcomes during simulated execution / baseline
     comparison for whatever action a policy (CRRO or a baseline)
     actually chooses -- so every policy is evaluated on genuine
     counterfactual outcomes from the SAME environment, not on its own
     predictions.

This module must never be visible to the predictive model, the LLM, or
the optimizer -- only to the benchmark harness.
"""

import numpy as np
import pandas as pd


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def true_p_recovery(df, action_array, shift=False):
    """
    Vectorized hidden ground-truth P(recovery) for an arbitrary array of
    actions (one action per row of df). df must contain the same columns
    produced by data_generator.generate_dataset, including the latent
    trait proxy columns and the internal `_latent_trait` if present.
    """
    n = len(df)
    latent_trait = df["_latent_trait"].values if "_latent_trait" in df.columns else \
        (2 * (df["customer_success_rate"].values - 0.5))  # fallback proxy

    failure_reason = df["failure_reason"].values
    payment_method = df["payment_method"].values
    amount_inr = df["amount_inr"].values
    attempt_count = df["attempt_count"].values
    minutes_since_failure = df["minutes_since_failure"].values
    action = np.asarray(action_array, dtype=object)

    failure_effect = pd.Series(failure_reason).map({
        "insufficient_funds": -0.9, "bank_decline": -0.6, "network_error": 0.4,
        "risk_block": -1.3, "otp_timeout": 0.2, "card_expired": -1.1,
    }).values
    method_effect = pd.Series(payment_method).map({
        "upi": 0.3, "card": -0.1, "netbanking": -0.3, "wallet": 0.1
    }).values
    time_effect = -0.15 * np.abs((minutes_since_failure / 60.0) - 6) / 6.0
    amount_effect = -0.25 * (np.log(amount_inr) - 8.0)
    attempt_effect = -0.35 * (attempt_count - 1)

    shift_shift = -0.15 if shift else 0.0
    if shift:
        amount_effect = amount_effect * 1.15

    base_logit = (
        0.9 * latent_trait + failure_effect + method_effect + time_effect
        + amount_effect + attempt_effect + shift_shift - 0.2
    )

    action_main = pd.Series(action).map({
        "NO_ACTION": 0.0, "RETRY_NOW": 0.35, "RETRY_LATER": 0.55,
        "PAYMENT_LINK": 0.45, "ALTERNATIVE_PAYMENT_METHOD": 0.5,
        "CUSTOMER_NUDGE": 0.3, "SMALL_INCENTIVE": 0.8,
    }).values

    fr_action_bonus = np.zeros(n)
    fr_action_bonus += ((failure_reason == "insufficient_funds") & (action == "RETRY_LATER")).astype(float) * 0.6
    fr_action_bonus += ((failure_reason == "risk_block") & (action == "PAYMENT_LINK")).astype(float) * 0.5
    fr_action_bonus += ((failure_reason == "network_error") & (action == "RETRY_NOW")).astype(float) * 0.7
    fr_action_bonus += ((failure_reason == "card_expired") & (action == "ALTERNATIVE_PAYMENT_METHOD")).astype(float) * 0.9
    fr_action_bonus += ((failure_reason == "otp_timeout") & (action == "RETRY_NOW")).astype(float) * 0.5

    low_reliability = (latent_trait < -0.3).astype(float)
    seg_action_bonus = low_reliability * (
        (action == "SMALL_INCENTIVE").astype(float) * 0.9
        + (action == "CUSTOMER_NUDGE").astype(float) * 0.4
        + (action == "PAYMENT_LINK").astype(float) * 0.3
    )
    high_reliability = (latent_trait > 0.6).astype(float)
    seg_action_penalty = high_reliability * (action != "NO_ACTION").astype(float) * (-0.25)

    pm_action_bonus = ((payment_method == "card") & (action == "ALTERNATIVE_PAYMENT_METHOD")).astype(float) * 0.6

    logit = (base_logit + action_main + fr_action_bonus + seg_action_bonus
              + seg_action_penalty + pm_action_bonus)
    return _sigmoid(logit)
