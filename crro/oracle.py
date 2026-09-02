"""Hidden causal benchmark oracle. Keep this module outside model/runtime logic."""
import numpy as np
import pandas as pd


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def true_p_recovery(df, action_array, shift=False):
    latent = df["_latent_trait"].to_numpy() if "_latent_trait" in df else 2 * (df["customer_success_rate"].to_numpy() - 0.5)
    failure = df["failure_reason"].to_numpy()
    method = df["payment_method"].to_numpy()
    amount = df["amount_inr"].to_numpy()
    attempts = df["attempt_count"].to_numpy()
    age = df["minutes_since_failure"].to_numpy()
    action = np.asarray(action_array, dtype=object)

    failure_effect = pd.Series(failure).map({
        "insufficient_funds": -0.9, "bank_decline": -0.6, "network_error": 0.4,
        "risk_block": -1.3, "otp_timeout": 0.2, "card_expired": -1.1,
    }).to_numpy()
    method_effect = pd.Series(method).map({"upi": 0.3, "card": -0.1, "netbanking": -0.3, "wallet": 0.1}).to_numpy()
    base = 0.9 * latent + failure_effect + method_effect
    base += -0.15 * np.abs(age / 60.0 - 6) / 6
    base += -0.25 * (np.log(amount) - 8.0) - 0.35 * (attempts - 1)
    if shift:
        base += -0.15
        base += -0.15 * (np.log(amount) - 8.0)
    base -= 0.2

    action_main = pd.Series(action).map({
        "NO_ACTION": 0.0, "RETRY_NOW": 0.35, "RETRY_LATER": 0.55,
        "PAYMENT_LINK": 0.45, "ALTERNATIVE_PAYMENT_METHOD": 0.5,
        "CUSTOMER_NUDGE": 0.3, "SMALL_INCENTIVE": 0.8,
    }).to_numpy()
    bonus = np.zeros(len(df))
    bonus += ((failure == "insufficient_funds") & (action == "RETRY_LATER")) * 0.6
    bonus += ((failure == "risk_block") & (action == "PAYMENT_LINK")) * 0.5
    bonus += ((failure == "network_error") & (action == "RETRY_NOW")) * 0.7
    bonus += ((failure == "card_expired") & (action == "ALTERNATIVE_PAYMENT_METHOD")) * 0.9
    bonus += ((failure == "otp_timeout") & (action == "RETRY_NOW")) * 0.5
    low = latent < -0.3
    bonus += low * ((action == "SMALL_INCENTIVE") * 0.9 + (action == "CUSTOMER_NUDGE") * 0.4 + (action == "PAYMENT_LINK") * 0.3)
    bonus += (latent > 0.6) * (action != "NO_ACTION") * -0.25
    bonus += ((method == "card") & (action == "ALTERNATIVE_PAYMENT_METHOD")) * 0.6
    return _sigmoid(base + action_main + bonus)
