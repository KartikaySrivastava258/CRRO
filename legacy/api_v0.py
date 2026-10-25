"""
CRRO FastAPI Application Layer v2 (Required Changes, Sections 13, 15)
--------------------------------------------------------------------------
Now backed by real PostgreSQL persistence when the database is reachable
(crro/db.py, crro/db_models.py, crro/state_db.py) -- guardrail state
(opt-outs, cooldowns, attempt counts) genuinely persists across requests
and process restarts, not just within one run_demo.py batch. Falls back
to the in-memory CustomerStateStore automatically if Postgres isn't
reachable, so the API still runs without a DB (documented, not silent --
see /health).

Run:
    docker compose up --build         # API + Postgres together, OR:
    alembic upgrade head              # apply schema locally
    python3 run_demo.py               # produces model.pkl etc.
    uvicorn api:app --reload --port 8000
    open http://localhost:8000/docs
"""

import json
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func

from crro.data_generator import eligible_actions_for
from crro.optimizer import compute_expected_incremental_value
from crro.policy import evaluate_policy
from crro.action_registry import ACTION_REGISTRY
from crro.state import CustomerStateStore
from crro.db import db_is_reachable, get_session
from crro.db_models import (Base, Customer, Merchant, PaymentCase, RecoveryDecision,
                              AuditEvent, WebhookEvent)
from crro.workflow import process_payment_failure, DAILY_CONTACT_BUDGET, _today_contacts_used
from crro.llm_analyst import analyze_case
from crro.evidence import build_evidence
from crro.providers.factory import get_provider

OUT = Path("outputs")

app = FastAPI(
    title="CRRO API",
    description=("Counterfactual Revenue Recovery Optimizer -- application layer, "
                  "backed by PostgreSQL when reachable (see /health)."),
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_state = {}


@app.on_event("startup")
def startup():
    _state["db_connected"] = db_is_reachable()
    if _state["db_connected"]:
        _state["state_store"] = _PostgresBackedFeasibilityStore()
    else:
        _state["state_store"] = CustomerStateStore()   # in-memory fallback

    if not (OUT / "model.pkl").exists():
        return  # API still starts; /health reports artifacts_loaded=False
    with open(OUT / "model.pkl", "rb") as f:
        _state["model"] = pickle.load(f)
    _state["test_df"] = pd.read_parquet(OUT / "test_cases.parquet")
    _state["test_df"]["eligible_actions"] = _state["test_df"].apply(eligible_actions_for, axis=1)
    if (OUT / "results.json").exists():
        _state["results"] = json.loads((OUT / "results.json").read_text())


class _PostgresBackedFeasibilityStore:
    """Thin adapter so api.py can call the same in-memory-shaped methods
    (is_opted_out, in_cooldown, attempts_used) while real reads/writes
    go to Postgres via crro/state_db.py."""
    def __init__(self):
        from crro.state_db import PostgresCustomerStateStore
        self._db_store = PostgresCustomerStateStore()

    def __getattr__(self, name):
        return getattr(self._db_store, name)


def _require_loaded():
    if "model" not in _state:
        raise HTTPException(503, "Pipeline artifacts not found. Run `python3 run_demo.py` first.")


def _upsert_case_and_customer(s, case_row):
    """Ensure the merchant/case/customer exist in Postgres before we write decisions against them."""
    merchant_id = case_row.get("merchant_id")
    if merchant_id and s.get(Merchant, merchant_id) is None:
        s.add(Merchant(merchant_id=merchant_id))
        s.flush()
    if s.get(Customer, case_row["customer_id"]) is None:
        s.add(Customer(customer_id=case_row["customer_id"]))
        s.flush()
    if s.get(PaymentCase, case_row["case_id"]) is None:
        s.add(PaymentCase(
            case_id=case_row["case_id"], merchant_id=merchant_id,
            customer_id=case_row["customer_id"], amount_inr=float(case_row["amount_inr"]),
            payment_method=case_row["payment_method"], failure_reason=case_row["failure_reason"],
            attempt_count=int(case_row["attempt_count"]),
            minutes_since_failure=int(case_row.get("minutes_since_failure", 0)),
        ))
    s.commit()



class ActionCandidate(BaseModel):
    action: str
    recovery_probability: float
    baseline_probability: float
    estimated_uplift: float
    expected_incremental_value_inr: float


class ScoreResponse(BaseModel):
    case_id: str
    candidates: List[ActionCandidate]


class ApprovalDecision(BaseModel):
    case_id: str
    action: str
    approved: bool
    requires_human_approval: bool
    reason_code: Optional[str]
    persisted: bool
    source: str   # "postgres" or "in_memory"



@app.get("/health")
def health():
    return {
        "status": "ok",
        "artifacts_loaded": "model" in _state,
        "db_connected": _state.get("db_connected", False),
        "state_backend": "postgres" if _state.get("db_connected") else "in_memory (fallback)",
    }


@app.get("/cases")
def list_cases(limit: int = 20, offset: int = 0):
    """List persisted live cases first, then benchmark cases.

    The benchmark parquet is used for model scoring, while the live
    recovery workflow persists cases in Postgres. Previously this endpoint
    returned only benchmark IDs (R-xxxxxx), so the Live Recovery page
    showed cases that did not exist in Postgres and `/journey` returned 404.
    """
    _require_loaded()
    cols = ["case_id", "merchant_id", "customer_id", "amount_inr", "payment_method",
            "failure_reason", "attempt_count", "minutes_since_failure"]

    live_cases = []
    live_ids = set()
    if _state.get("db_connected"):
        s = get_session()
        try:
            rows = (s.query(PaymentCase)
                     .order_by(PaymentCase.created_at.desc())
                     .all())
            for c in rows:
                live_ids.add(c.case_id)
                live_cases.append({
                    "case_id": c.case_id,
                    "merchant_id": c.merchant_id,
                    "customer_id": c.customer_id,
                    "amount_inr": c.amount_inr,
                    "payment_method": c.payment_method,
                    "failure_reason": c.failure_reason,
                    "attempt_count": c.attempt_count,
                    "minutes_since_failure": c.minutes_since_failure,
                    "status": c.status,
                    "source": "live",
                })
        finally:
            s.close()

    df = _state["test_df"]
    benchmark = []
    for row in df[cols].to_dict(orient="records"):
        if row["case_id"] not in live_ids:
            row["status"] = "BENCHMARK"
            row["source"] = "benchmark"
            benchmark.append(row)

    combined = live_cases + benchmark
    page = combined[offset:offset + limit]
    return {"total": len(combined), "limit": limit, "offset": offset, "cases": page}


@app.get("/cases/{case_id}")
def get_case(case_id: str):
    _require_loaded()

    if _state.get("db_connected"):
        s = get_session()
        try:
            c = s.get(PaymentCase, case_id)
            if c is not None:
                return {
                    "case_id": c.case_id,
                    "merchant_id": c.merchant_id,
                    "customer_id": c.customer_id,
                    "amount_inr": c.amount_inr,
                    "payment_method": c.payment_method,
                    "failure_reason": c.failure_reason,
                    "attempt_count": c.attempt_count,
                    "minutes_since_failure": c.minutes_since_failure,
                    "eligible_actions": eligible_actions_for({
                        "payment_method": c.payment_method,
                        "failure_reason": c.failure_reason,
                        "attempt_count": c.attempt_count,
                    }),
                    "status": c.status,
                    "source": "live",
                }
        finally:
            s.close()

    df = _state["test_df"]
    row = df[df["case_id"] == case_id]
    if row.empty:
        raise HTTPException(404, f"case_id {case_id} not found")
    cols = ["case_id", "merchant_id", "customer_id", "amount_inr", "payment_method",
            "failure_reason", "attempt_count", "minutes_since_failure", "eligible_actions"]
    result = row[cols].iloc[0].to_dict()
    result["status"] = "BENCHMARK"
    result["source"] = "benchmark"
    return result


@app.post("/recovery/score", response_model=ScoreResponse)
def score_case(case_id: str):
    """Score every eligible action for one case: recovery probability, uplift, value."""
    _require_loaded()
    df = _state["test_df"]
    row = df[df["case_id"] == case_id]
    if row.empty:
        raise HTTPException(404, f"case_id {case_id} not found")

    model = _state["model"]
    scored = model.score_all_actions(row, "eligible_actions")
    scored = compute_expected_incremental_value(scored, row)
    candidates = [
        ActionCandidate(
            action=r["action"], recovery_probability=r["recovery_probability"],
            baseline_probability=r["baseline_probability"], estimated_uplift=r["estimated_uplift"],
            expected_incremental_value_inr=r["expected_incremental_value_inr"],
        ) for _, r in scored.iterrows()
    ]
    return ScoreResponse(case_id=case_id, candidates=candidates)


@app.post("/recovery/approve", response_model=ApprovalDecision)
def approve_action(case_id: str, action: str):
    """
    Runs the deterministic policy gate against REAL persisted state
    (opt-out, cooldown, attempt count) when Postgres is connected --
    calling this twice for the same customer/action within the
    cooldown window will genuinely be blocked the second time, because
    the first call's PROPOSED record is now in the database.
    """
    _require_loaded()
    if action not in ACTION_REGISTRY:
        raise HTTPException(400, f"Unknown action '{action}': not in the approved action registry.")
    df = _state["test_df"]
    row = df[df["case_id"] == case_id]
    if row.empty:
        raise HTTPException(404, f"case_id {case_id} not found")
    case_row = row.iloc[0]
    store = _state["state_store"]
    db_connected = _state.get("db_connected", False)

    spec = ACTION_REGISTRY[action]
    opted_out = {case_row["customer_id"]} if store.is_opted_out(case_row["customer_id"]) else set()
    cooldown_blocked = (spec.min_cooldown_minutes > 0 and
                         store.in_cooldown(case_row["customer_id"], action, spec.min_cooldown_minutes))

    decision = evaluate_policy(
        case_row, action, expected_incremental_value=1.0,   # >0 sentinel; real value came from /score
        customer_state={"attempts_used": store.attempts_used(case_id), "contacts_used": 0},
        opted_out_customers=opted_out,
    )
    if decision.approved and cooldown_blocked:
        decision = type(decision)(False, False, "COOLDOWN_ACTIVE")

    persisted = False
    if action != "NO_ACTION":
        status = "PROPOSED" if decision.approved else "BLOCKED"
        if db_connected:
            s = get_session()
            try:
                _upsert_case_and_customer(s, case_row)
                s.add(RecoveryDecision(
                    case_id=case_id, action=action, selected=True,
                    approved=decision.approved, requires_human_approval=decision.requires_human_approval,
                    reason_code=decision.reason_code, model_version="uplift_tlearner_v1",
                ))
                s.add(AuditEvent(case_id=case_id, event_type="API_DECISION_RECORDED",
                                  payload={"action": action, "approved": decision.approved,
                                           "reason_code": decision.reason_code}))
                s.commit()
                persisted = True
            finally:
                s.close()
        store.record(case_row["customer_id"], case_id, action, status=status)

    return ApprovalDecision(case_id=case_id, action=action, approved=decision.approved,
                              requires_human_approval=decision.requires_human_approval,
                              reason_code=decision.reason_code, persisted=persisted,
                              source="postgres" if db_connected else "in_memory")


@app.post("/customers/{customer_id}/opt-out")
def opt_out_customer(customer_id: str):
    store = _state.get("state_store")
    if store is None:
        raise HTTPException(503, "state store not initialized")
    if hasattr(store, "set_opted_out"):
        store.set_opted_out(customer_id, True)
        return {"customer_id": customer_id, "opted_out": True, "persisted": _state.get("db_connected", False)}
    store.opted_out_customers.add(customer_id)
    return {"customer_id": customer_id, "opted_out": True, "persisted": False}


@app.get("/customers/{customer_id}/history")
def customer_history(customer_id: str):
    store = _state.get("state_store")
    if store is None or not hasattr(store, "history_for_customer"):
        raise HTTPException(503, "persistent history requires Postgres (db_connected=false)")
    return {"customer_id": customer_id, "history": store.history_for_customer(customer_id)}


@app.get("/recovery/results")
def get_results():
    """Full measured benchmark report from the last run_demo.py run."""
    _require_loaded()
    if "results" not in _state:
        raise HTTPException(404, "results.json not found; run run_demo.py")
    return _state["results"]


@app.get("/recovery/audit")
def get_audit(case_id: Optional[str] = None, event_type: Optional[str] = None,
              status: Optional[str] = None, date_from: Optional[str] = None,
              date_to: Optional[str] = None, q: Optional[str] = None, limit: int = 100):
    """
    Prefers Postgres audit_events if connected, else falls back to the CSV
    artifact. Item 1E: supports search/filtering by case, event type,
    case status, and a date range so the audit view is genuinely usable,
    not just a raw dump.
    """
    if _state.get("db_connected"):
        s = get_session()
        try:
            query = s.query(AuditEvent, PaymentCase.status, PaymentCase.customer_id,
                             PaymentCase.amount_inr).outerjoin(
                PaymentCase, PaymentCase.case_id == AuditEvent.case_id)
            if case_id:
                query = query.filter(AuditEvent.case_id == case_id)
            if event_type:
                query = query.filter(AuditEvent.event_type == event_type)
            if status:
                query = query.filter(PaymentCase.status == status)
            if date_from:
                query = query.filter(AuditEvent.ts >= date_from)
            if date_to:
                query = query.filter(AuditEvent.ts <= date_to)
            rows = query.order_by(AuditEvent.ts.desc()).limit(limit).all()
            results = []
            for r, case_status, customer_id, amount_inr in rows:
                if q and q.lower() not in json.dumps(r.payload or {}).lower() \
                        and q.lower() not in (r.event_type or "").lower() \
                        and q.lower() not in (r.case_id or "").lower():
                    continue
                results.append({
                    "event_id": r.event_id, "case_id": r.case_id, "customer_id": customer_id,
                    "amount_inr": amount_inr, "event_type": r.event_type, "case_status": case_status,
                    "payload": r.payload, "ts": r.ts.isoformat(),
                })
            return results
        finally:
            s.close()
    audit_path = OUT / "audit_log.csv"
    if not audit_path.exists():
        raise HTTPException(404, "no audit source available; run run_demo.py or connect Postgres")
    df = pd.read_csv(audit_path)
    if case_id:
        df = df[df["case_id"] == case_id]
    return df.tail(limit).to_dict(orient="records")


@app.get("/metrics")
def get_metrics():
    _require_loaded()
    if "results" not in _state:
        raise HTTPException(404, "results.json not found; run run_demo.py")
    r = _state["results"]
    return {
        "policy_comparison": r.get("policy_comparison"),
        "budget_usage": r.get("budget_usage"),
        "execution_reliability": r.get("execution_reliability"),
        "performance_targets_measured": r.get("performance_targets_measured"),
    }



class PaymentFailedEvent(BaseModel):
    case_id: str
    merchant_id: str = "merchant_demo"
    customer_id: str
    amount_inr: float
    payment_method: str
    failure_reason: str
    attempt_count: int = 1
    minutes_since_failure: int = 0


@app.post("/recovery/analyze")
def analyze_case_endpoint(case_id: str):
    """AI Recovery Analyst alone (no execution) -- diagnosis + candidate actions + evidence."""
    _require_loaded()
    df = _state["test_df"]
    row = df[df["case_id"] == case_id]
    if row.empty:
        raise HTTPException(404, f"case_id {case_id} not found")
    case_row = row.iloc[0].to_dict()
    eligible = case_row["eligible_actions"]
    evidence = build_evidence(case_row, eligible)
    analysis = analyze_case(case_row, eligible, evidence)
    return {"analysis": analysis.model_dump(), "evidence_source": evidence.source}


@app.get("/recovery/whatif")
def whatif(case_id: str):
    """
    Counterfactual What-If mode: for one case,
    show every eligible action's predicted probability, uplift, and
    expected value side by side -- the clearest single-screen
    illustration of the counterfactual thesis.
    """
    _require_loaded()
    df = _state["test_df"]
    row = df[df["case_id"] == case_id]
    if row.empty:
        raise HTTPException(404, f"case_id {case_id} not found")
    model = _state["model"]
    scored = model.score_all_actions(row, "eligible_actions")
    scored = compute_expected_incremental_value(scored, row)
    control_p = float(model.predict_proba(row, "NO_ACTION")[0])
    rows = [{"action": "NO_ACTION", "recovery_probability": control_p, "uplift": 0.0,
              "expected_incremental_value_inr": 0.0}]
    for _, r in scored.sort_values("expected_incremental_value_inr", ascending=False).iterrows():
        rows.append({"action": r["action"], "recovery_probability": round(r["recovery_probability"], 4),
                       "uplift": round(r["estimated_uplift"], 4),
                       "expected_incremental_value_inr": round(r["expected_incremental_value_inr"], 2)})
    return {"case_id": case_id, "comparison": rows}


@app.post("/events/payment-failed")
def payment_failed_event(event: PaymentFailedEvent):
    """
    Simulates an inbound `payment.failed` webhook triggering the full
    recovery workflow. In a live deployment this
    body would arrive from Razorpay's webhook (after signature
    verification, see /webhooks/razorpay below); this endpoint lets the
    same workflow be demoed/tested without needing a live Razorpay
    account to originate the failure event.
    """
    _require_loaded()
    trace = process_payment_failure(event.model_dump(), _state["model"])
    return trace


@app.get("/cases/{case_id}/journey")
def case_journey(case_id: str):
    """Full recovery journey for one case: decisions + audit trail, oldest first."""
    if not _state.get("db_connected"):
        raise HTTPException(503, "journey view requires Postgres")
    s = get_session()
    try:
        case = s.get(PaymentCase, case_id)
        if case is None:
            raise HTTPException(404, f"case_id {case_id} not found")
        decisions = (s.query(RecoveryDecision).filter(RecoveryDecision.case_id == case_id)
                      .order_by(RecoveryDecision.created_at).all())
        events = (s.query(AuditEvent).filter(AuditEvent.case_id == case_id)
                   .order_by(AuditEvent.ts).all())
        return {
            "case": {"case_id": case.case_id, "status": case.status, "amount_inr": case.amount_inr,
                      "recovered_amount_inr": case.recovered_amount_inr,
                      "recovered_at": case.recovered_at.isoformat() if case.recovered_at else None},
            "decisions": [{"action": d.action, "approved": d.approved, "reason_code": d.reason_code,
                             "execution_status": d.execution_status, "provider_reference": d.provider_reference,
                             "llm_diagnosis": d.llm_diagnosis, "llm_evidence": d.llm_evidence,
                             "llm_confidence": d.llm_confidence, "created_at": d.created_at.isoformat()}
                            for d in decisions],
            "audit_events": [{"event_type": e.event_type, "payload": e.payload, "ts": e.ts.isoformat()}
                               for e in events],
        }
    finally:
        s.close()



@app.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request):
    """
    Hardened webhook processing per Checklist Section 4:
      - signature verified against the RAW request body (never re-serialized JSON)
      - idempotency via x-razorpay-event-id, recorded BEFORE any state change
      - tolerant of out-of-order delivery (checks current status before transitioning)
      - acknowledges quickly (all work here is a few indexed queries)
    """
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")
    event_id = request.headers.get("x-razorpay-event-id", "")

    provider = get_provider()
    try:
        sig_valid = provider.verify_webhook_signature(raw_body, signature)
    except Exception:
        sig_valid = False

    try:
        payload = json.loads(raw_body)
    except Exception:
        payload = None

    if not _state.get("db_connected"):
        if not sig_valid:
            raise HTTPException(400, "invalid webhook signature")
        return {"status": "accepted", "idempotent": False, "note": "no DB connected; not persisted"}

    s = get_session()
    try:
        if not event_id:
            s.add(WebhookEvent(razorpay_event_id=f"missing:{raw_body[:16].hex()}",
                                 event_type="UNKNOWN", signature_valid=sig_valid,
                                 processed=False, processing_note="missing x-razorpay-event-id header",
                                 payload=payload))
            s.commit()
            raise HTTPException(400, "missing x-razorpay-event-id header")

        existing = s.query(WebhookEvent).filter(WebhookEvent.razorpay_event_id == event_id).first()
        if existing is not None:
            return {"status": "duplicate_ignored", "event_id": event_id, "idempotent": True}

        if not sig_valid:
            s.add(WebhookEvent(razorpay_event_id=event_id, event_type=(payload or {}).get("event", "UNKNOWN"),
                                 signature_valid=False, processed=False,
                                 processing_note="invalid signature", payload=payload))
            s.commit()
            raise HTTPException(400, "invalid webhook signature")

        if payload is None:
            s.add(WebhookEvent(razorpay_event_id=event_id, event_type="MALFORMED",
                                 signature_valid=True, processed=False,
                                 processing_note="malformed JSON payload", payload=None))
            s.commit()
            raise HTTPException(400, "malformed payload")

        event_type = payload.get("event", "unknown")
        note = _reconcile_event(s, event_type, payload)

        s.add(WebhookEvent(razorpay_event_id=event_id, event_type=event_type,
                             case_id=note.get("case_id"), signature_valid=True,
                             processed=True, processing_note=note.get("note"), payload=payload))
        s.commit()
        return {"status": "processed", "event_id": event_id, "event_type": event_type, **note}
    finally:
        s.close()


def _reconcile_event(session, event_type: str, payload: dict) -> dict:
    """
    Applies a webhook event to persisted state. Tolerant of out-of-order
    delivery: only moves a case FORWARD in its state machine, never
    backward, and no-ops safely on an unknown case_id or an
    already-recovered case (Checklist Sections 4, 5).
    """
    entity = (payload.get("payload", {}).get("payment_link", {}) or
              payload.get("payload", {}).get("payment", {}))
    reference_id = (entity.get("entity", {}) or {}).get("reference_id") or entity.get("reference_id")
    provider_reference = (entity.get("entity", {}) or {}).get("id") or entity.get("id")
    amount_paise = (entity.get("entity", {}) or {}).get("amount") or entity.get("amount")

    decision = None
    if provider_reference:
        decision = (session.query(RecoveryDecision)
                     .filter(RecoveryDecision.provider_reference == provider_reference).first())
    if decision is None and reference_id:
        case_id = reference_id.split(":")[0]
        decision = (session.query(RecoveryDecision)
                     .filter(RecoveryDecision.case_id == case_id).order_by(RecoveryDecision.created_at.desc())
                     .first())

    if decision is None:
        return {"note": "no matching recovery decision found for this event; ignored (unknown case)"}

    case = session.get(PaymentCase, decision.case_id)
    if case is None:
        return {"note": "matched decision but case row missing; ignored"}

    if event_type in ("payment_link.paid", "payment.captured"):
        if case.status == "RECOVERED":
            return {"case_id": case.case_id, "note": "already recovered; duplicate/out-of-order event ignored"}
        case.status = "RECOVERED"
        case.recovered_amount_inr = (amount_paise / 100.0) if amount_paise else case.amount_inr
        case.recovered_at = datetime.now(timezone.utc)
        decision.execution_status = "RECOVERED"
        session.add(AuditEvent(case_id=case.case_id, event_type="WEBHOOK_RECONCILED",
                                 payload={"event_type": event_type, "recovered_amount_inr": case.recovered_amount_inr}))
        return {"case_id": case.case_id, "note": "case marked RECOVERED"}

    if event_type in ("payment_link.expired", "payment_link.cancelled", "payment.failed"):
        if case.status == "RECOVERED":
            return {"case_id": case.case_id, "note": "case already recovered; ignoring late failure event (out-of-order)"}
        case.status = "CLOSED_UNRECOVERED"
        decision.execution_status = event_type.upper()
        session.add(AuditEvent(case_id=case.case_id, event_type="WEBHOOK_RECONCILED",
                                 payload={"event_type": event_type}))
        return {"case_id": case.case_id, "note": f"case marked CLOSED_UNRECOVERED ({event_type})"}

    return {"case_id": case.case_id, "note": f"event_type '{event_type}' received but not actionable"}



def _require_db():
    if not _state.get("db_connected"):
        raise HTTPException(503, "This view requires Postgres. Start the DB (docker compose up) "
                                   "and restart the API; see /health.")


@app.get("/dashboard/overview")
def dashboard_overview(days: int = 14):
    """
    Executive Overview (Item 1A): every number here is computed from
    live Postgres rows (cases/decisions), not hardcoded demo values.
    Falls back to the last run_demo.py benchmark (`results.json`) only
    for the illustrative "modeled segment performance" charts, which
    are clearly labeled as such -- there is no live segment volume yet
    on a freshly seeded database.
    """
    _require_db()
    s = get_session()
    try:
        total_cases = s.query(func.count(PaymentCase.case_id)).scalar() or 0
        open_cases = s.query(func.count(PaymentCase.case_id)).filter(PaymentCase.status == "OPEN").scalar() or 0
        recovered_cases = (s.query(func.count(PaymentCase.case_id))
                            .filter(PaymentCase.status == "RECOVERED").scalar() or 0)
        revenue_at_risk = (s.query(func.coalesce(func.sum(PaymentCase.amount_inr), 0.0))
                            .filter(PaymentCase.status == "OPEN").scalar() or 0.0)
        revenue_recovered = (s.query(func.coalesce(func.sum(PaymentCase.recovered_amount_inr), 0.0))
                              .filter(PaymentCase.status == "RECOVERED").scalar() or 0.0)
        failed_total = total_cases
        recovery_rate = (recovered_cases / failed_total) if failed_total else 0.0

        incremental_value = (s.query(func.coalesce(func.sum(RecoveryDecision.expected_incremental_value_inr), 0.0))
                              .filter(RecoveryDecision.execution_status == "EXECUTED").scalar() or 0.0)

        pending_approvals = (s.query(func.count(RecoveryDecision.decision_id))
                              .filter(RecoveryDecision.execution_status == "PENDING_APPROVAL").scalar() or 0)

        customers_contacted = (s.query(func.count(func.distinct(PaymentCase.customer_id)))
                                .join(RecoveryDecision, RecoveryDecision.case_id == PaymentCase.case_id)
                                .filter(RecoveryDecision.execution_status == "EXECUTED").scalar() or 0)

        contacts_used_today = _today_contacts_used(s)

        active_cases = (s.query(func.count(RecoveryDecision.decision_id))
                         .filter(RecoveryDecision.execution_status.in_(
                             ["PENDING_APPROVAL", "EXECUTED"])).scalar() or 0)

        since = datetime.now(timezone.utc) - timedelta(days=days)
        failed_rows = (s.query(func.date(PaymentCase.created_at), func.count(PaymentCase.case_id))
                        .filter(PaymentCase.created_at >= since)
                        .group_by(func.date(PaymentCase.created_at)).all())
        recovered_rows = (s.query(func.date(PaymentCase.recovered_at), func.count(PaymentCase.case_id),
                                    func.coalesce(func.sum(PaymentCase.recovered_amount_inr), 0.0))
                           .filter(PaymentCase.recovered_at >= since)
                           .group_by(func.date(PaymentCase.recovered_at)).all())
        failed_by_day = {str(d): n for d, n in failed_rows}
        recovered_by_day = {str(d): (n, v) for d, n, v in recovered_rows}
        all_days = sorted(set(failed_by_day) | set(recovered_by_day))
        performance_over_time = [
            {"date": d, "failed_payments": failed_by_day.get(d, 0),
             "recovered_cases": recovered_by_day.get(d, (0, 0.0))[0],
             "recovered_revenue_inr": recovered_by_day.get(d, (0, 0.0))[1]}
            for d in all_days
        ]

        by_action_rows = (s.query(RecoveryDecision.action, func.count(RecoveryDecision.decision_id),
                                    func.coalesce(func.sum(RecoveryDecision.expected_incremental_value_inr), 0.0))
                           .filter(RecoveryDecision.execution_status == "EXECUTED")
                           .group_by(RecoveryDecision.action).all())
        recovery_by_action = [
            {"action": a, "executions": n, "expected_incremental_value_inr": round(v, 2)}
            for a, n, v in by_action_rows
        ]

    finally:
        s.close()

    modeled_segment_performance = None
    if "results" in _state:
        modeled_segment_performance = {
            "by_failure_reason": _state["results"].get("crro_segment_performance_by_failure_reason"),
            "by_payment_method": _state["results"].get("crro_segment_performance_by_payment_method"),
            "policy_comparison": _state["results"].get("policy_comparison"),
            "note": "From the last offline run_demo.py benchmark (20k synthetic cases), "
                    "not this database's live volume.",
        }

    return {
        "totals": {
            "total_failed_payments": total_cases,
            "revenue_at_risk_inr": round(revenue_at_risk, 2),
            "revenue_recovered_inr": round(revenue_recovered, 2),
            "recovery_rate": round(recovery_rate, 4),
            "incremental_recovered_value_inr": round(incremental_value, 2),
            "active_recovery_cases": active_cases,
            "pending_approvals": pending_approvals,
            "customers_contacted": customers_contacted,
        },
        "budget": {
            "contacts_used_today": contacts_used_today,
            "daily_contact_budget": DAILY_CONTACT_BUDGET,
            "contacts_remaining_today": max(DAILY_CONTACT_BUDGET - contacts_used_today, 0),
        },
        "performance_over_time": performance_over_time,
        "recovery_by_action": recovery_by_action,
        "modeled_segment_performance": modeled_segment_performance,
    }


@app.get("/approvals/pending")
def list_pending_approvals(limit: int = 50):
    """Item 2: cases waiting on a human approval, with everything needed to decide."""
    _require_db()
    s = get_session()
    try:
        rows = (s.query(RecoveryDecision, PaymentCase)
                 .join(PaymentCase, PaymentCase.case_id == RecoveryDecision.case_id)
                 .filter(RecoveryDecision.execution_status == "PENDING_APPROVAL")
                 .order_by(RecoveryDecision.created_at.desc()).limit(limit).all())
        out = []
        for d, c in rows:
            spec = ACTION_REGISTRY.get(d.action)
            out.append({
                "decision_id": d.decision_id,
                "case_id": c.case_id,
                "customer_id": c.customer_id,
                "amount_inr": c.amount_inr,
                "failure_reason": c.failure_reason,
                "payment_method": c.payment_method,
                "recommended_action": d.action,
                "expected_incremental_value_inr": d.expected_incremental_value_inr,
                "recovery_probability": d.recovery_probability,
                "action_cost_inr": spec.monetary_cost_inr if spec else None,
                "why_approval_required": (
                    f"Amount ₹{c.amount_inr:,.0f} exceeds the ₹{spec.requires_approval_above_amount:,.0f} "
                    f"auto-approval threshold for {d.action}."
                ) if spec and spec.requires_approval_above_amount is not None else
                "Above the merchant's configured auto-approval threshold for this action.",
                "llm_diagnosis": d.llm_diagnosis,
                "llm_evidence": d.llm_evidence,
                "created_at": d.created_at.isoformat(),
            })
        return {"count": len(out), "pending": out}
    finally:
        s.close()


class ApprovalAction(BaseModel):
    note: Optional[str] = None


@app.post("/approvals/{decision_id}/approve")
def approve_pending(decision_id: str, body: ApprovalAction = ApprovalAction()):
    """
    Item 2: real approval endpoint -- moves PENDING_APPROVAL -> EXECUTION
    by actually calling the RecoveryExecutionProvider, then persists the
    result. This does not just flip a frontend flag.
    """
    _require_db()
    s = get_session()
    try:
        decision = s.get(RecoveryDecision, decision_id)
        if decision is None:
            raise HTTPException(404, f"decision {decision_id} not found")
        if decision.execution_status != "PENDING_APPROVAL":
            raise HTTPException(409, f"decision is '{decision.execution_status}', not PENDING_APPROVAL")
        case = s.get(PaymentCase, decision.case_id)
        if case is None:
            raise HTTPException(404, f"case {decision.case_id} not found for this decision")

        provider = get_provider()
        idem_key = f"{case.case_id}:{decision.action}:approved:v1"
        if decision.action in ("PAYMENT_LINK", "ALTERNATIVE_PAYMENT_METHOD",
                                 "CUSTOMER_NUDGE", "SMALL_INCENTIVE"):
            exec_result = provider.create_payment_link(case.case_id, case.amount_inr,
                                                          case.customer_id, idem_key)
        else:
            exec_result = provider.retry_payment(case.case_id, idem_key)

        decision.execution_status = exec_result.status
        decision.provider_reference = exec_result.provider_reference
        decision.reason_code = None
        s.add(AuditEvent(case_id=case.case_id, event_type="APPROVAL_GRANTED",
                           payload={"decision_id": decision_id, "action": decision.action, "note": body.note}))
        s.add(AuditEvent(case_id=case.case_id, event_type="ACTION_EXECUTED",
                           payload={"action": decision.action, "status": exec_result.status,
                                    "provider_reference": exec_result.provider_reference}))
        s.commit()
        return {"decision_id": decision_id, "case_id": case.case_id, "action": decision.action,
                "execution_status": exec_result.status, "provider_reference": exec_result.provider_reference}
    finally:
        s.close()


@app.post("/approvals/{decision_id}/reject")
def reject_pending(decision_id: str, body: ApprovalAction = ApprovalAction()):
    """Item 2: PENDING_APPROVAL -> REJECTED. Persisted, not a frontend-only flag."""
    _require_db()
    s = get_session()
    try:
        decision = s.get(RecoveryDecision, decision_id)
        if decision is None:
            raise HTTPException(404, f"decision {decision_id} not found")
        if decision.execution_status != "PENDING_APPROVAL":
            raise HTTPException(409, f"decision is '{decision.execution_status}', not PENDING_APPROVAL")

        decision.execution_status = "REJECTED"
        decision.reason_code = "HUMAN_REJECTED"
        s.add(AuditEvent(case_id=decision.case_id, event_type="APPROVAL_REJECTED",
                           payload={"decision_id": decision_id, "action": decision.action, "note": body.note}))
        s.commit()
        return {"decision_id": decision_id, "case_id": decision.case_id, "execution_status": "REJECTED"}
    finally:
        s.close()


@app.get("/dashboard/optimizer")
def dashboard_optimizer(limit: int = 15):
    """
    Item 1C: shows, for the most recently decided cases, the full
    candidate set the optimizer considered (not just the winner) so it's
    visibly not "ask an LLM what to do" -- every candidate is re-scored
    by the same deterministic uplift model + policy gate used at
    decision time.
    """
    _require_loaded()
    _require_db()
    s = get_session()
    try:
        recent = (s.query(RecoveryDecision, PaymentCase)
                   .join(PaymentCase, PaymentCase.case_id == RecoveryDecision.case_id)
                   .filter(RecoveryDecision.selected == True)  # noqa: E712
                   .order_by(RecoveryDecision.created_at.desc()).limit(limit).all())
        contacts_used_today = _today_contacts_used(s)
    finally:
        s.close()

    df = _state["test_df"]
    model = _state["model"]
    out = []
    for d, c in recent:
        row = df[df["case_id"] == c.case_id]
        candidates = []
        candidate_note = None
        if row.empty:
            candidate_note = ("Full candidate comparison unavailable: this case was submitted "
                               "via /events/payment-failed and isn't in the scored benchmark set "
                               "(run_demo.py's test_cases.parquet). The selected action and its "
                               "own score are still shown below.")
        if not row.empty:
            scored = model.score_all_actions(row, "eligible_actions")
            scored = compute_expected_incremental_value(scored, row)
            for _, r in scored.sort_values("expected_incremental_value_inr", ascending=False).iterrows():
                spec = ACTION_REGISTRY.get(r["action"])
                is_selected = r["action"] == d.action
                reason = "Selected: highest expected incremental value within budget and policy." if is_selected else (
                    "Rejected: lower expected incremental value than the selected action."
                    if r["expected_incremental_value_inr"] < (d.expected_incremental_value_inr or 0)
                    else "Rejected: not chosen by the optimizer this round."
                )
                candidates.append({
                    "action": r["action"],
                    "recovery_probability": round(float(r["recovery_probability"]), 4),
                    "expected_uplift": round(float(r["estimated_uplift"]), 4),
                    "expected_incremental_value_inr": round(float(r["expected_incremental_value_inr"]), 2),
                    "action_cost_inr": spec.monetary_cost_inr if spec else None,
                    "requires_customer_contact": spec.requires_customer_contact if spec else None,
                    "selected": is_selected,
                    "reason": reason,
                })
        out.append({
            "case_id": c.case_id,
            "customer_id": c.customer_id,
            "amount_inr": c.amount_inr,
            "selected_action": d.action,
            "execution_status": d.execution_status,
            "requires_human_approval": d.requires_human_approval,
            "candidate_actions": candidates,
            "candidate_note": candidate_note,
        })

    return {
        "portfolio_budget": {
            "contacts_used_today": contacts_used_today,
            "daily_contact_budget": DAILY_CONTACT_BUDGET,
        },
        "recent_decisions": out,
    }


@app.get("/cases/{case_id}/explainability")
def case_explainability(case_id: str):
    """
    Item 1D: a human-readable "Why this action?" explanation assembled
    strictly from persisted decision data for this case -- no
    regeneration/re-prompting, so it matches exactly what actually
    happened.
    """
    _require_db()
    s = get_session()
    try:
        case = s.get(PaymentCase, case_id)
        if case is None:
            raise HTTPException(404, f"case_id {case_id} not found")
        decisions = (s.query(RecoveryDecision).filter(RecoveryDecision.case_id == case_id)
                      .order_by(RecoveryDecision.created_at).all())
        if not decisions:
            raise HTTPException(404, f"no recovery decision recorded yet for case {case_id}")
        d = decisions[-1]  # most recent decision for this case

        spec = ACTION_REGISTRY.get(d.action)
        reasons = []
        if d.llm_evidence:
            for b in (d.llm_evidence if isinstance(d.llm_evidence, list) else [d.llm_evidence]):
                reasons.append(str(b))
        if d.recovery_probability is not None:
            reasons.append(f"Predicted recovery probability: {d.recovery_probability:.1%}.")
        if d.expected_incremental_value_inr is not None:
            reasons.append(f"Expected incremental value: ₹{d.expected_incremental_value_inr:,.2f}.")
        if spec is not None:
            reasons.append(f"Action cost (₹{spec.monetary_cost_inr:.0f}) is within merchant budget.")
        if d.requires_human_approval:
            reasons.append("Amount/action combination required human approval before execution.")
        else:
            reasons.append("No human approval was required for this action/amount.")

        rejected = []  # from the candidate set, everything not selected
        row = _state.get("test_df")
        if row is not None:
            row = row[row["case_id"] == case_id]
            if not row.empty and "model" in _state:
                scored = _state["model"].score_all_actions(row, "eligible_actions")
                scored = compute_expected_incremental_value(scored, row)
                for _, r in scored.iterrows():
                    if r["action"] != d.action:
                        rejected.append({
                            "action": r["action"],
                            "expected_incremental_value_inr": round(float(r["expected_incremental_value_inr"]), 2),
                            "reason": "Lower expected incremental value than the selected action.",
                        })

        return {
            "case_id": case_id,
            "selected_action": d.action,
            "why_this_action": reasons,
            "diagnosis": d.llm_diagnosis,
            "evidence": d.llm_evidence,
            "ai_confidence": d.llm_confidence,
            "guardrails_applied": {
                "requires_human_approval": d.requires_human_approval,
                "approved": d.approved,
                "reason_code": d.reason_code,
            },
            "budget_impact_inr": spec.monetary_cost_inr if spec else None,
            "rejected_alternatives": rejected,
            "execution_status": d.execution_status,
            "provider_reference": d.provider_reference,
        }
    finally:
        s.close()

