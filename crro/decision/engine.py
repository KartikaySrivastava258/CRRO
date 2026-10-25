"""
Live decision engine for one recovery case.

Deterministic guardrails are authoritative; the model is advisory input to them:
  eligibility (registry rules) -> merchant allow-list -> opt-out -> cooldown ->
  per-customer contact cap -> contact/incentive budget -> expected value > 0
Every candidate action is reported with its estimates AND every reason it was
rejected, so the dashboard can explain "why not X" exactly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from ..action_registry import ACTION_REGISTRY
from ..data_generator import eligible_actions_for
from ..economics import action_cost_paise, auto_offer_for, expected_incremental_net_paise

IST = timezone(timedelta(hours=5, minutes=30))


def build_features(case: dict, card: dict, policy: dict, now: datetime):
    """Map a ledger case to model features; returns (feature_row, imputations)."""
    imputations = []
    failed_at = datetime.fromisoformat(case["failed_at"])
    mins = (now - failed_at).total_seconds() / 60
    lo, hi = card["minutes_since_failure_training_range"]
    if mins < lo or mins > hi:
        imputations.append({"feature": "minutes_since_failure", "reason": "clipped_to_training_range",
                            "observed": round(mins, 1)})
        mins = min(max(mins, lo), hi)
    feats = case.get("features") or {}
    row = {"amount_inr": case["amount_paise"] / 100.0, "attempt_count": int(case["attempt_count"]),
           "minutes_since_failure": float(mins), "time_of_day": failed_at.astimezone(IST).hour,
           "payment_method": case["payment_method"], "failure_reason": case["failure_reason"]}
    for f in ("customer_success_rate", "customer_previous_recovery_rate"):
        if f in feats:
            row[f] = float(feats[f])
        else:
            row[f] = card["imputation_defaults"][f]
            imputations.append({"feature": f, "reason": "not_provided_used_training_median", "value": row[f]})
    mc = case.get("merchant_category") or policy.get("default_merchant_category")
    if not case.get("merchant_category"):
        imputations.append({"feature": "merchant_category", "reason": "not_provided_used_policy_default", "value": mc})
    row["merchant_category"] = mc
    for c in ("payment_method", "failure_reason", "merchant_category"):
        if str(row[c]) not in card["known_categories"].get(c, []):
            imputations.append({"feature": c, "reason": "unknown_category_treated_as_missing", "value": row[c]})
    return row, imputations


def decide(case: dict, model, card: dict, policy: dict, guard: dict, now: datetime) -> dict:
    row, imputations = build_features(case, card, policy, now)
    X = pd.DataFrame([row])
    trained = [a for a in ACTION_REGISTRY if a in getattr(model, "actions", [])]
    probs = model.predict_actions(X, trained)          # one batched model call per case
    p0 = float(probs["NO_ACTION"])
    eligible = set(eligible_actions_for({"amount_inr": row["amount_inr"], "attempt_count": row["attempt_count"]}))
    allowed = set(policy["allowed_actions"])
    amt = int(case["amount_paise"])
    candidates = []
    for a, spec in ACTION_REGISTRY.items():
        if a == "NO_ACTION":
            continue
        reasons = []
        if a not in eligible:
            reasons.append("NOT_ELIGIBLE")
        if a not in allowed:
            reasons.append("NOT_ALLOWED_BY_MERCHANT_POLICY")
        if a not in getattr(model, "actions", []):
            reasons.append("MODEL_HAS_NO_ESTIMATE")
        if spec.requires_customer_contact and guard["opted_out"]:
            reasons.append("CUSTOMER_OPTED_OUT")
        last = guard["last_action_at"].get(a)
        if last and spec.min_cooldown_minutes and \
                now - datetime.fromisoformat(last) < timedelta(minutes=spec.min_cooldown_minutes):
            reasons.append("COOLDOWN_ACTIVE")
        if spec.requires_customer_contact and guard["customer_contacts_24h"] >= policy["per_customer_daily_contact_cap"]:
            reasons.append("CUSTOMER_CONTACT_CAP")
        if spec.requires_customer_contact and guard["contacts_used_today"] >= policy["daily_contact_budget"]:
            reasons.append("CONTACT_BUDGET_EXHAUSTED")
        offer_id, discount = auto_offer_for(a, amt)
        if spec.is_incentive and offer_id is None:
            reasons.append("NO_AUTO_APPROVABLE_OFFER")
        if discount and guard["incentive_committed_today_paise"] + discount > policy["daily_incentive_budget_paise"]:
            reasons.append("INCENTIVE_BUDGET_EXHAUSTED")
        cand = {"action": a, "requires_customer_contact": spec.requires_customer_contact,
                "action_cost_paise": action_cost_paise(a), "incentive_discount_paise": discount, "offer_id": offer_id,
                "p_action": None, "p_control": p0, "uplift": None, "expected_value_paise": None}
        if "MODEL_HAS_NO_ESTIMATE" not in reasons and "NOT_ELIGIBLE" not in reasons:
            pa = float(probs[a])
            ev = expected_incremental_net_paise(pa, p0, amt, a)
            cand.update(p_action=pa, uplift=pa - p0, expected_value_paise=round(ev, 2),
                        expected_gross_incremental_paise=round((pa - p0) * amt, 2),
                        expected_incentive_cost_paise=round(pa * discount, 2))
            if ev <= 0:
                reasons.append("NON_POSITIVE_EXPECTED_VALUE")
        cand["rejection_reasons"] = reasons
        cand["feasible"] = not reasons
        candidates.append(cand)
    feasible = [c for c in candidates if c["feasible"]]
    # Highest expected value; ties -> lower cost, then registry order (deterministic).
    feasible.sort(key=lambda c: (-c["expected_value_paise"], c["action_cost_paise"] + c["incentive_discount_paise"]))
    base = {"candidates": candidates, "p_control": p0, "model_version": card["model_version"],
            "feature_imputations": imputations, "features_used": row}
    if not feasible:
        scored = [c for c in candidates if c["expected_value_paise"] is not None]
        if scored and all(c["rejection_reasons"] == ["NON_POSITIVE_EXPECTED_VALUE"] for c in scored
                          if c["rejection_reasons"]):
            reason = "NO_POSITIVE_EXPECTED_VALUE"
        else:
            reason = "ALL_CANDIDATES_BLOCKED_BY_GUARDRAILS"
        return {**base, "selected_action": "NO_ACTION", "reason_code": reason, "expected_value_paise": 0,
                "p_selected": None, "requires_approval": False}
    best = feasible[0]
    threshold = policy["approval_threshold_paise"].get(best["action"])
    needs = threshold is not None and amt > threshold
    return {**base, "selected_action": best["action"], "reason_code": "HIGHEST_EXPECTED_INCREMENTAL_VALUE",
            "expected_value_paise": best["expected_value_paise"], "p_selected": best["p_action"],
            "requires_approval": needs, "threshold_paise": threshold,
            "approval_reason": (f"Amount {amt} paise exceeds the {threshold} paise auto-approval threshold for "
                                f"{best['action']}") if needs else None,
            "discount_paise": best["incentive_discount_paise"], "action_cost_paise": best["action_cost_paise"],
            "offer_id": best["offer_id"], "requires_contact": best["requires_customer_contact"]}
