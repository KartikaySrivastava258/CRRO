"""
LedgerService: the authoritative payment-recovery lifecycle.

Consistency model (stated precisely, see docs/ARCHITECTURE.md):
  * Every state change + its audit record commit in ONE database transaction.
  * Inbound requests are idempotent: `payment.failed` via (merchant, scope,
    Idempotency-Key) with a request hash, plus a business-key unique constraint
    (merchant, external_payment_id).
  * Webhooks are deduplicated by UNIQUE(provider, provider_event_id), inserted
    in the same transaction as the reconciliation side effects.
  * Recovered value is recorded in `recoveries` with UNIQUE(case_id) and
    UNIQUE(provider_payment_id): a case can be credited at most once and a
    provider payment can credit at most one case.
  * Provider dispatch is at-least-once with a stable idempotency key that the
    provider dedupes/looks up (outbox-style: intent row committed BEFORE the
    call, result recorded AFTER). This is NOT exactly-once delivery; it gives
    idempotent effects provided the provider honours the key.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from ..action_registry import ACTION_REGISTRY
from ..money import MoneyError, rupees_to_paise, validate_currency, validate_paise
from .db import Database, UniqueViolation, loads
from .errors import (BadRequest, Conflict, IdempotencyConflict, NotFound, PayloadTooLarge,
                     Unauthorized, ValidationFailed)
from .states import TERMINAL, can_transition

RETRY_ACTIONS = {"RETRY_NOW", "RETRY_LATER"}
LINK_ACTIONS = {"PAYMENT_LINK", "ALTERNATIVE_PAYMENT_METHOD", "CUSTOMER_NUDGE", "SMALL_INCENTIVE"}
OPEN_EXPOSURE = ("OPEN", "PENDING_APPROVAL", "ACTION_QUEUED", "PROVIDER_PENDING",
                 "OUTCOME_UNKNOWN", "NEEDS_REVIEW")
_ID_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,64}$")
_CAT_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

DEFAULT_POLICY = {
    "daily_contact_budget": 500,
    "daily_incentive_budget_paise": 5_000_000,      # ₹50,000
    "per_customer_daily_contact_cap": 1,
    "allowed_actions": [a for a in ACTION_REGISTRY if a != "NO_ACTION"],
    # Approval threshold per action (paise); default from the action registry.
    "approval_threshold_paise": {a: int(s.requires_approval_above_amount * 100)
                                 for a, s in ACTION_REGISTRY.items()
                                 if s.requires_approval_above_amount is not None},
    "approval_ttl_hours": 24,
    "default_merchant_category": "ecommerce",
}


def _hash_request(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _encode_cursor(*parts) -> str:
    return base64.urlsafe_b64encode("|".join(str(p) for p in parts).encode()).decode()


def _decode_cursor(cursor: Optional[str], n: int):
    if not cursor:
        return None
    try:
        parts = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
    except Exception:
        raise BadRequest("invalid cursor") from None
    if len(parts) != n:
        raise BadRequest("invalid cursor")
    return parts


def idempotency_key_for(case_id: str, action: str, n: int = 1) -> str:
    """Stable, provider-safe (<=40 chars) key for one logical action attempt."""
    return "crro_" + hashlib.sha256(f"{case_id}:{action}:{n}".encode()).hexdigest()[:24]


class LedgerService:
    def __init__(self, db: Database, provider, clock: Optional[Callable[[], datetime]] = None,
                 webhook_max_bytes: int = 64 * 1024):
        self.db = db
        self.provider = provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.webhook_max_bytes = webhook_max_bytes

    # ------------------------------------------------------------------ utils
    def now(self) -> datetime:
        return self._clock()

    def now_iso(self) -> str:
        return self.now().astimezone(timezone.utc).isoformat(timespec="microseconds")

    def _for_update(self) -> str:
        return " FOR UPDATE" if self.db.dialect == "postgresql" else ""

    def _audit(self, tx, event_type, actor, *, merchant_id=None, case_id=None,
               correlation_id=None, data=None):
        tx.execute("INSERT INTO audit_log (audit_id, ts, merchant_id, case_id, correlation_id, event_type,"
                   " actor, data) VALUES (?,?,?,?,?,?,?,?)",
                   ["aud_" + uuid.uuid4().hex[:20], self.now_iso(), merchant_id, case_id, correlation_id,
                    event_type, actor, data or {}])

    def _lock_case(self, tx, case_id, merchant_id=None) -> dict:
        sql = "SELECT * FROM payment_cases WHERE case_id = ?"
        params = [case_id]
        if merchant_id is not None:
            sql += " AND merchant_id = ?"
            params.append(merchant_id)
        row = tx.one(sql + self._for_update(), params)
        if row is None:
            raise NotFound(f"case {case_id} not found")
        return row

    def _set_case_status(self, tx, case: dict, target: str, reason: Optional[str], actor: str,
                         event_data: Optional[dict] = None, provider_event_at: Optional[str] = None):
        if not can_transition(case["status"], target):
            raise Conflict(f"invalid case transition {case['status']} -> {target}",
                           current=case["status"], target=target)
        n = tx.execute("UPDATE payment_cases SET status = ?, status_reason = ?, version = version + 1,"
                       " updated_at = ?, last_provider_event_at = COALESCE(?, last_provider_event_at)"
                       " WHERE case_id = ? AND version = ?",
                       [target, reason, self.now_iso(), provider_event_at, case["case_id"], case["version"]])
        if n != 1:
            raise Conflict("concurrent modification of case", case_id=case["case_id"])
        self._audit(tx, "CASE_STATUS_CHANGED", actor, merchant_id=case["merchant_id"],
                    case_id=case["case_id"], correlation_id=case["correlation_id"],
                    data={"from": case["status"], "to": target, "reason": reason, **(event_data or {})})
        case.update(status=target, status_reason=reason, version=case["version"] + 1)

    # ----------------------------------------------------------------- policy
    def get_policy(self, merchant_id: str, tx=None) -> dict:
        def _get(t):
            row = t.one("SELECT policy, version FROM merchant_policies WHERE merchant_id = ?", [merchant_id])
            pol = dict(DEFAULT_POLICY)
            if row:
                pol.update(loads(row["policy"]))
                pol["version"] = f"{merchant_id}:v{row['version']}"
            else:
                pol["version"] = f"{merchant_id}:default"
            return pol
        if tx is not None:
            return _get(tx)
        with self.db.transaction(write=False) as t:
            return _get(t)

    def update_policy(self, merchant_id: str, patch: dict, actor: str) -> dict:
        allowed_keys = {"daily_contact_budget", "daily_incentive_budget_paise",
                        "per_customer_daily_contact_cap", "allowed_actions",
                        "approval_threshold_paise", "approval_ttl_hours", "default_merchant_category"}
        if not isinstance(patch, dict) or not patch:
            raise ValidationFailed("policy patch must be a non-empty object")
        unknown = set(patch) - allowed_keys
        if unknown:
            raise ValidationFailed(f"unknown policy fields: {sorted(unknown)}")
        clean = {}
        for k, v in patch.items():
            if k in ("daily_contact_budget", "per_customer_daily_contact_cap", "approval_ttl_hours"):
                if isinstance(v, bool) or not isinstance(v, int) or not (0 <= v <= 1_000_000):
                    raise ValidationFailed(f"{k} must be an integer in [0, 1000000]")
            elif k == "daily_incentive_budget_paise":
                try:
                    validate_paise(v, allow_zero=True)
                except MoneyError as e:
                    raise ValidationFailed(f"{k}: {e}") from None
            elif k == "allowed_actions":
                if not isinstance(v, list) or any(a not in ACTION_REGISTRY or a == "NO_ACTION" for a in v):
                    raise ValidationFailed("allowed_actions must list registry actions (NO_ACTION is implicit)")
                v = sorted(set(v))
            elif k == "approval_threshold_paise":
                if not isinstance(v, dict) or any(a not in ACTION_REGISTRY for a in v):
                    raise ValidationFailed("approval_threshold_paise keys must be registry actions")
                for a, amt in v.items():
                    try:
                        validate_paise(amt, allow_zero=True)
                    except MoneyError as e:
                        raise ValidationFailed(f"approval_threshold_paise[{a}]: {e}") from None
            elif k == "default_merchant_category":
                if not isinstance(v, str) or not _CAT_RE.match(v):
                    raise ValidationFailed("default_merchant_category must be a lowercase identifier")
            clean[k] = v
        with self.db.transaction() as tx:
            row = tx.one("SELECT policy, version FROM merchant_policies WHERE merchant_id = ?" + self._for_update(),
                         [merchant_id])
            current = loads(row["policy"]) if row else {}
            merged = {**current, **clean}
            if row:
                tx.execute("UPDATE merchant_policies SET policy = ?, version = version + 1, updated_by = ?,"
                           " updated_at = ? WHERE merchant_id = ?", [merged, actor, self.now_iso(), merchant_id])
            else:
                tx.execute("INSERT INTO merchant_policies (merchant_id, policy, version, updated_by, updated_at)"
                           " VALUES (?,?,1,?,?)", [merchant_id, merged, actor, self.now_iso()])
            self._audit(tx, "POLICY_UPDATED", actor, merchant_id=merchant_id, data={"patch": clean})
            return self.get_policy(merchant_id, tx)

    # ---------------------------------------------------------------- intake
    @staticmethod
    def validate_payment_failed(body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValidationFailed("body must be a JSON object")
        allowed = {"external_payment_id", "customer_ref", "amount_paise", "amount_inr", "currency",
                   "payment_method", "failure_reason", "attempt_count", "merchant_category", "failed_at",
                   "features"}
        unknown = set(body) - allowed
        if unknown:
            raise ValidationFailed(f"unknown fields: {sorted(unknown)}")
        out = {}
        for f in ("external_payment_id", "customer_ref"):
            v = body.get(f)
            if not isinstance(v, str) or not _ID_RE.match(v):
                raise ValidationFailed(f"{f} must match {_ID_RE.pattern}")
            out[f] = v
        if ("amount_paise" in body) == ("amount_inr" in body):
            raise ValidationFailed("provide exactly one of amount_paise (integer) or amount_inr (decimal)")
        try:
            out["amount_paise"] = (validate_paise(body["amount_paise"]) if "amount_paise" in body
                                   else rupees_to_paise(body["amount_inr"]))
            out["currency"] = validate_currency(body.get("currency", "INR"))
        except MoneyError as e:
            raise ValidationFailed(str(e)) from None
        for f in ("payment_method", "failure_reason"):
            v = body.get(f)
            if not isinstance(v, str) or not _CAT_RE.match(v):
                raise ValidationFailed(f"{f} must be a lowercase identifier")
            out[f] = v
        ac = body.get("attempt_count", 1)
        if isinstance(ac, bool) or not isinstance(ac, int) or not (1 <= ac <= 20):
            raise ValidationFailed("attempt_count must be an integer in [1, 20]")
        out["attempt_count"] = ac
        mc = body.get("merchant_category")
        if mc is not None and (not isinstance(mc, str) or not _CAT_RE.match(mc)):
            raise ValidationFailed("merchant_category must be a lowercase identifier")
        out["merchant_category"] = mc
        fa = body.get("failed_at")
        if fa is not None:
            try:
                dt = datetime.fromisoformat(str(fa).replace("Z", "+00:00"))
            except ValueError:
                raise ValidationFailed("failed_at must be ISO-8601") from None
            if dt.tzinfo is None:
                raise ValidationFailed("failed_at must include a timezone offset")
            out["failed_at"] = dt.astimezone(timezone.utc).isoformat(timespec="microseconds")
        else:
            out["failed_at"] = None
        feats = body.get("features") or {}
        if not isinstance(feats, dict) or set(feats) - {"customer_success_rate", "customer_previous_recovery_rate"}:
            raise ValidationFailed("features may only contain customer_success_rate, customer_previous_recovery_rate")
        for k, v in feats.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0.0 <= float(v) <= 1.0):
                raise ValidationFailed(f"features.{k} must be a number in [0, 1]")
        out["features"] = {k: float(v) for k, v in feats.items()}
        return out

    def open_case(self, merchant_id: str, body: dict, idempotency_key: Optional[str],
                  data_origin: str = "SYNTHETIC_DEMO", actor: str = "api"):
        """Returns (http_status, response, replayed: bool)."""
        if not idempotency_key or not _ID_RE.match(idempotency_key):
            raise BadRequest("Idempotency-Key header is required (1-64 chars, [A-Za-z0-9_-:.])")
        clean = self.validate_payment_failed(body)
        if clean["failed_at"] is None:
            clean["failed_at"] = self.now_iso()
        elif datetime.fromisoformat(clean["failed_at"]) > self.now() + timedelta(minutes=5):
            raise ValidationFailed("failed_at is in the future")
        req_hash = _hash_request({k: v for k, v in body.items()})
        for _ in range(3):  # retry once if a concurrent writer won a unique-constraint race
            try:
                with self.db.transaction() as tx:
                    prior = tx.one("SELECT request_hash, status_code, response FROM idempotency_keys"
                                   " WHERE merchant_id = ? AND scope = 'payment_failed' AND idem_key = ?",
                                   [merchant_id, idempotency_key])
                    if prior:
                        if prior["request_hash"] != req_hash:
                            raise IdempotencyConflict(
                                "Idempotency-Key was already used with a different request body")
                        return int(prior["status_code"]), loads(prior["response"]), True
                    existing = tx.one("SELECT case_id, status FROM payment_cases WHERE merchant_id = ?"
                                      " AND external_payment_id = ?", [merchant_id, clean["external_payment_id"]])
                    if existing:
                        status, resp = 200, {"case_id": existing["case_id"], "status": existing["status"],
                                             "duplicate_of_existing_case": True}
                    else:
                        case_id = "case_" + uuid.uuid4().hex[:16]
                        corr = "corr_" + uuid.uuid4().hex[:16]
                        now = self.now_iso()
                        tx.execute(
                            "INSERT INTO payment_cases (case_id, merchant_id, external_payment_id, customer_ref,"
                            " amount_paise, currency, payment_method, failure_reason, attempt_count,"
                            " merchant_category, failed_at, features, status, data_origin, correlation_id,"
                            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,?,?)",
                            [case_id, merchant_id, clean["external_payment_id"], clean["customer_ref"],
                             clean["amount_paise"], clean["currency"], clean["payment_method"],
                             clean["failure_reason"], clean["attempt_count"], clean["merchant_category"],
                             clean["failed_at"], clean["features"], data_origin, corr, now, now])
                        self._audit(tx, "PAYMENT_FAILED_RECEIVED", actor, merchant_id=merchant_id,
                                    case_id=case_id, correlation_id=corr,
                                    data={"external_payment_id": clean["external_payment_id"],
                                          "amount_paise": clean["amount_paise"], "currency": clean["currency"],
                                          "failure_reason": clean["failure_reason"],
                                          "payment_method": clean["payment_method"], "data_origin": data_origin})
                        status, resp = 201, {"case_id": case_id, "status": "OPEN", "correlation_id": corr,
                                             "duplicate_of_existing_case": False}
                    tx.execute("INSERT INTO idempotency_keys (merchant_id, scope, idem_key, request_hash,"
                               " status_code, response, created_at) VALUES (?, 'payment_failed', ?, ?, ?, ?, ?)",
                               [merchant_id, idempotency_key, req_hash, status, resp, self.now_iso()])
                    return status, resp, False
            except UniqueViolation:
                continue
        raise Conflict("could not open case due to concurrent conflicting writes; retry")

    # -------------------------------------------------------------- guardrails
    def guardrail_state(self, merchant_id: str, customer_ref: str, tx=None) -> dict:
        def _q(t):
            opted = t.one("SELECT opted_out FROM customer_contact_state WHERE merchant_id = ? AND customer_ref = ?",
                          [merchant_id, customer_ref])
            since = (self.now() - timedelta(hours=24)).isoformat(timespec="microseconds")
            rows = t.all("SELECT a.action, a.created_at, a.requires_contact FROM action_attempts a"
                         " JOIN payment_cases c ON c.case_id = a.case_id"
                         " WHERE c.merchant_id = ? AND c.customer_ref = ? AND a.created_at >= ?"
                         " AND a.status IN ('QUEUED','DISPATCHING','PROVIDER_ACCEPTED','UNKNOWN')",
                         [merchant_id, customer_ref, since])
            last = {}
            for r in rows:
                if r["action"] not in last or r["created_at"] > last[r["action"]]:
                    last[r["action"]] = r["created_at"]
            day = self.now().date().isoformat()
            usage = t.one("SELECT contacts_used, incentive_committed_paise FROM budget_usage"
                          " WHERE merchant_id = ? AND day = ?", [merchant_id, day]) or \
                {"contacts_used": 0, "incentive_committed_paise": 0}
            return {"opted_out": bool(opted and opted["opted_out"]), "last_action_at": last,
                    "customer_contacts_24h": sum(1 for r in rows if r["requires_contact"]),
                    "contacts_used_today": int(usage["contacts_used"]),
                    "incentive_committed_today_paise": int(usage["incentive_committed_paise"]),
                    "now": self.now_iso()}
        if tx is not None:
            return _q(tx)
        with self.db.transaction(write=False) as t:
            return _q(t)

    def set_opt_out(self, merchant_id: str, customer_ref: str, opted_out: bool, actor: str) -> dict:
        if not _ID_RE.match(customer_ref or ""):
            raise ValidationFailed("invalid customer_ref")
        with self.db.transaction() as tx:
            tx.execute("INSERT INTO customer_contact_state (merchant_id, customer_ref, opted_out, opted_out_at)"
                       " VALUES (?,?,?,?) ON CONFLICT (merchant_id, customer_ref) DO UPDATE SET"
                       " opted_out = excluded.opted_out, opted_out_at = excluded.opted_out_at",
                       [merchant_id, customer_ref, bool(opted_out), self.now_iso() if opted_out else None])
            self._audit(tx, "CUSTOMER_OPT_OUT_SET", actor, merchant_id=merchant_id,
                        data={"customer_ref": customer_ref, "opted_out": bool(opted_out)})
        return {"customer_ref": customer_ref, "opted_out": bool(opted_out)}

    # --------------------------------------------------------------- budgets
    def _reserve_budget(self, tx, merchant_id, policy, contacts: int, incentive_paise: int) -> bool:
        day = self.now().date().isoformat()
        tx.execute("INSERT INTO budget_usage (merchant_id, day) VALUES (?, ?) ON CONFLICT DO NOTHING",
                   [merchant_id, day])
        n = tx.execute("UPDATE budget_usage SET contacts_used = contacts_used + ?,"
                       " incentive_committed_paise = incentive_committed_paise + ?"
                       " WHERE merchant_id = ? AND day = ? AND contacts_used + ? <= ?"
                       " AND incentive_committed_paise + ? <= ?",
                       [contacts, incentive_paise, merchant_id, day, contacts,
                        int(policy["daily_contact_budget"]), incentive_paise,
                        int(policy["daily_incentive_budget_paise"])])
        return n == 1

    def _release_budget(self, tx, attempt: dict):
        day = attempt["created_at"][:10]
        c = 1 if attempt["requires_contact"] else 0
        tx.execute("UPDATE budget_usage SET contacts_used = contacts_used - ?,"
                   " incentive_committed_paise = incentive_committed_paise - ?"
                   " WHERE merchant_id = ? AND day = ? AND contacts_used >= ? AND incentive_committed_paise >= ?",
                   [c, attempt["discount_paise"], attempt["merchant_id"], day, c, attempt["discount_paise"]])

    # -------------------------------------------------------------- decision
    def record_decision(self, case_id: str, decision: dict, actor: str = "decision_engine") -> dict:
        """Persist the decision and move the case to NO_ACTION / PENDING_APPROVAL /
        ACTION_QUEUED. Budget is reserved atomically; if reservation fails the
        selection is downgraded to NO_ACTION with reason BUDGET_EXHAUSTED_AT_COMMIT."""
        with self.db.transaction() as tx:
            case = self._lock_case(tx, case_id)
            if case["status"] != "OPEN":
                existing = tx.one("SELECT decision_id, selected_action FROM case_decisions WHERE case_id = ?",
                                  [case_id])
                if existing:
                    return {"case_id": case_id, "status": case["status"], "replayed": True, **existing}
                raise Conflict(f"case is {case['status']}, expected OPEN")
            policy = self.get_policy(case["merchant_id"], tx)
            action = decision["selected_action"]
            reason = decision.get("reason_code")
            if action != "NO_ACTION" and action not in ACTION_REGISTRY:
                raise ValidationFailed(f"unknown action {action}")
            if action != "NO_ACTION" and not decision.get("requires_approval"):
                ok = self._reserve_budget(tx, case["merchant_id"], policy,
                                          1 if decision.get("requires_contact") else 0,
                                          int(decision.get("discount_paise", 0)))
                if not ok:
                    action, reason = "NO_ACTION", "BUDGET_EXHAUSTED_AT_COMMIT"
            decision_id = "dec_" + uuid.uuid4().hex[:16]
            tx.execute("INSERT INTO case_decisions (decision_id, case_id, merchant_id, selected_action, reason_code,"
                       " candidates, expected_value_paise, p_control, p_selected, requires_approval,"
                       " approval_reason, model_version, policy_version, feature_imputations,"
                       " advisory_analysis, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       [decision_id, case_id, case["merchant_id"], action, reason, decision.get("candidates", []),
                        int(round(decision.get("expected_value_paise", 0))) if action != "NO_ACTION" else 0,
                        decision.get("p_control"), decision.get("p_selected") if action != "NO_ACTION" else None,
                        bool(decision.get("requires_approval")) and action != "NO_ACTION",
                        decision.get("approval_reason"), decision["model_version"], policy["version"],
                        decision.get("feature_imputations", []), decision.get("advisory_analysis"),
                        self.now_iso()])
            self._audit(tx, "DECISION_RECORDED", actor, merchant_id=case["merchant_id"], case_id=case_id,
                        correlation_id=case["correlation_id"],
                        data={"decision_id": decision_id, "selected_action": action, "reason_code": reason,
                              "expected_value_paise": decision.get("expected_value_paise"),
                              "model_version": decision["model_version"], "policy_version": policy["version"]})
            if action == "NO_ACTION":
                self._set_case_status(tx, case, "NO_ACTION", reason, actor)
            elif decision.get("requires_approval"):
                approval_id = "apr_" + uuid.uuid4().hex[:16]
                expires = (self.now() + timedelta(hours=int(policy["approval_ttl_hours"]))).isoformat(
                    timespec="microseconds")
                tx.execute("INSERT INTO approval_requests (approval_id, case_id, decision_id, merchant_id, action,"
                           " amount_paise, threshold_paise, reason, status, expires_at, created_at)"
                           " VALUES (?,?,?,?,?,?,?,?,'PENDING',?,?)",
                           [approval_id, case_id, decision_id, case["merchant_id"], action, case["amount_paise"],
                            int(decision.get("threshold_paise", 0)), decision.get("approval_reason") or "",
                            expires, self.now_iso()])
                self._audit(tx, "APPROVAL_REQUESTED", actor, merchant_id=case["merchant_id"], case_id=case_id,
                            correlation_id=case["correlation_id"],
                            data={"approval_id": approval_id, "action": action,
                                  "threshold_paise": decision.get("threshold_paise"),
                                  "reason": decision.get("approval_reason")})
                self._set_case_status(tx, case, "PENDING_APPROVAL", "APPROVAL_REQUIRED", actor)
            else:
                self._queue_attempt(tx, case, action, decision, actor)
            return {"case_id": case_id, "decision_id": decision_id, "selected_action": action,
                    "status": case["status"], "replayed": False}

    def _queue_attempt(self, tx, case, action, decision, actor) -> str:
        attempt_id = "att_" + uuid.uuid4().hex[:16]
        discount = int(decision.get("discount_paise", 0))
        collect = case["amount_paise"] - discount
        if collect <= 0:
            raise ValidationFailed("incentive would reduce collectable amount to zero")
        now = self.now_iso()
        tx.execute("INSERT INTO action_attempts (attempt_id, case_id, merchant_id, action, idempotency_key,"
                   " collect_amount_paise, discount_paise, action_cost_paise, offer_id, requires_contact, status,"
                   " provider, provider_mode, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,'QUEUED',?,?,?,?)",
                   [attempt_id, case["case_id"], case["merchant_id"], action,
                    idempotency_key_for(case["case_id"], action), collect, discount,
                    int(decision.get("action_cost_paise", 0)), decision.get("offer_id"),
                    bool(decision.get("requires_contact")), self.provider.name, self.provider.mode, now, now])
        self._audit(tx, "ACTION_QUEUED", actor, merchant_id=case["merchant_id"], case_id=case["case_id"],
                    correlation_id=case["correlation_id"],
                    data={"attempt_id": attempt_id, "action": action, "collect_amount_paise": collect,
                          "discount_paise": discount, "idempotency_key": idempotency_key_for(case["case_id"], action)})
        self._set_case_status(tx, case, "ACTION_QUEUED", None, actor)
        return attempt_id

    # -------------------------------------------------------------- approvals
    def decide_approval(self, merchant_id: str, approval_id: str, decision: str, actor: str,
                        note: str = "") -> dict:
        if decision not in ("APPROVE", "REJECT"):
            raise ValidationFailed("decision must be APPROVE or REJECT")
        if not isinstance(note, str) or len(note) > 500:
            raise ValidationFailed("note must be a string of at most 500 characters")
        if decision == "REJECT" and not note.strip():
            raise ValidationFailed("a reason is required to reject")
        target = "APPROVED" if decision == "APPROVE" else "REJECTED"
        with self.db.transaction() as tx:
            apr = tx.one("SELECT * FROM approval_requests WHERE approval_id = ? AND merchant_id = ?"
                         + self._for_update(), [approval_id, merchant_id])
            if apr is None:
                raise NotFound("approval not found")  # also for other merchants' approvals (no IDOR leak)
            if apr["status"] == target:
                return {**self._approval_view(tx, approval_id), "replayed": True}
            if apr["status"] != "PENDING":
                raise Conflict(f"approval is already {apr['status']}", current=apr["status"])
            case = self._lock_case(tx, apr["case_id"])
            if self.now_iso() > apr["expires_at"]:
                tx.execute("UPDATE approval_requests SET status = 'EXPIRED' WHERE approval_id = ? AND status = 'PENDING'",
                           [approval_id])
                self._audit(tx, "APPROVAL_EXPIRED", "system", merchant_id=merchant_id, case_id=case["case_id"],
                            correlation_id=case["correlation_id"], data={"approval_id": approval_id})
                self._set_case_status(tx, case, "ACTION_REJECTED", "APPROVAL_EXPIRED", "system")
                # Commit the expiry, then report the conflict to the caller.
                return {**self._approval_view(tx, approval_id), "replayed": False, "expired": True}
            n = tx.execute("UPDATE approval_requests SET status = ?, decided_by = ?, decision_note = ?, decided_at = ?"
                           " WHERE approval_id = ? AND status = 'PENDING'",
                           [target, actor, note, self.now_iso(), approval_id])
            if n != 1:
                raise Conflict("approval was decided concurrently")
            self._audit(tx, "APPROVAL_" + target, actor, merchant_id=merchant_id, case_id=case["case_id"],
                        correlation_id=case["correlation_id"],
                        data={"approval_id": approval_id, "action": apr["action"], "note": note})
            if target == "REJECTED":
                self._set_case_status(tx, case, "ACTION_REJECTED", "HUMAN_REJECTED", actor)
            else:
                dec = tx.one("SELECT * FROM case_decisions WHERE decision_id = ?", [apr["decision_id"]])
                cands = {c["action"]: c for c in loads(dec["candidates"])}
                sel = cands.get(apr["action"], {})
                policy = self.get_policy(merchant_id, tx)
                d = {"discount_paise": sel.get("incentive_discount_paise", 0),
                     "action_cost_paise": sel.get("action_cost_paise", 0), "offer_id": sel.get("offer_id"),
                     "requires_contact": sel.get("requires_customer_contact", False)}
                if not self._reserve_budget(tx, merchant_id, policy, 1 if d["requires_contact"] else 0,
                                            int(d["discount_paise"])):
                    raise Conflict("daily budget exhausted; approval not applied", reason="BUDGET_EXHAUSTED")
                self._queue_attempt(tx, case, apr["action"], d, actor)
            return {**self._approval_view(tx, approval_id), "replayed": False}

    def _approval_view(self, tx, approval_id):
        a = tx.one("SELECT a.*, c.status AS case_status FROM approval_requests a JOIN payment_cases c"
                   " ON c.case_id = a.case_id WHERE a.approval_id = ?", [approval_id])
        att = tx.one("SELECT attempt_id, status FROM action_attempts WHERE case_id = ?", [a["case_id"]])
        a["attempt"] = att
        return a

    # --------------------------------------------------------------- dispatch
    def dispatch(self, attempt_id: str, actor: str = "dispatcher") -> dict:
        """Claim (QUEUED -> DISPATCHING), call provider outside the transaction,
        record the result. Safe to call repeatedly / concurrently."""
        with self.db.transaction() as tx:
            att = tx.one("SELECT * FROM action_attempts WHERE attempt_id = ?" + self._for_update(), [attempt_id])
            if att is None:
                raise NotFound("attempt not found")
            n = tx.execute("UPDATE action_attempts SET status = 'DISPATCHING', dispatch_count = dispatch_count + 1,"
                           " updated_at = ? WHERE attempt_id = ? AND status = 'QUEUED'", [self.now_iso(), attempt_id])
            if n != 1:
                return {**att, "claimed": False}
            case = tx.one("SELECT * FROM payment_cases WHERE case_id = ?", [att["case_id"]])
            self._audit(tx, "ACTION_DISPATCH_STARTED", actor, merchant_id=att["merchant_id"], case_id=att["case_id"],
                        correlation_id=case["correlation_id"],
                        data={"attempt_id": attempt_id, "idempotency_key": att["idempotency_key"],
                              "dispatch_count": att["dispatch_count"] + 1})
        notes = {"merchant_id": att["merchant_id"], "case_id": att["case_id"], "attempt_id": attempt_id,
                 "correlation_id": case["correlation_id"]}
        try:
            if att["action"] in RETRY_ACTIONS:
                res = self.provider.retry_payment(att["idempotency_key"], att["collect_amount_paise"], "INR", notes)
            else:
                res = self.provider.create_payment_link(att["idempotency_key"], att["collect_amount_paise"], "INR",
                                                        notes)
        except Exception as e:  # unknown failure mode: the provider may or may not have acted
            from .providers import ProviderResult
            res = ProviderResult("TIMEOUT", detail=f"unexpected {type(e).__name__}")
        return self._record_dispatch_result(attempt_id, res, actor)

    def _record_dispatch_result(self, attempt_id, res, actor) -> dict:
        status_map = {"ACCEPTED": "PROVIDER_ACCEPTED", "ERROR": "FAILED", "TIMEOUT": "UNKNOWN",
                      "UNSUPPORTED": "UNSUPPORTED"}
        new_status = status_map[res.status]
        with self.db.transaction() as tx:
            att = tx.one("SELECT * FROM action_attempts WHERE attempt_id = ?" + self._for_update(), [attempt_id])
            case = self._lock_case(tx, att["case_id"])
            if att["status"] not in ("DISPATCHING", "UNKNOWN"):
                return {**att, "claimed": True, "note": "result already recorded"}
            if res.provider_reference and att["provider_reference"] not in (None, res.provider_reference):
                new_status, res.detail = "UNKNOWN", "provider reference conflict"
            tx.execute("UPDATE action_attempts SET status = ?, provider_reference = COALESCE(?, provider_reference),"
                       " provider_status = ?, last_error = ?, updated_at = ? WHERE attempt_id = ?",
                       [new_status, res.provider_reference, res.provider_status, res.detail, self.now_iso(), attempt_id])
            self._audit(tx, "ACTION_DISPATCH_RESULT", actor, merchant_id=att["merchant_id"], case_id=att["case_id"],
                        correlation_id=case["correlation_id"],
                        data={"attempt_id": attempt_id, "provider_result": res.status,
                              "provider_reference": res.provider_reference, "provider_dedup_hit": res.duplicate,
                              "detail": res.detail, "provider_mode": self.provider.mode})
            if case["status"] in ("ACTION_QUEUED", "OUTCOME_UNKNOWN"):
                target = {"PROVIDER_ACCEPTED": "PROVIDER_PENDING", "UNKNOWN": "OUTCOME_UNKNOWN",
                          "FAILED": "ACTION_FAILED", "UNSUPPORTED": "ACTION_FAILED"}[new_status]
                if target != case["status"]:
                    self._set_case_status(tx, case, target,
                                          {"FAILED": "PROVIDER_ERROR", "UNSUPPORTED": "UNSUPPORTED_BY_PROVIDER",
                                           "UNKNOWN": "PROVIDER_TIMEOUT"}.get(new_status), actor)
            if new_status in ("FAILED", "UNSUPPORTED"):
                self._release_budget(tx, att)  # no customer contact happened
            return {**tx.one("SELECT * FROM action_attempts WHERE attempt_id = ?", [attempt_id]), "claimed": True}

    def resolve_attempt(self, attempt_id: str, actor: str = "resolver", stale_after_s: int = 60) -> dict:
        """Resolve UNKNOWN (timeout) or stale DISPATCHING attempts by asking the
        provider whether an object exists for the idempotency key. Found ->
        record acceptance. Not found -> safe to re-queue and dispatch again with
        the SAME key (the provider dedupes if our lookup raced its commit)."""
        with self.db.transaction(write=False) as tx:
            att = tx.one("SELECT * FROM action_attempts WHERE attempt_id = ?", [attempt_id])
        if att is None:
            raise NotFound("attempt not found")
        if att["status"] == "DISPATCHING":
            age = (self.now() - datetime.fromisoformat(att["updated_at"])).total_seconds()
            if age < stale_after_s:
                raise Conflict("attempt is being dispatched; retry later")
        elif att["status"] != "UNKNOWN":
            return att
        found = self.provider.lookup(att["idempotency_key"])
        if found is not None:
            with self.db.transaction() as tx:
                tx.execute("UPDATE action_attempts SET status = 'UNKNOWN' WHERE attempt_id = ? AND status = 'DISPATCHING'",
                           [attempt_id])
            return self._record_dispatch_result(attempt_id, found, actor)
        with self.db.transaction() as tx:
            n = tx.execute("UPDATE action_attempts SET status = 'QUEUED', updated_at = ? WHERE attempt_id = ?"
                           " AND status IN ('UNKNOWN','DISPATCHING')", [self.now_iso(), attempt_id])
            case = tx.one("SELECT correlation_id FROM payment_cases WHERE case_id = ?", [att["case_id"]])
            if n == 1:
                self._audit(tx, "ACTION_REQUEUED_AFTER_LOOKUP", actor, merchant_id=att["merchant_id"],
                            case_id=att["case_id"], correlation_id=case["correlation_id"],
                            data={"attempt_id": attempt_id, "lookup": "not_found"})
        return self.dispatch(attempt_id, actor)

    # ---------------------------------------------------------------- webhooks
    def ingest_webhook(self, raw_body: bytes, headers: dict):
        """Returns (http_status, body). Never raises for provider-originated
        conditions that retries cannot fix (they are recorded, then 200)."""
        h = {k.lower(): v for k, v in headers.items()}
        provider = self.provider.name
        if len(raw_body) > self.webhook_max_bytes:
            self._reject(provider, "PAYLOAD_TOO_LARGE", raw_body)
            raise PayloadTooLarge("webhook body too large")
        sig = h.get("x-razorpay-signature")
        if not sig:
            self._reject(provider, "MISSING_SIGNATURE", raw_body)
            raise Unauthorized("missing webhook signature")
        if not self.provider.verify_webhook_signature(raw_body, sig):
            self._reject(provider, "INVALID_SIGNATURE", raw_body)
            raise Unauthorized("invalid webhook signature")
        # --- authenticated from here on ---
        try:
            body = json.loads(raw_body)
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            self._reject(provider, "MALFORMED_JSON", raw_body)
            raise BadRequest("malformed JSON payload") from None
        event_id = h.get("x-razorpay-event-id")
        if not event_id or not isinstance(event_id, str) or len(event_id) > 100:
            self._reject(provider, "MISSING_EVENT_ID", raw_body)
            raise BadRequest("missing x-razorpay-event-id header")
        ev = self._extract(body)
        sha = hashlib.sha256(raw_body).hexdigest()
        with self.db.transaction() as tx:
            row_id = "pev_" + uuid.uuid4().hex[:16]
            ins = tx.execute("INSERT INTO provider_events (event_row_id, provider, provider_event_id, event_type,"
                             " provider_created_at, received_at, payload_sha256, summary, match_status, outcome)"
                             " VALUES (?,?,?,?,?,?,?,?,'UNMATCHED','PROCESSING') ON CONFLICT DO NOTHING",
                             [row_id, provider, event_id, ev["event"], ev["created_at"], self.now_iso(), sha,
                              ev["summary"]])
            if ins != 1:
                prior = tx.one("SELECT outcome, case_id, payload_sha256 FROM provider_events WHERE provider = ?"
                               " AND provider_event_id = ?", [provider, event_id])
                return 200, {"status": "duplicate_ignored", "event_id": event_id, "prior_outcome": prior["outcome"],
                             "same_payload": prior["payload_sha256"] == sha}
            match, outcome, reason, case_id, attempt_id, merchant_id = self._reconcile(tx, ev, event_id)
            tx.execute("UPDATE provider_events SET match_status = ?, outcome = ?, outcome_reason = ?, case_id = ?,"
                       " attempt_id = ?, merchant_id = ? WHERE event_row_id = ?",
                       [match, outcome, reason, case_id, attempt_id, merchant_id, row_id])
            return 200, {"status": "processed", "event_id": event_id, "event_type": ev["event"],
                         "match_status": match, "outcome": outcome, "reason": reason, "case_id": case_id}

    def _reject(self, provider, reason, raw_body):
        with self.db.transaction() as tx:
            tx.execute("INSERT INTO webhook_rejections (rejection_id, provider, reason, body_sha256, body_size,"
                       " received_at) VALUES (?,?,?,?,?,?)",
                       ["rej_" + uuid.uuid4().hex[:16], provider, reason, hashlib.sha256(raw_body).hexdigest(),
                        len(raw_body), self.now_iso()])

    @staticmethod
    def _extract(body: dict) -> dict:
        def s(v):
            return v if isinstance(v, str) and 0 < len(v) <= 100 else None
        event = s(body.get("event")) or "unknown"
        payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        pl = ((payload.get("payment_link") or {}).get("entity") or {}) if isinstance(payload.get("payment_link"), dict) else {}
        pay = ((payload.get("payment") or {}).get("entity") or {}) if isinstance(payload.get("payment"), dict) else {}
        if not isinstance(pl, dict):
            pl = {}
        if not isinstance(pay, dict):
            pay = {}
        pay_notes = pay.get("notes") if isinstance(pay.get("notes"), dict) else {}
        pl_notes = pl.get("notes") if isinstance(pl.get("notes"), dict) else {}
        notes = pl_notes or pay_notes
        if event.startswith("payment_link."):
            link_ref, reference_id = s(pl.get("id")), s(pl.get("reference_id"))
            amount = pl.get("amount_paid") if event.endswith("paid") else None
            if event == "payment_link.paid" and isinstance(pay.get("amount"), int) and amount is None:
                amount = pay.get("amount")
            currency = s(pl.get("currency"))
        else:
            link_ref, reference_id = s(pay_notes.get("crro_provider_reference")), s(pay_notes.get("crro_reference_id"))
            amount, currency = pay.get("amount"), s(pay.get("currency"))
        created = body.get("created_at")
        created_iso = (datetime.fromtimestamp(created, tz=timezone.utc).isoformat(timespec="microseconds")
                       if isinstance(created, int) and not isinstance(created, bool) and created > 0 else None)
        amount = amount if isinstance(amount, int) and not isinstance(amount, bool) else None
        return {"event": event, "link_ref": link_ref, "reference_id": reference_id,
                "payment_id": s(pay.get("id")), "amount": amount, "currency": currency,
                "notes_merchant_id": s(notes.get("merchant_id")), "created_at": created_iso,
                "summary": {"event": event, "provider_reference": link_ref, "reference_id": reference_id,
                            "payment_id": s(pay.get("id")), "amount_paise": amount, "currency": currency,
                            "provider_created_at": created_iso}}

    def _reconcile(self, tx, ev: dict, event_id: str):
        """Apply one verified, first-seen provider event. Returns
        (match_status, outcome, reason, case_id, attempt_id, merchant_id)."""
        supported = {"payment_link.paid", "payment_link.expired", "payment_link.cancelled",
                     "payment_link.partially_paid", "payment.captured", "payment.failed"}
        if ev["event"] not in supported:
            return "UNSUPPORTED", "IGNORED", f"unsupported event type {ev['event']}", None, None, None
        att = None
        if ev["link_ref"]:
            att = tx.one("SELECT * FROM action_attempts WHERE provider_reference = ?" + self._for_update(),
                         [ev["link_ref"]])
        if att is None and ev["reference_id"]:
            att = tx.one("SELECT * FROM action_attempts WHERE idempotency_key = ?" + self._for_update(),
                         [ev["reference_id"]])
        if att is None:
            return "UNMATCHED", "NEEDS_REVIEW", "UNKNOWN_PROVIDER_REFERENCE", None, None, None
        case = self._lock_case(tx, att["case_id"])
        m = case["merchant_id"]
        ctx = {"provider_event_id": event_id, "event_type": ev["event"], "provider_reference": ev["link_ref"]}

        def review(reason):
            if case["status"] != "NEEDS_REVIEW" and can_transition(case["status"], "NEEDS_REVIEW"):
                self._set_case_status(tx, case, "NEEDS_REVIEW", reason, "reconciler", ctx, ev["created_at"])
            else:
                tx.execute("UPDATE payment_cases SET review_reason = ?, updated_at = ? WHERE case_id = ?",
                           [reason, self.now_iso(), case["case_id"]])
                self._audit(tx, "REVIEW_FLAG_SET", "reconciler", merchant_id=m, case_id=case["case_id"],
                            correlation_id=case["correlation_id"], data={"reason": reason, **ctx})
            return "MATCHED", "NEEDS_REVIEW", reason, case["case_id"], att["attempt_id"], m

        if ev["link_ref"] and att["provider_reference"] and att["provider_reference"] != ev["link_ref"]:
            return review("PROVIDER_REFERENCE_CONFLICT")
        if ev["notes_merchant_id"] and ev["notes_merchant_id"] != m:
            return review("MERCHANT_MISMATCH")
        if att["provider_reference"] is None and ev["link_ref"]:
            # Verified event proves the provider object exists (resolves UNKNOWN).
            tx.execute("UPDATE action_attempts SET provider_reference = ?, status = 'PROVIDER_ACCEPTED', updated_at = ?"
                       " WHERE attempt_id = ?", [ev["link_ref"], self.now_iso(), att["attempt_id"]])
        stale = (ev["created_at"] is not None and case["last_provider_event_at"] is not None
                 and ev["created_at"] < case["last_provider_event_at"])
        et = ev["event"]

        if et in ("payment_link.paid", "payment.captured"):
            if case["status"] == "RECOVERED":
                rec = tx.one("SELECT * FROM recoveries WHERE case_id = ?", [case["case_id"]])
                if ev["payment_id"] is None or rec["provider_payment_id"] == ev["payment_id"]:
                    return "MATCHED", "NO_CHANGE", "ALREADY_RECOVERED_SAME_PAYMENT", case["case_id"], att["attempt_id"], m
                return review("POSSIBLE_DUPLICATE_PAYMENT")
            if ev["currency"] is None or ev["currency"] != case["currency"]:
                return review("CURRENCY_MISMATCH")
            if ev["amount"] is None:
                return review("MISSING_AMOUNT")
            if ev["amount"] != att["collect_amount_paise"]:
                self._audit(tx, "AMOUNT_MISMATCH", "reconciler", merchant_id=m, case_id=case["case_id"],
                            correlation_id=case["correlation_id"],
                            data={"expected_paise": att["collect_amount_paise"], "observed_paise": ev["amount"], **ctx})
                return review("AMOUNT_MISMATCH")
            if ev["payment_id"] and tx.one("SELECT recovery_id FROM recoveries WHERE provider_payment_id = ?",
                                           [ev["payment_id"]]):
                return review("PAYMENT_ALREADY_CREDITED_TO_ANOTHER_CASE")
            if not can_transition(case["status"], "RECOVERED"):
                return review(f"PAYMENT_IN_UNEXPECTED_STATE_{case['status']}")
            late = case["status"] == "EXPIRED"
            tx.execute("INSERT INTO recoveries (recovery_id, case_id, attempt_id, merchant_id, provider_reference,"
                       " provider_payment_id, provider_event_id, currency, gross_recovered_paise, cash_collected_paise,"
                       " incentive_cost_paise, action_cost_paise, provider_mode, confirmed_at)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       ["rec_" + uuid.uuid4().hex[:16], case["case_id"], att["attempt_id"], m,
                        ev["link_ref"] or att["provider_reference"] or "", ev["payment_id"], event_id, case["currency"],
                        case["amount_paise"], ev["amount"], case["amount_paise"] - ev["amount"],
                        att["action_cost_paise"], att["provider_mode"], self.now_iso()])
            tx.execute("UPDATE action_attempts SET provider_status = 'paid', updated_at = ? WHERE attempt_id = ?",
                       [self.now_iso(), att["attempt_id"]])
            self._set_case_status(tx, case, "RECOVERED", "LATE_PAYMENT_AFTER_EXPIRY" if late else "PROVIDER_CONFIRMED",
                                  "reconciler", {**ctx, "cash_collected_paise": ev["amount"],
                                                 "provider_mode": att["provider_mode"]}, ev["created_at"])
            return "MATCHED", "RECOVERED", "LATE_PAYMENT_AFTER_EXPIRY" if late else None, case["case_id"], att["attempt_id"], m

        if et == "payment_link.partially_paid":
            return review("PARTIAL_PAYMENT")

        if case["status"] == "RECOVERED":
            return "MATCHED", "STALE_IGNORED", "CASE_ALREADY_RECOVERED", case["case_id"], att["attempt_id"], m
        if stale:
            return "MATCHED", "STALE_IGNORED", "OLDER_THAN_LAST_APPLIED_EVENT", case["case_id"], att["attempt_id"], m

        if et in ("payment_link.expired", "payment_link.cancelled"):
            target, reason = "EXPIRED", et.split(".")[1].upper()
        elif et == "payment.failed" and att["action"] in RETRY_ACTIONS:
            target, reason = "STILL_FAILED", "RETRY_DECLINED"
        else:  # payment.failed against an open link: customer may try again
            self._audit(tx, "CUSTOMER_PAYMENT_ATTEMPT_FAILED", "reconciler", merchant_id=m, case_id=case["case_id"],
                        correlation_id=case["correlation_id"], data=ctx)
            return "MATCHED", "NO_CHANGE", "LINK_STILL_OPEN", case["case_id"], att["attempt_id"], m
        if case["status"] == target:
            return "MATCHED", "NO_CHANGE", "ALREADY_IN_STATE", case["case_id"], att["attempt_id"], m
        if not can_transition(case["status"], target):
            return "MATCHED", "NO_CHANGE", f"TRANSITION_NOT_ALLOWED_FROM_{case['status']}", case["case_id"], att["attempt_id"], m
        tx.execute("UPDATE action_attempts SET provider_status = ?, updated_at = ? WHERE attempt_id = ?",
                   [reason.lower(), self.now_iso(), att["attempt_id"]])
        self._set_case_status(tx, case, target, reason, "reconciler", ctx, ev["created_at"])
        return "MATCHED", target, reason, case["case_id"], att["attempt_id"], m

    # ----------------------------------------------------------------- queries
    def get_case(self, merchant_id: str, case_id: str) -> dict:
        with self.db.transaction(write=False) as tx:
            case = tx.one("SELECT * FROM payment_cases WHERE case_id = ? AND merchant_id = ?", [case_id, merchant_id])
            if case is None:
                raise NotFound("case not found")
            case["features"] = loads(case["features"])
            dec = tx.one("SELECT * FROM case_decisions WHERE case_id = ?", [case_id])
            if dec:
                for k in ("candidates", "feature_imputations", "advisory_analysis"):
                    dec[k] = loads(dec[k])
                dec["requires_approval"] = bool(dec["requires_approval"])
            apr = tx.one("SELECT * FROM approval_requests WHERE case_id = ?", [case_id])
            att = tx.one("SELECT * FROM action_attempts WHERE case_id = ?", [case_id])
            if att:
                att["requires_contact"] = bool(att["requires_contact"])
            rec = tx.one("SELECT * FROM recoveries WHERE case_id = ?", [case_id])
            evs = tx.all("SELECT provider_event_id, event_type, provider_created_at, received_at, match_status,"
                         " outcome, outcome_reason, summary FROM provider_events WHERE case_id = ? ORDER BY received_at",
                         [case_id])
            for e in evs:
                e["summary"] = loads(e["summary"])
            timeline = tx.all("SELECT seq, audit_id, ts, event_type, actor, correlation_id, data FROM audit_log"
                              " WHERE case_id = ? ORDER BY seq", [case_id])
            for t in timeline:
                t["data"] = loads(t["data"])
        return {"case": case, "decision": dec, "approval": apr, "attempt": att, "recovery": rec,
                "provider_events": evs, "timeline": timeline}

    def list_cases(self, merchant_id, status=None, q=None, limit=25, cursor=None) -> dict:
        limit = max(1, min(int(limit), 200))
        sql = ("SELECT c.case_id, c.external_payment_id, c.customer_ref, c.amount_paise, c.currency, c.payment_method,"
               " c.failure_reason, c.status, c.status_reason, c.review_reason, c.data_origin, c.created_at,"
               " d.selected_action, d.expected_value_paise, a.provider_reference, a.provider_mode"
               " FROM payment_cases c LEFT JOIN case_decisions d ON d.case_id = c.case_id"
               " LEFT JOIN action_attempts a ON a.case_id = c.case_id WHERE c.merchant_id = ?")
        params = [merchant_id]
        if status:
            statuses = [s for s in status.split(",") if s]
            sql += " AND c.status IN (" + ",".join("?" * len(statuses)) + ")"
            params += statuses
        if q:
            sql += " AND (c.case_id = ? OR c.external_payment_id = ? OR c.customer_ref = ? OR a.provider_reference = ?)"
            params += [q, q, q, q]
        cur = _decode_cursor(cursor, 2)
        if cur:
            sql += " AND (c.created_at < ? OR (c.created_at = ? AND c.case_id < ?))"
            params += [cur[0], cur[0], cur[1]]
        sql += " ORDER BY c.created_at DESC, c.case_id DESC LIMIT ?"
        params.append(limit + 1)
        with self.db.transaction(write=False) as tx:
            rows = tx.all(sql, params)
        nxt = _encode_cursor(rows[limit - 1]["created_at"], rows[limit - 1]["case_id"]) if len(rows) > limit else None
        return {"items": rows[:limit], "next_cursor": nxt}

    def list_approvals(self, merchant_id, status="PENDING", limit=50, cursor=None) -> dict:
        limit = max(1, min(int(limit), 200))
        sql = ("SELECT a.*, c.failure_reason, c.payment_method, c.customer_ref, c.status AS case_status,"
               " d.expected_value_paise, d.p_selected, d.p_control"
               " FROM approval_requests a JOIN payment_cases c ON c.case_id = a.case_id"
               " JOIN case_decisions d ON d.decision_id = a.decision_id WHERE a.merchant_id = ?")
        params = [merchant_id]
        if status and status != "ALL":
            sql += " AND a.status = ?"
            params.append(status)
        cur = _decode_cursor(cursor, 2)
        if cur:
            sql += " AND (a.created_at < ? OR (a.created_at = ? AND a.approval_id < ?))"
            params += [cur[0], cur[0], cur[1]]
        sql += " ORDER BY a.created_at DESC, a.approval_id DESC LIMIT ?"
        params.append(limit + 1)
        with self.db.transaction(write=False) as tx:
            rows = tx.all(sql, params)
        nxt = _encode_cursor(rows[limit - 1]["created_at"], rows[limit - 1]["approval_id"]) if len(rows) > limit else None
        return {"items": rows[:limit], "next_cursor": nxt}

    def list_audit(self, merchant_id, case_id=None, event_type=None, correlation_id=None,
                   date_from=None, date_to=None, limit=100, cursor=None) -> dict:
        limit = max(1, min(int(limit), 500))
        sql = "SELECT seq, audit_id, ts, case_id, correlation_id, event_type, actor, data FROM audit_log WHERE merchant_id = ?"
        params = [merchant_id]
        for col, val in (("case_id", case_id), ("event_type", event_type), ("correlation_id", correlation_id)):
            if val:
                sql += f" AND {col} = ?"
                params.append(val)
        for op, val in ((">=", date_from), ("<=", date_to)):
            if val:
                try:
                    dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                except ValueError:
                    raise ValidationFailed("date filters must be ISO-8601") from None
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                sql += f" AND ts {op} ?"
                params.append(dt.astimezone(timezone.utc).isoformat(timespec="microseconds"))
        cur = _decode_cursor(cursor, 1)
        if cur:
            sql += " AND seq < ?"
            params.append(int(cur[0]))
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(limit + 1)
        with self.db.transaction(write=False) as tx:
            rows = tx.all(sql, params)
        for r in rows:
            r["data"] = loads(r["data"])
        nxt = _encode_cursor(rows[limit - 1]["seq"]) if len(rows) > limit else None
        return {"items": rows[:limit], "next_cursor": nxt}

    def overview(self, merchant_id: str) -> dict:
        with self.db.transaction(write=False) as tx:
            by_status = tx.all("SELECT status, COUNT(*) AS n, COALESCE(SUM(amount_paise),0) AS amount_paise"
                               " FROM payment_cases WHERE merchant_id = ? GROUP BY status", [merchant_id])
            rec = tx.all("SELECT provider_mode, COUNT(*) AS n, COALESCE(SUM(gross_recovered_paise),0) AS gross,"
                         " COALESCE(SUM(cash_collected_paise),0) AS cash, COALESCE(SUM(incentive_cost_paise),0) AS incentive"
                         " FROM recoveries WHERE merchant_id = ? GROUP BY provider_mode", [merchant_id])
            costs = tx.all("SELECT provider_mode, COUNT(*) AS n, COALESCE(SUM(action_cost_paise),0) AS cost"
                           " FROM action_attempts WHERE merchant_id = ? AND status = 'PROVIDER_ACCEPTED'"
                           " GROUP BY provider_mode", [merchant_id])
            exp = tx.one("SELECT COUNT(*) AS n, COALESCE(SUM(d.expected_value_paise),0) AS v FROM case_decisions d"
                         " JOIN payment_cases c ON c.case_id = d.case_id WHERE c.merchant_id = ?"
                         " AND d.selected_action <> 'NO_ACTION' AND c.status IN ('PENDING_APPROVAL','ACTION_QUEUED',"
                         "'PROVIDER_PENDING','OUTCOME_UNKNOWN')", [merchant_id])
            funnel = tx.one(
                "SELECT COUNT(*) AS failed,"
                " SUM(CASE WHEN d.decision_id IS NOT NULL THEN 1 ELSE 0 END) AS decided,"
                " SUM(CASE WHEN d.selected_action <> 'NO_ACTION' THEN 1 ELSE 0 END) AS recommended,"
                " SUM(CASE WHEN a.attempt_id IS NOT NULL THEN 1 ELSE 0 END) AS approved,"
                " SUM(CASE WHEN a.status = 'PROVIDER_ACCEPTED' THEN 1 ELSE 0 END) AS executed,"
                " SUM(CASE WHEN r.recovery_id IS NOT NULL THEN 1 ELSE 0 END) AS recovered"
                " FROM payment_cases c LEFT JOIN case_decisions d ON d.case_id = c.case_id"
                " LEFT JOIN action_attempts a ON a.case_id = c.case_id LEFT JOIN recoveries r ON r.case_id = c.case_id"
                " WHERE c.merchant_id = ?", [merchant_id])
            pend = tx.one("SELECT COUNT(*) AS n, MIN(created_at) AS oldest FROM approval_requests"
                          " WHERE merchant_id = ? AND status = 'PENDING'", [merchant_id])
            unmatched = tx.scalar("SELECT COUNT(*) FROM provider_events WHERE match_status = 'UNMATCHED'")
            flags = tx.scalar("SELECT COUNT(*) FROM payment_cases WHERE merchant_id = ? AND review_reason IS NOT NULL",
                              [merchant_id])
            since = (self.now() - timedelta(hours=24)).isoformat(timespec="microseconds")
            rejections = tx.scalar("SELECT COUNT(*) FROM webhook_rejections WHERE received_at >= ?", [since])
            origins = tx.all("SELECT data_origin, COUNT(*) AS n FROM payment_cases WHERE merchant_id = ? GROUP BY data_origin",
                             [merchant_id])
            policy = self.get_policy(merchant_id, tx)
            usage = self.guardrail_state(merchant_id, "__portfolio__", tx)
        status_map = {r["status"]: {"count": int(r["n"]), "amount_paise": int(r["amount_paise"])} for r in by_status}
        recovered_by_mode = {r["provider_mode"]: {"cases": int(r["n"]), "gross_recovered_paise": int(r["gross"]),
                                                  "cash_collected_paise": int(r["cash"]),
                                                  "incentive_cost_paise": int(r["incentive"])} for r in rec}
        cost_by_mode = {r["provider_mode"]: {"executed_actions": int(r["n"]), "action_cost_paise": int(r["cost"])}
                        for r in costs}
        exposure = [status_map.get(s, {"count": 0, "amount_paise": 0}) for s in OPEN_EXPOSURE]
        return {
            "merchant_id": merchant_id,
            "generated_at": self.now_iso(),
            "data_origins": {r["data_origin"]: int(r["n"]) for r in origins},
            "status_breakdown": status_map,
            "open_exposure": {"cases": sum(e["count"] for e in exposure),
                              "amount_paise": sum(e["amount_paise"] for e in exposure),
                              "statuses": list(OPEN_EXPOSURE)},
            "confirmed_recovered_by_provider_mode": recovered_by_mode,
            "action_costs_by_provider_mode": cost_by_mode,
            "expected_incremental_value_open_decisions": {
                "decisions": int(exp["n"]), "expected_value_paise": int(exp["v"]),
                "label": "MODEL_ESTIMATE"},
            "funnel": {k: int(v or 0) for k, v in funnel.items()},
            "attention": {
                "pending_approvals": int(pend["n"]), "oldest_pending_approval_at": pend["oldest"],
                "outcome_unknown": status_map.get("OUTCOME_UNKNOWN", {}).get("count", 0),
                "needs_review": status_map.get("NEEDS_REVIEW", {}).get("count", 0),
                "review_flags": int(flags or 0),
                "action_failed": status_map.get("ACTION_FAILED", {}).get("count", 0),
                "unmatched_provider_events": int(unmatched or 0),
                "webhook_rejections_24h": int(rejections or 0)},
            "budget_today": {"contacts_used": usage["contacts_used_today"],
                             "contact_budget": int(policy["daily_contact_budget"]),
                             "incentive_committed_paise": usage["incentive_committed_today_paise"],
                             "incentive_budget_paise": int(policy["daily_incentive_budget_paise"]),
                             "day_utc": self.now().date().isoformat()},
            "policy_version": policy["version"],
        }

    def unmatched_events(self, limit=50) -> list:
        with self.db.transaction(write=False) as tx:
            rows = tx.all("SELECT provider_event_id, event_type, received_at, outcome_reason, summary FROM provider_events"
                          " WHERE match_status = 'UNMATCHED' ORDER BY received_at DESC LIMIT ?", [int(limit)])
        for r in rows:
            r["summary"] = loads(r["summary"])
        return rows
