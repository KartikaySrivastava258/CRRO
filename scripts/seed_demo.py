#!/usr/bin/env python3
"""
Seed the running API with SYNTHETIC demo data over HTTP (an external client:
it builds and HMAC-signs Razorpay-shaped webhooks itself, so the server's real
raw-body verification path is exercised).

    python3 scripts/seed_demo.py --api http://127.0.0.1:8000

Re-running is safe: every request uses a fixed Idempotency-Key and every
webhook a fixed event id, so nothing is duplicated (replays are reported).
Scenarios: card expired, insufficient funds (retry), network error with
duplicate webhook deliveries, OTP timeout (awaiting customer), high amount
pending approval, approved-then-expired, opted-out customer, cooldown/contact
cap for a repeat customer, provider timeout (unknown outcome), amount mismatch,
max-attempts ineligibility, an unmatched provider event and a forged webhook.
"""
import argparse
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request

TOKEN = os.environ.get("CRRO_SEED_TOKEN", "demo-operator-token-local-only")
SECRET = os.environ.get("CRRO_SIMULATOR_WEBHOOK_SECRET", "dev_only_simulator_webhook_secret")


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def call(self, method, path, body=None, headers=None, raw=None, auth=True):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = {"Content-Type": "application/json", **(headers or {})}
        if auth:
            h["Authorization"] = f"Bearer {TOKEN}"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


def webhook(c, event_type, attempt, *, event_id, amount_paid=None, created_at=None, payment_id=None):
    """Razorpay-shaped event built and signed by the client."""
    ref, amount = attempt["provider_reference"], attempt["collect_amount_paise"]
    paid = amount if amount_paid is None else amount_paid
    notes = {"merchant_id": attempt["merchant_id"], "case_id": attempt["case_id"], "attempt_id": attempt["attempt_id"]}
    pay = {"entity": {"id": payment_id or "pay_" + hashlib.sha256(ref.encode()).hexdigest()[:14], "amount": paid,
                      "currency": "INR", "status": "captured" if event_type.endswith(("paid", "captured")) else "failed",
                      "notes": {**notes, "crro_reference_id": attempt["idempotency_key"],
                                "crro_provider_reference": ref}}}
    if event_type.startswith("payment_link."):
        body = {"entity": "event", "event": event_type, "payload": {"payment_link": {"entity": {
            "id": ref, "amount": amount, "amount_paid": paid if event_type.endswith("paid") else 0, "currency": "INR",
            "reference_id": attempt["idempotency_key"], "status": event_type.split(".")[1], "notes": notes}}}}
        if event_type.endswith("paid"):
            body["payload"]["payment"] = pay
    else:
        body = {"entity": "event", "event": event_type, "payload": {"payment": pay}}
    body["created_at"] = int(created_at or time.time())
    raw = json.dumps(body, separators=(",", ":")).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return c.call("POST", "/v1/webhooks/razorpay", raw=raw, auth=False,
                  headers={"x-razorpay-signature": sig, "x-razorpay-event-id": event_id})


CASES = [  # ext_id, customer, amount, method, reason, attempts, extra headers, what happens next
    ("DEMO-001", "cust_A", "4999.50", "card", "card_expired", 1, {}, "paid"),
    ("DEMO-002", "cust_B", "1250.00", "upi", "insufficient_funds", 1, {}, "paid"),
    ("DEMO-003", "cust_C", "780.00", "upi", "network_error", 1, {}, "paid_x3"),
    ("DEMO-004", "cust_D", "3200.00", "card", "otp_timeout", 1, {}, "await"),
    ("DEMO-005", "cust_E", "85000.00", "card", "card_expired", 1, {}, "await"),
    ("DEMO-006", "cust_F", "150000.00", "netbanking", "bank_decline", 1, {}, "reject"),
    ("DEMO-007", "cust_optout", "2000.00", "card", "risk_block", 1, {}, "await"),
    ("DEMO-008", "cust_A", "1500.00", "card", "card_expired", 1, {}, "await"),
    ("DEMO-009", "cust_G", "5600.00", "card", "card_expired", 1, {"X-Simulator-Fault": "timeout_after"}, "await"),
    ("DEMO-010", "cust_H", "6000.00", "card", "card_expired", 1, {}, "amount_mismatch"),
    ("DEMO-011", "cust_I", "500.00", "netbanking", "bank_decline", 3, {}, "await"),
    ("DEMO-012", "cust_J", "2750.00", "wallet", "risk_block", 1, {}, "expire"),
    ("DEMO-013", "cust_K", "18000.00", "upi", "insufficient_funds", 2, {}, "paid"),
    ("DEMO-014", "cust_L", "960.00", "card", "network_error", 1, {}, "await"),
    ("DEMO-015", "cust_M", "65000.00", "card", "card_expired", 1, {}, "approve_then_paid"),
    ("DEMO-016", "cust_optout", "1100.00", "upi", "network_error", 1, {}, "paid"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--skip-optimizer", action="store_true")
    c = Client(ap.parse_args().api)
    a = ap.parse_args()
    st, me = c.call("GET", "/v1/me")
    if st != 200:
        raise SystemExit(f"cannot authenticate against {a.api}: {st} {me}")
    print(f"seeding {me['merchant_id']} on {me['environment']} (SYNTHETIC DEMO DATA)")
    c.call("POST", "/v1/customers/cust_optout/opt-out", {"opted_out": True})
    for ext, cust, amt, method, reason, attempts, extra, then in CASES:
        st, r = c.call("POST", "/v1/events/payment-failed",
                       {"external_payment_id": ext, "customer_ref": cust, "amount_inr": amt, "payment_method": method,
                        "failure_reason": reason, "attempt_count": attempts,
                        "features": {"customer_success_rate": 0.35, "customer_previous_recovery_rate": 0.3}},
                       headers={"Idempotency-Key": f"seed-{ext}", **extra})
        if st not in (200, 201):
            print(f"  {ext}: HTTP {st} {r}")
            continue
        _, d = c.call("GET", f"/v1/cases/{r['case_id']}")
        att, note = d["attempt"], ""
        if then in ("reject", "approve_then_paid") and d["approval"] and d["approval"]["status"] == "PENDING":
            apr = d["approval"]["approval_id"]
            if then == "reject":
                c.call("POST", f"/v1/approvals/{apr}/decision",
                       {"decision": "REJECT", "note": "Seed: customer disputed the original charge"})
                note = "approval rejected by operator"
            else:
                c.call("POST", f"/v1/approvals/{apr}/decision",
                       {"decision": "APPROVE", "note": "Seed: verified customer by phone"})
                att = c.call("GET", f"/v1/cases/{r['case_id']}")[1]["attempt"]
                if att and att.get("provider_reference"):
                    st2, w = webhook(c, "payment_link.paid", att, event_id=f"evt_seed_{ext}_paid")
                    note = f"approved -> paid: {w.get('outcome') or w.get('status')}"
        elif att and att.get("provider_reference"):
            retry = att["action"].startswith("RETRY")
            paid_event = "payment.captured" if retry else "payment_link.paid"
            if then in ("paid", "paid_x3"):
                for _ in range(3 if then == "paid_x3" else 1):
                    st2, w = webhook(c, paid_event, att, event_id=f"evt_seed_{ext}_paid")
                note = f"webhook {paid_event}: {w.get('outcome') or w.get('status')}"
            elif then == "amount_mismatch":
                st2, w = webhook(c, paid_event, att, event_id=f"evt_seed_{ext}_paid", amount_paid=1_500_000)
                note = f"paid ₹15,000 vs expected: {w.get('outcome')} ({w.get('reason')})"
            elif then == "expire" and not retry:
                st2, w = webhook(c, "payment_link.expired", att, event_id=f"evt_seed_{ext}_expired")
                note = f"link expired: {w.get('outcome')}"
        _, d = c.call("GET", f"/v1/cases/{r['case_id']}")
        print(f"  {ext} {reason:18s} ₹{amt:>9s} -> {(d['decision'] or {}).get('selected_action', '-'):26s}"
              f" {d['case']['status']:16s} {'(replayed)' if r.get('replayed') else ''} {note}")
    # Provider event that matches nothing we sent (must land in the attention queue, not crash).
    fake = {"provider_reference": "plink_unknown_demo", "collect_amount_paise": 99900, "merchant_id": "unknown",
            "case_id": "unknown", "attempt_id": "unknown", "idempotency_key": "crro_unknown_reference_000"}
    print("  unmatched provider event ->", webhook(c, "payment_link.paid", fake, event_id="evt_seed_unmatched")[1].get("match_status"))
    raw = b'{"event":"payment_link.paid"}'
    st, _ = c.call("POST", "/v1/webhooks/razorpay", raw=raw, auth=False,
                   headers={"x-razorpay-signature": "f" * 64, "x-razorpay-event-id": "evt_seed_forged"})
    print(f"  forged webhook (bad signature) -> HTTP {st}")
    if not a.skip_optimizer:
        st, run = c.call("POST", "/v1/optimizer/runs", {"n_cases": 1500, "seed": 2026})
        print(f"  optimizer run {run.get('run_id')} -> {run.get('status')} in {run.get('duration_ms')} ms (HTTP {st})")


if __name__ == "__main__":
    main()
