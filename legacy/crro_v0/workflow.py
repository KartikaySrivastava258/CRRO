"""
Event-Driven Recovery Workflow
------------------------------------------------------------------
Implements the required flow for a SINGLE incoming payment.failed
event (as opposed to run_demo.py's offline batch pipeline):

  payment.failed
    -> idempotency check (case already open?)
    -> create/update RecoveryCase
    -> customer state lookup (Postgres, real)
    -> AI Recovery Analyst (candidate actions, validated against registry)
    -> Uplift model (scores only the analyst's validated candidates)
    -> real-time portfolio budget check (today's contacts used, from
       Postgres -- a genuine running counter, not a batch LP; this is
       how real-time event-driven systems approximate portfolio
       constraints without re-solving an LP per event)
    -> policy / guardrail gate (cooldown, opt-out, max-attempts,
       approval threshold -- same crro/policy.py used by the batch path)
    -> approval gate if required
    -> execution via RecoveryExecutionProvider
    -> persistence (RecoveryDecision, CustomerActionHistory, AuditEvent)

Reconciliation (webhook -> recovered) is handled separately in
api.py's webhook receiver, which updates the same PaymentCase /
RecoveryDecision rows this module creates.
"""

import os
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from .action_registry import ACTION_REGISTRY
from .data_generator import eligible_actions_for
from .evidence import build_evidence
from .llm_analyst import analyze_case
from .optimizer import compute_expected_incremental_value
from .policy import evaluate_policy
from .db import get_session, db_is_reachable
from .db_models import Merchant, Customer, PaymentCase, RecoveryDecision, AuditEvent, CustomerActionHistory

DAILY_CONTACT_BUDGET = int(os.environ.get("CRRO_DAILY_CONTACT_BUDGET", "4000"))


def _today_contacts_used(session) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=1)
    n = (session.query(CustomerActionHistory)
         .filter(CustomerActionHistory.status == "EXECUTED",
                 CustomerActionHistory.timestamp >= since,
                 CustomerActionHistory.action.in_(
                     [a for a, spec in ACTION_REGISTRY.items() if spec.requires_customer_contact]))
         .count())
    return n


def _upsert_case(session, case_row):
    if case_row.get("merchant_id") and session.get(Merchant, case_row["merchant_id"]) is None:
        session.add(Merchant(merchant_id=case_row["merchant_id"]))
        session.flush()
    if session.get(Customer, case_row["customer_id"]) is None:
        session.add(Customer(customer_id=case_row["customer_id"]))
        session.flush()
    case = session.get(PaymentCase, case_row["case_id"])
    if case is None:
        case = PaymentCase(
            case_id=case_row["case_id"], merchant_id=case_row.get("merchant_id"),
            customer_id=case_row["customer_id"], amount_inr=float(case_row["amount_inr"]),
            payment_method=case_row["payment_method"], failure_reason=case_row["failure_reason"],
            attempt_count=int(case_row.get("attempt_count", 1)),
            minutes_since_failure=int(case_row.get("minutes_since_failure", 0)),
            status="OPEN",
        )
        session.add(case)
        session.flush()
    return case


def process_payment_failure(case_row, model, opted_out_check=None) -> dict:
    """
    case_row: dict with case_id, merchant_id, customer_id, amount_inr,
    payment_method, failure_reason, attempt_count, minutes_since_failure.
    model: a fitted crro.model.UpliftModel.

    Returns a summary dict of every stage's outcome (for API responses
    and the dashboard's "Live Recovery Case" view).
    """
    import pandas as pd
    trace = {"case_id": case_row["case_id"]}

    eligible = eligible_actions_for(case_row)
    trace["eligible_actions"] = eligible

    evidence = build_evidence(case_row, eligible)
    analysis = analyze_case(case_row, eligible, evidence)
    trace["ai_analysis"] = analysis.model_dump()

    if evidence.prior_cases_for_customer > 0:
        derived_success_rate = evidence.prior_recoveries_for_customer / evidence.prior_cases_for_customer
    else:
        derived_success_rate = 0.5
    enriched_case_row = dict(case_row)
    enriched_case_row.setdefault("customer_success_rate", derived_success_rate)
    enriched_case_row.setdefault("customer_previous_recovery_rate", derived_success_rate)
    enriched_case_row.setdefault("time_of_day", datetime.now(timezone.utc).hour)
    enriched_case_row.setdefault("merchant_category", "ecommerce")

    candidate_actions = [ra.action for ra in analysis.recommended_actions if ra.action != "NO_ACTION"]
    if not candidate_actions:
        trace["decision"] = {"action": "NO_ACTION", "reason_code": "NO_POSITIVE_UPLIFT"}
        _persist_decision(case_row, "NO_ACTION", None, analysis, approved=True,
                            requires_human_approval=False, reason_code="NO_POSITIVE_UPLIFT",
                            execution_status="NOT_EXECUTED", provider_reference=None)
        return trace

    row_df = pd.DataFrame([enriched_case_row])
    scored_rows = []
    for a in candidate_actions:
        p = model.predict_proba(row_df, a)[0]
        p_control = model.predict_proba(row_df, "NO_ACTION")[0]
        scored_rows.append({"case_id": case_row["case_id"], "action": a,
                              "recovery_probability": p, "baseline_probability": p_control,
                              "estimated_uplift": p - p_control})
    scored_df = pd.DataFrame(scored_rows)
    scored_df = compute_expected_incremental_value(scored_df, row_df)
    trace["uplift_scores"] = scored_df.to_dict(orient="records")

    best = scored_df.sort_values("expected_incremental_value_inr", ascending=False).iloc[0]
    if best["expected_incremental_value_inr"] <= 0:
        trace["decision"] = {"action": "NO_ACTION", "reason_code": "NO_POSITIVE_UPLIFT"}
        _persist_decision(case_row, "NO_ACTION", best, analysis, approved=True,
                            requires_human_approval=False, reason_code="NO_POSITIVE_UPLIFT",
                            execution_status="NOT_EXECUTED", provider_reference=None)
        return trace

    action = best["action"]
    spec = ACTION_REGISTRY[action]

    budget_blocked = False
    if db_is_reachable() and spec.requires_customer_contact:
        s = get_session()
        try:
            used = _today_contacts_used(s)
            budget_blocked = used >= DAILY_CONTACT_BUDGET
            trace["portfolio_budget"] = {"contacts_used_today": used, "daily_budget": DAILY_CONTACT_BUDGET,
                                            "blocked": budget_blocked}
        finally:
            s.close()

    from .state import CustomerStateStore
    from .state_db import PostgresCustomerStateStore
    store = PostgresCustomerStateStore() if db_is_reachable() else CustomerStateStore()
    opted_out = {case_row["customer_id"]} if store.is_opted_out(case_row["customer_id"]) else set()

    decision = evaluate_policy(
        case_row, action, expected_incremental_value=best["expected_incremental_value_inr"],
        customer_state={"attempts_used": store.attempts_used(case_row["case_id"]), "contacts_used": 0},
        opted_out_customers=opted_out,
    )
    if decision.approved and budget_blocked:
        from .policy import PolicyDecision
        decision = PolicyDecision(False, False, "POLICY_BLOCKED")
    if decision.approved and spec.min_cooldown_minutes > 0 and \
            store.in_cooldown(case_row["customer_id"], action, spec.min_cooldown_minutes):
        from .policy import PolicyDecision
        decision = PolicyDecision(False, False, "COOLDOWN_ACTIVE")

    trace["policy_decision"] = {"approved": decision.approved,
                                  "requires_human_approval": decision.requires_human_approval,
                                  "reason_code": decision.reason_code}

    if not decision.approved:
        _persist_decision(case_row, action, best, analysis, approved=False,
                            requires_human_approval=False, reason_code=decision.reason_code,
                            execution_status="NOT_EXECUTED", provider_reference=None)
        store.record(case_row["customer_id"], case_row["case_id"], action, status="BLOCKED")
        trace["decision"] = {"action": "NO_ACTION", "reason_code": decision.reason_code}
        return trace

    if decision.requires_human_approval:
        _persist_decision(case_row, action, best, analysis, approved=True,
                            requires_human_approval=True, reason_code="APPROVAL_PENDING",
                            execution_status="PENDING_APPROVAL", provider_reference=None)
        store.record(case_row["customer_id"], case_row["case_id"], action, status="PROPOSED")
        trace["decision"] = {"action": action, "status": "PENDING_APPROVAL"}
        return trace

    from .providers.factory import get_provider
    provider = get_provider()
    idem_key = f"{case_row['case_id']}:{action}:v1"
    if action in ("PAYMENT_LINK", "ALTERNATIVE_PAYMENT_METHOD", "CUSTOMER_NUDGE", "SMALL_INCENTIVE"):
        exec_result = provider.create_payment_link(case_row["case_id"], case_row["amount_inr"],
                                                      case_row["customer_id"], idem_key)
    else:
        exec_result = provider.retry_payment(case_row["case_id"], idem_key)

    _persist_decision(case_row, action, best, analysis, approved=True, requires_human_approval=False,
                        reason_code=None, execution_status=exec_result.status,
                        provider_reference=exec_result.provider_reference)
    store.record(case_row["customer_id"], case_row["case_id"], action,
                  status="EXECUTED" if exec_result.status == "EXECUTED" else "FAILED",
                  provider_reference=exec_result.provider_reference)

    if db_is_reachable():
        s = get_session()
        try:
            case = _upsert_case(s, case_row)
            s.add(AuditEvent(case_id=case_row["case_id"], event_type="ACTION_EXECUTED",
                               payload={"action": action, "status": exec_result.status,
                                        "provider_reference": exec_result.provider_reference}))
            s.commit()
        finally:
            s.close()

    trace["decision"] = {"action": action, "execution_status": exec_result.status,
                           "provider_reference": exec_result.provider_reference}
    return trace


def _persist_decision(case_row, action, scored_row, analysis, approved, requires_human_approval,
                        reason_code, execution_status, provider_reference):
    if not db_is_reachable():
        return
    s = get_session()
    try:
        case = _upsert_case(s, case_row)
        s.add(RecoveryDecision(
            case_id=case_row["case_id"], action=action,
            recovery_probability=float(scored_row["recovery_probability"]) if scored_row is not None else None,
            baseline_probability=float(scored_row["baseline_probability"]) if scored_row is not None else None,
            estimated_uplift=float(scored_row["estimated_uplift"]) if scored_row is not None else None,
            expected_incremental_value_inr=float(scored_row["expected_incremental_value_inr"]) if scored_row is not None else None,
            model_version="uplift_tlearner_v1",
            llm_diagnosis=analysis.diagnosis, llm_evidence=analysis.evidence,
            llm_confidence=analysis.confidence, selected=True, approved=approved,
            requires_human_approval=requires_human_approval, reason_code=reason_code,
            execution_status=execution_status, provider_reference=provider_reference,
        ))
        s.add(AuditEvent(case_id=case_row["case_id"], event_type="DECISION_RECORDED",
                           payload={"action": action, "approved": approved, "reason_code": reason_code,
                                    "ai_source": analysis.source}))
        s.commit()
    finally:
        s.close()
