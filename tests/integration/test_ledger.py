"""
Integration tests for the payment-recovery ledger against a real database.

Backend: a fresh SQLite file per test (WAL, real transactions, real unique
constraints). Set CRRO_TEST_DATABASE_URL=postgresql://... to run the same
suite against PostgreSQL; with CRRO_REQUIRE_POSTGRES=1 (as in CI) a missing
Postgres is a FAILURE, not a skip.

Runs under both `python -m unittest` and pytest.
"""
import json
import os
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from crro.ledger.db import Database
from crro.ledger.errors import (BadRequest, Conflict, IdempotencyConflict, NotFound, PayloadTooLarge,
                                Unauthorized, ValidationFailed)
from crro.ledger.providers import SimulatedProvider, hmac_sha256_hex
from crro.ledger.schema import migrate
from crro.ledger.service import LedgerService, idempotency_key_for

SECRET = "test_webhook_secret_not_real"
M = "merchant_a"


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


def make_db():
    url = os.environ.get("CRRO_TEST_DATABASE_URL")
    if url:
        db = Database(url)
        with db.transaction() as tx:  # isolate: drop ledger tables
            for t in ("audit_log", "recoveries", "provider_events", "webhook_rejections", "action_attempts",
                      "approval_requests", "case_decisions", "payment_cases", "idempotency_keys", "budget_usage",
                      "customer_contact_state", "merchant_policies", "optimizer_runs", "sim_provider_objects",
                      "schema_migrations"):
                tx.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        return db
    if os.environ.get("CRRO_REQUIRE_POSTGRES") == "1":
        raise RuntimeError("CRRO_REQUIRE_POSTGRES=1 but CRRO_TEST_DATABASE_URL is not set")
    d = tempfile.mkdtemp(prefix="crro_test_")
    return Database(f"sqlite:///{d}/ledger.db")


def failed_payment(ext="pay_001", amount_paise=250_000, **kw):
    body = {"external_payment_id": ext, "customer_ref": kw.pop("customer_ref", "cust_1"),
            "amount_paise": amount_paise, "currency": "INR", "payment_method": "card",
            "failure_reason": "card_expired", "attempt_count": 1}
    body.update(kw)
    return body


def decision(action="PAYMENT_LINK", requires_approval=False, discount=0, contact=True):
    return {"selected_action": action, "reason_code": None, "candidates": [
        {"action": action, "incentive_discount_paise": discount, "action_cost_paise": 500,
         "requires_customer_contact": contact, "offer_id": "OFFER_2PCT" if discount else None}],
        "expected_value_paise": 12345, "p_control": 0.2, "p_selected": 0.5,
        "requires_approval": requires_approval, "approval_reason": "amount above threshold" if requires_approval else None,
        "threshold_paise": 5_000_000, "model_version": "test_model", "discount_paise": discount,
        "action_cost_paise": 500, "requires_contact": contact, "offer_id": "OFFER_2PCT" if discount else None}


class LedgerTestBase(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        migrate(self.db)
        self.clock = Clock()
        self.faults = {}
        self.provider = SimulatedProvider(self.db, SECRET, self.faults)
        self.svc = LedgerService(self.db, self.provider, clock=self.clock)

    def open_and_queue(self, ext="pay_001", amount_paise=250_000, action="PAYMENT_LINK", discount=0,
                       contact=True, **kw):
        status, resp, _ = self.svc.open_case(M, failed_payment(ext, amount_paise, **kw), idempotency_key="k-" + ext)
        self.svc.record_decision(resp["case_id"], decision(action, discount=discount, contact=contact))
        att = self.svc.get_case(M, resp["case_id"])["attempt"]
        return resp["case_id"], att

    def dispatched(self, ext="pay_001", **kw):
        case_id, att = self.open_and_queue(ext, **kw)
        att = self.svc.dispatch(att["attempt_id"])
        return case_id, att

    def deliver(self, raw, headers):
        return self.svc.ingest_webhook(raw, headers)

    def count(self, table, where="1=1", params=()):
        with self.db.transaction(write=False) as tx:
            return int(tx.scalar(f"SELECT COUNT(*) FROM {table} WHERE {where}", list(params)))


class TestIntakeIdempotency(LedgerTestBase):
    def test_same_key_same_payload_replays_stored_response(self):
        s1, r1, rep1 = self.svc.open_case(M, failed_payment(), "idem-1")
        s2, r2, rep2 = self.svc.open_case(M, failed_payment(), "idem-1")
        self.assertEqual((s1, rep1), (201, False))
        self.assertEqual((s2, r2, rep2), (201, r1, True))
        self.assertEqual(self.count("payment_cases"), 1)

    def test_same_key_different_payload_conflicts(self):
        self.svc.open_case(M, failed_payment(), "idem-1")
        with self.assertRaises(IdempotencyConflict):
            self.svc.open_case(M, failed_payment(amount_paise=999), "idem-1")
        self.assertEqual(self.count("payment_cases"), 1)

    def test_business_key_dedup_with_different_idempotency_keys(self):
        _, r1, _ = self.svc.open_case(M, failed_payment(), "idem-1")
        s2, r2, _ = self.svc.open_case(M, failed_payment(), "idem-2")
        self.assertEqual(s2, 200)
        self.assertTrue(r2["duplicate_of_existing_case"])
        self.assertEqual(r2["case_id"], r1["case_id"])

    def test_missing_key_and_invalid_payloads_rejected(self):
        with self.assertRaises(BadRequest):
            self.svc.open_case(M, failed_payment(), None)
        bad = [failed_payment(amount_paise=0), failed_payment(amount_paise=-5), failed_payment(currency="USD"),
               failed_payment(amount_paise=1.5), {**failed_payment(), "amount_inr": "10.00"},
               failed_payment(attempt_count=0), failed_payment(payment_method="Card; DROP"),
               {**failed_payment(), "evil": 1}, failed_payment(failed_at="2026-10-01T09:00:00")]
        for i, b in enumerate(bad):
            with self.assertRaises(ValidationFailed, msg=str(b)):
                self.svc.open_case(M, b, f"bad-{i}")
        self.assertEqual(self.count("payment_cases"), 0)

    def test_concurrent_duplicate_submissions_create_one_case(self):
        results, errors = [], []

        def go():
            try:
                results.append(self.svc.open_case(M, failed_payment(), "idem-race"))
            except Exception as e:  # pragma: no cover - surfaced by assertion
                errors.append(e)
        threads = [threading.Thread(target=go) for _ in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(self.count("payment_cases"), 1)
        self.assertEqual(len({r[1]["case_id"] for r in results}), 1)
        self.assertEqual(sum(1 for r in results if not r[2]), 1)  # exactly one non-replay


class TestDispatchIdempotency(LedgerTestBase):
    def test_happy_path_dispatch_creates_one_provider_object(self):
        case_id, att = self.dispatched()
        self.assertEqual(att["status"], "PROVIDER_ACCEPTED")
        self.assertEqual(self.svc.get_case(M, case_id)["case"]["status"], "PROVIDER_PENDING")
        self.assertEqual(self.provider.count_objects(), 1)

    def test_repeated_and_concurrent_dispatch_is_single_side_effect(self):
        case_id, att = self.open_and_queue()
        threads = [threading.Thread(target=self.svc.dispatch, args=(att["attempt_id"],)) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.svc.dispatch(att["attempt_id"])
        self.assertEqual(self.provider.count_objects(), 1)
        self.assertEqual(self.provider.calls, 1)

    def test_timeout_after_provider_success_resolves_without_duplicate(self):
        case_id, att = self.open_and_queue()
        self.faults[att["idempotency_key"]] = ["timeout_after"]
        out = self.svc.dispatch(att["attempt_id"])
        self.assertEqual(out["status"], "UNKNOWN")
        self.assertEqual(self.svc.get_case(M, case_id)["case"]["status"], "OUTCOME_UNKNOWN")
        self.assertEqual(self.provider.count_objects(), 1)  # provider DID create it
        res = self.svc.resolve_attempt(att["attempt_id"])
        self.assertEqual(res["status"], "PROVIDER_ACCEPTED")
        self.assertEqual(self.provider.count_objects(), 1)  # no second link
        self.assertEqual(self.svc.get_case(M, case_id)["case"]["status"], "PROVIDER_PENDING")

    def test_timeout_before_provider_commit_redispatches_with_same_key(self):
        case_id, att = self.open_and_queue()
        self.faults[att["idempotency_key"]] = ["timeout_before"]
        self.assertEqual(self.svc.dispatch(att["attempt_id"])["status"], "UNKNOWN")
        self.assertEqual(self.provider.count_objects(), 0)
        res = self.svc.resolve_attempt(att["attempt_id"])
        self.assertEqual(res["status"], "PROVIDER_ACCEPTED")
        self.assertEqual(self.provider.count_objects(), 1)

    def test_process_restart_does_not_duplicate_side_effect(self):
        case_id, att = self.open_and_queue()
        self.faults[att["idempotency_key"]] = ["timeout_after"]
        self.svc.dispatch(att["attempt_id"])
        # "restart": brand-new provider + service objects, same durable stores
        p2 = SimulatedProvider(self.db, SECRET)
        s2 = LedgerService(self.db, p2, clock=self.clock)
        s2.resolve_attempt(att["attempt_id"])
        s2.dispatch(att["attempt_id"])
        self.assertEqual(p2.count_objects(), 1)

    def test_provider_error_fails_case_and_releases_budget(self):
        case_id, att = self.open_and_queue()
        before = self.svc.guardrail_state(M, "x")["contacts_used_today"]
        self.faults[att["idempotency_key"]] = ["error"]
        out = self.svc.dispatch(att["attempt_id"])
        self.assertEqual(out["status"], "FAILED")
        self.assertEqual(self.svc.get_case(M, case_id)["case"]["status"], "ACTION_FAILED")
        self.assertEqual(self.svc.guardrail_state(M, "x")["contacts_used_today"], before - 1)

    def test_idempotency_key_is_stable_and_provider_safe(self):
        k = idempotency_key_for("case_abc", "ALTERNATIVE_PAYMENT_METHOD")
        self.assertEqual(k, idempotency_key_for("case_abc", "ALTERNATIVE_PAYMENT_METHOD"))
        self.assertLessEqual(len(k), 40)


class TestWebhooks(LedgerTestBase):
    def test_valid_paid_webhook_recovers_case_once(self):
        case_id, att = self.dispatched()
        raw, h = self.provider.build_webhook("payment_link.paid", att["provider_reference"])
        status, body = self.deliver(raw, h)
        self.assertEqual((status, body["outcome"]), (200, "RECOVERED"))
        detail = self.svc.get_case(M, case_id)
        self.assertEqual(detail["case"]["status"], "RECOVERED")
        self.assertEqual(detail["recovery"]["gross_recovered_paise"], 250_000)
        self.assertEqual(detail["recovery"]["cash_collected_paise"], 250_000)

    def test_signature_is_checked_over_raw_bytes(self):
        case_id, att = self.dispatched()
        raw, h = self.provider.build_webhook("payment_link.paid", att["provider_reference"])
        reserialised = json.dumps(json.loads(raw), indent=2).encode()  # same JSON, different bytes
        with self.assertRaises(Unauthorized):
            self.deliver(reserialised, h)
        with self.assertRaises(Unauthorized):
            self.deliver(raw, {**h, "x-razorpay-signature": "0" * 64})
        with self.assertRaises(Unauthorized):
            self.deliver(raw, {k: v for k, v in h.items() if k != "x-razorpay-signature"})
        with self.assertRaises(Unauthorized):
            self.deliver(raw, {**h, "x-razorpay-signature": "é" * 64})
        self.assertEqual(self.count("provider_events"), 0)
        self.assertEqual(self.count("webhook_rejections"), 4)
        self.assertEqual(self.svc.get_case(M, case_id)["case"]["status"], "PROVIDER_PENDING")

    def test_invalid_signature_cannot_poison_event_id(self):
        """Regression: the old handler stored invalid-signature deliveries in the
        dedup ledger, so the genuine event with the same id was later dropped."""
        case_id, att = self.dispatched()
        raw, h = self.provider.build_webhook("payment_link.paid", att["provider_reference"], event_id="evt_X")
        with self.assertRaises(Unauthorized):
            self.deliver(b'{"event":"payment_link.expired"}', {**h, "x-razorpay-signature": "bad"})
        status, body = self.deliver(raw, h)
        self.assertEqual(body["outcome"], "RECOVERED")

    def test_duplicate_delivery_has_no_second_side_effect(self):
        case_id, att = self.dispatched()
        raw, h = self.provider.build_webhook("payment_link.paid", att["provider_reference"])
        self.deliver(raw, h)
        for _ in range(5):
            status, body = self.deliver(raw, h)
            self.assertEqual((status, body["status"]), (200, "duplicate_ignored"))
        self.assertEqual(self.count("recoveries"), 1)
        self.assertEqual(self.count("provider_events"), 1)

    def test_concurrent_duplicate_deliveries_one_recovery(self):
        case_id, att = self.dispatched()
        raw, h = self.provider.build_webhook("payment_link.paid", att["provider_reference"])
        out = []
        threads = [threading.Thread(target=lambda: out.append(self.deliver(raw, h))) for _ in range(16)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(b["status"] for _, b in out).count("processed"), 1)
        self.assertEqual(self.count("recoveries"), 1)

    def test_same_payment_different_event_ids_counted_once(self):
        """payment_link.paid and payment.captured for one payment (different event ids)."""
        case_id, att = self.dispatched()
        raw1, h1 = self.provider.build_webhook("payment_link.paid", att["provider_reference"], payment_id="pay_1")
        raw2, h2 = self.provider.build_webhook("payment_link.paid", att["provider_reference"], payment_id="pay_1")
        self.deliver(raw1, h1)
        _, body = self.deliver(raw2, h2)
        self.assertEqual(body["outcome"], "NO_CHANGE")
        self.assertEqual(self.count("recoveries"), 1)

    def test_second_distinct_payment_flags_review_not_double_count(self):
        case_id, att = self.dispatched()
        self.deliver(*self.provider.build_webhook("payment_link.paid", att["provider_reference"], payment_id="pay_1"))
        _, body = self.deliver(*self.provider.build_webhook("payment_link.paid", att["provider_reference"],
                                                            payment_id="pay_2"))
        self.assertEqual(body["outcome"], "NEEDS_REVIEW")
        detail = self.svc.get_case(M, case_id)
        self.assertEqual(detail["case"]["status"], "RECOVERED")
        self.assertEqual(detail["case"]["review_reason"], "POSSIBLE_DUPLICATE_PAYMENT")
        self.assertEqual(self.count("recoveries"), 1)

    def test_stale_failure_after_paid_never_downgrades(self):
        case_id, att = self.dispatched()
        t0 = 1_790_000_000
        self.deliver(*self.provider.build_webhook("payment_link.paid", att["provider_reference"], created_at=t0 + 10))
        for et in ("payment_link.expired", "payment_link.cancelled", "payment.failed"):
            _, body = self.deliver(*self.provider.build_webhook(et, att["provider_reference"], created_at=t0))
            self.assertEqual(body["outcome"], "STALE_IGNORED" if et != "payment.failed" else body["outcome"])
        self.assertEqual(self.svc.get_case(M, case_id)["case"]["status"], "RECOVERED")
        self.assertEqual(self.count("recoveries"), 1)

    def test_newer_failure_event_after_paid_still_cannot_downgrade(self):
        case_id, att = self.dispatched()
        self.deliver(*self.provider.build_webhook("payment_link.paid", att["provider_reference"], created_at=100))
        _, body = self.deliver(*self.provider.build_webhook("payment_link.expired", att["provider_reference"],
                                                            created_at=500))
        self.assertEqual((body["outcome"], body["reason"]), ("STALE_IGNORED", "CASE_ALREADY_RECOVERED"))
        self.assertEqual(self.svc.get_case(M, case_id)["case"]["status"], "RECOVERED")

    def test_state_machine_has_no_exit_from_recovered(self):
        from crro.ledger.states import ALLOWED, can_transition
        self.assertEqual(ALLOWED["RECOVERED"], set())
        for target in ALLOWED:
            self.assertFalse(can_transition("RECOVERED", target))

    def test_paid_after_expired_is_recovered_as_late_payment(self):
        case_id, att = self.dispatched()
        self.deliver(*self.provider.build_webhook("payment_link.expired", att["provider_reference"], created_at=100))
        self.assertEqual(self.svc.get_case(M, case_id)["case"]["status"], "EXPIRED")
        _, body = self.deliver(*self.provider.build_webhook("payment_link.paid", att["provider_reference"],
                                                            created_at=90))
        self.assertEqual((body["outcome"], body["reason"]), ("RECOVERED", "LATE_PAYMENT_AFTER_EXPIRY"))

    def test_older_expiry_event_is_stale(self):
        case_id, att = self.dispatched()
        self.deliver(*self.provider.build_webhook("payment_link.partially_paid", att["provider_reference"],
                                                  created_at=200, amount_paid_paise=100))
        _, body = self.deliver(*self.provider.build_webhook("payment_link.expired", att["provider_reference"],
                                                            created_at=150))
        self.assertEqual(body["outcome"], "STALE_IGNORED")

    def test_amount_mismatch_routes_to_review(self):
        case_id, att = self.dispatched()
        _, body = self.deliver(*self.provider.build_webhook("payment_link.paid", att["provider_reference"],
                                                            amount_paid_paise=1_500_000))
        self.assertEqual((body["outcome"], body["reason"]), ("NEEDS_REVIEW", "AMOUNT_MISMATCH"))
        self.assertEqual(self.svc.get_case(M, case_id)["case"]["status"], "NEEDS_REVIEW")
        self.assertEqual(self.count("recoveries"), 0)

    def test_currency_mismatch_routes_to_review(self):
        case_id, att = self.dispatched()
        _, body = self.deliver(*self.provider.build_webhook("payment_link.paid", att["provider_reference"],
                                                            currency="USD"))
        self.assertEqual(body["reason"], "CURRENCY_MISMATCH")
        self.assertEqual(self.count("recoveries"), 0)

    def test_wrong_merchant_routes_to_review(self):
        case_id, att = self.dispatched()
        _, body = self.deliver(*self.provider.build_webhook("payment_link.paid", att["provider_reference"],
                                                            merchant_id_override="merchant_b"))
        self.assertEqual(body["reason"], "MERCHANT_MISMATCH")
        self.assertEqual(self.count("recoveries"), 0)

    def test_unknown_reference_is_recorded_unmatched(self):
        raw, h = self.provider.build_webhook("payment_link.paid", "plink_does_not_exist")
        status, body = self.deliver(raw, h)
        self.assertEqual((status, body["match_status"]), (200, "UNMATCHED"))
        self.assertEqual(len(self.svc.unmatched_events()), 1)

    def test_malformed_and_oversized_rejected(self):
        raw = b"{not json"
        with self.assertRaises(BadRequest):
            self.deliver(raw, {"x-razorpay-signature": hmac_sha256_hex(SECRET, raw), "x-razorpay-event-id": "e1"})
        big = b"{" + b" " * (70 * 1024) + b"}"
        with self.assertRaises(PayloadTooLarge):
            self.deliver(big, {"x-razorpay-signature": hmac_sha256_hex(SECRET, big), "x-razorpay-event-id": "e2"})
        ok = b'{"event":"payment_link.paid"}'
        with self.assertRaises(BadRequest):  # signed but missing event id
            self.deliver(ok, {"x-razorpay-signature": hmac_sha256_hex(SECRET, ok)})

    def test_webhook_before_dispatch_result_resolves_unknown(self):
        case_id, att = self.open_and_queue()
        self.faults[att["idempotency_key"]] = ["timeout_after"]
        self.svc.dispatch(att["attempt_id"])
        ref = self.provider.lookup(att["idempotency_key"]).provider_reference
        _, body = self.deliver(*self.provider.build_webhook("payment_link.paid", ref))
        self.assertEqual(body["outcome"], "RECOVERED")
        detail = self.svc.get_case(M, case_id)
        self.assertEqual(detail["attempt"]["provider_reference"], ref)

    def test_retry_declined_marks_still_failed(self):
        case_id, att = self.dispatched(action="RETRY_NOW", contact=False)
        _, body = self.deliver(*self.provider.build_webhook("payment.failed", att["provider_reference"]))
        self.assertEqual(body["outcome"], "STILL_FAILED")

    def test_incentive_reconciliation_accounts_discount(self):
        case_id, att = self.dispatched(amount_paise=1_000_000, action="SMALL_INCENTIVE", discount=20_000)
        self.assertEqual(att["collect_amount_paise"], 980_000)
        _, body = self.deliver(*self.provider.build_webhook("payment_link.paid", att["provider_reference"]))
        rec = self.svc.get_case(M, case_id)["recovery"]
        self.assertEqual((rec["gross_recovered_paise"], rec["cash_collected_paise"], rec["incentive_cost_paise"]),
                         (1_000_000, 980_000, 20_000))


class TestApprovals(LedgerTestBase):
    def pending(self, ext="pay_big"):
        _, resp, _ = self.svc.open_case(M, failed_payment(ext, 9_000_000), "k-" + ext)
        self.svc.record_decision(resp["case_id"], decision(requires_approval=True))
        apr = self.svc.list_approvals(M)["items"][0]
        return resp["case_id"], apr["approval_id"]

    def test_approve_queues_exactly_one_attempt_and_is_idempotent(self):
        case_id, apr = self.pending()
        a1 = self.svc.decide_approval(M, apr, "APPROVE", "alice", "ok")
        a2 = self.svc.decide_approval(M, apr, "APPROVE", "alice", "ok")
        self.assertFalse(a1["replayed"])
        self.assertTrue(a2["replayed"])
        self.assertEqual(self.count("action_attempts"), 1)
        self.assertEqual(self.svc.get_case(M, case_id)["case"]["status"], "ACTION_QUEUED")

    def test_conflicting_decision_after_approval_is_409(self):
        case_id, apr = self.pending()
        self.svc.decide_approval(M, apr, "APPROVE", "alice", "ok")
        with self.assertRaises(Conflict):
            self.svc.decide_approval(M, apr, "REJECT", "bob", "changed my mind")

    def test_reject_requires_reason_and_closes_case(self):
        case_id, apr = self.pending()
        with self.assertRaises(ValidationFailed):
            self.svc.decide_approval(M, apr, "REJECT", "bob", "")
        self.svc.decide_approval(M, apr, "REJECT", "bob", "customer disputed")
        self.assertEqual(self.svc.get_case(M, case_id)["case"]["status"], "ACTION_REJECTED")
        self.assertEqual(self.count("action_attempts"), 0)

    def test_other_merchant_cannot_see_or_decide(self):
        case_id, apr = self.pending()
        with self.assertRaises(NotFound):
            self.svc.decide_approval("merchant_b", apr, "APPROVE", "mallory", "x")
        with self.assertRaises(NotFound):
            self.svc.get_case("merchant_b", case_id)
        self.assertEqual(self.svc.list_cases("merchant_b")["items"], [])

    def test_concurrent_approvals_single_attempt(self):
        case_id, apr = self.pending()
        errs = []

        def go():
            try:
                self.svc.decide_approval(M, apr, "APPROVE", "alice", "ok")
            except Exception as e:
                errs.append(e)
        ts = [threading.Thread(target=go) for _ in range(10)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errs, [])
        self.assertEqual(self.count("action_attempts"), 1)

    def test_expired_approval_cannot_be_approved(self):
        case_id, apr = self.pending()
        self.clock.advance(hours=25)
        out = self.svc.decide_approval(M, apr, "APPROVE", "alice", "late")
        self.assertTrue(out["expired"])
        self.assertEqual(self.svc.get_case(M, case_id)["case"]["status"], "ACTION_REJECTED")
        self.assertEqual(self.count("action_attempts"), 0)


class TestBudgetsAndAudit(LedgerTestBase):
    def test_budget_reservation_is_atomic_and_enforced(self):
        self.svc.update_policy(M, {"daily_contact_budget": 2}, "admin")
        statuses = []
        for i in range(4):
            _, r, _ = self.svc.open_case(M, failed_payment(f"p{i}", customer_ref=f"c{i}"), f"k{i}")
            out = self.svc.record_decision(r["case_id"], decision())
            statuses.append(out["selected_action"])
        self.assertEqual(statuses, ["PAYMENT_LINK", "PAYMENT_LINK", "NO_ACTION", "NO_ACTION"])
        self.assertEqual(self.svc.guardrail_state(M, "x")["contacts_used_today"], 2)

    def test_policy_validation(self):
        for bad in ({"daily_contact_budget": -1}, {"allowed_actions": ["HACK"]}, {"nope": 1},
                    {"daily_incentive_budget_paise": 1.5}, {"approval_threshold_paise": {"X": 1}}):
            with self.assertRaises(ValidationFailed):
                self.svc.update_policy(M, bad, "admin")

    def test_audit_is_append_only_and_correlated(self):
        case_id, att = self.dispatched()
        self.deliver(*self.provider.build_webhook("payment_link.paid", att["provider_reference"]))
        detail = self.svc.get_case(M, case_id)
        corr = {t["correlation_id"] for t in detail["timeline"]}
        self.assertEqual(corr, {detail["case"]["correlation_id"]})
        types = [t["event_type"] for t in detail["timeline"]]
        for expected in ("PAYMENT_FAILED_RECEIVED", "DECISION_RECORDED", "ACTION_QUEUED", "ACTION_DISPATCH_STARTED",
                         "ACTION_DISPATCH_RESULT", "CASE_STATUS_CHANGED"):
            self.assertIn(expected, types)
        with self.assertRaises(Exception):
            with self.db.transaction() as tx:
                tx.execute("DELETE FROM audit_log")

    def test_rollback_on_injected_failure_leaves_no_partial_state(self):
        _, r, _ = self.svc.open_case(M, failed_payment(), "k")
        bad = decision()
        bad["model_version"] = None  # violates NOT NULL inside the decision transaction
        with self.assertRaises(Exception):
            self.svc.record_decision(r["case_id"], bad)
        self.assertEqual(self.count("case_decisions"), 0)
        self.assertEqual(self.count("action_attempts"), 0)
        self.assertEqual(self.svc.get_case(M, r["case_id"])["case"]["status"], "OPEN")
        self.assertEqual(self.svc.guardrail_state(M, "x")["contacts_used_today"], 0)

    def test_overview_separates_confirmed_from_expected(self):
        case_id, att = self.dispatched()
        ov = self.svc.overview(M)
        self.assertEqual(ov["confirmed_recovered_by_provider_mode"], {})
        self.assertEqual(ov["expected_incremental_value_open_decisions"]["expected_value_paise"], 12345)
        self.deliver(*self.provider.build_webhook("payment_link.paid", att["provider_reference"]))
        ov = self.svc.overview(M)
        self.assertEqual(ov["confirmed_recovered_by_provider_mode"]["SIMULATOR"]["gross_recovered_paise"], 250_000)
        self.assertEqual(ov["expected_incremental_value_open_decisions"]["expected_value_paise"], 0)
        self.assertEqual(ov["funnel"]["recovered"], 1)

    def test_pagination_is_stable(self):
        for i in range(7):
            self.clock.advance(seconds=1)
            self.svc.open_case(M, failed_payment(f"p{i}"), f"k{i}")
        seen, cur = [], None
        while True:
            page = self.svc.list_cases(M, limit=3, cursor=cur)
            seen += [r["case_id"] for r in page["items"]]
            cur = page["next_cursor"]
            if not cur:
                break
        self.assertEqual(len(seen), 7)
        self.assertEqual(len(set(seen)), 7)


if __name__ == "__main__":
    unittest.main()
