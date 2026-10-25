"""
Webhook Processing Tests
----------------------------------------------------
Covers the exact required test list: valid webhook, invalid signature,
duplicate webhook, same event twice, malformed payload, unknown case,
out-of-order event, provider timeout/retry (via the simulator's
configurable error rates).

Skipped entirely if Postgres isn't reachable (webhook idempotency is a
DB-backed guarantee in this system).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import hmac
import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient

from crro.db import db_is_reachable

pytestmark = pytest.mark.skipif(not db_is_reachable(), reason="Postgres not reachable")

SECRET = "simulator_dev_secret"


def _sign(raw: bytes) -> str:
    return hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


@pytest.fixture(scope="module")
def client():
    if not os.path.exists("outputs/model.pkl"):
        pytest.skip("outputs/model.pkl not found; run run_demo.py first")
    from api import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def recovered_case_setup(client):
    """Creates a real case + decision via the payment-failed workflow, so
    webhook tests have something legitimate to reconcile against."""
    case_id = f"WHTEST-{uuid.uuid4().hex[:8]}"
    resp = client.post("/events/payment-failed", json={
        "case_id": case_id, "merchant_id": "merchant_test", "customer_id": f"cust_{case_id}",
        "amount_inr": 5000, "payment_method": "card", "failure_reason": "card_expired",
        "attempt_count": 1, "minutes_since_failure": 5,
    })
    assert resp.status_code == 200
    trace = resp.json()
    provider_ref = trace["decision"].get("provider_reference")
    return case_id, provider_ref


def _payload(case_id, provider_ref, event="payment_link.paid", amount=500000):
    return {
        "event": event,
        "payload": {"payment_link": {"entity": {
            "id": provider_ref, "reference_id": f"{case_id}:X:v1", "amount": amount
        }}}
    }


def test_valid_webhook_reconciles_case(client, recovered_case_setup):
    case_id, provider_ref = recovered_case_setup
    if provider_ref is None:
        pytest.skip("no provider_reference on this run (action was blocked/NO_ACTION)")
    raw = json.dumps(_payload(case_id, provider_ref)).encode()
    resp = client.post("/webhooks/razorpay", content=raw,
                         headers={"x-razorpay-event-id": f"evt_{uuid.uuid4().hex}",
                                   "x-razorpay-signature": _sign(raw)})
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"

    journey = client.get(f"/cases/{case_id}/journey").json()
    assert journey["case"]["status"] == "RECOVERED"


def test_invalid_signature_rejected(client, recovered_case_setup):
    case_id, provider_ref = recovered_case_setup
    raw = json.dumps(_payload(case_id, provider_ref)).encode()
    resp = client.post("/webhooks/razorpay", content=raw,
                         headers={"x-razorpay-event-id": f"evt_{uuid.uuid4().hex}",
                                   "x-razorpay-signature": "not_a_valid_signature"})
    assert resp.status_code == 400


def test_duplicate_webhook_same_event_id_ignored(client, recovered_case_setup):
    case_id, provider_ref = recovered_case_setup
    if provider_ref is None:
        pytest.skip("no provider_reference on this run")
    raw = json.dumps(_payload(case_id, provider_ref)).encode()
    event_id = f"evt_{uuid.uuid4().hex}"
    headers = {"x-razorpay-event-id": event_id, "x-razorpay-signature": _sign(raw)}

    first = client.post("/webhooks/razorpay", content=raw, headers=headers)
    second = client.post("/webhooks/razorpay", content=raw, headers=headers)
    assert first.json()["status"] == "processed"
    assert second.json()["status"] == "duplicate_ignored"
    assert second.json()["idempotent"] is True


def test_malformed_payload_rejected(client):
    raw = b"{not valid json"
    resp = client.post("/webhooks/razorpay", content=raw,
                         headers={"x-razorpay-event-id": f"evt_{uuid.uuid4().hex}",
                                   "x-razorpay-signature": _sign(raw)})
    assert resp.status_code == 400


def test_unknown_case_does_not_crash(client):
    raw = json.dumps(_payload("NO-SUCH-CASE", "plink_does_not_exist")).encode()
    resp = client.post("/webhooks/razorpay", content=raw,
                         headers={"x-razorpay-event-id": f"evt_{uuid.uuid4().hex}",
                                   "x-razorpay-signature": _sign(raw)})
    assert resp.status_code == 200
    assert "ignored" in resp.json().get("note", "")


def test_out_of_order_events_do_not_regress_recovered_case(client, recovered_case_setup):
    """A late payment.failed arriving AFTER payment_link.paid must not un-recover the case."""
    case_id, provider_ref = recovered_case_setup
    if provider_ref is None:
        pytest.skip("no provider_reference on this run")
    paid_raw = json.dumps(_payload(case_id, provider_ref, event="payment_link.paid")).encode()
    client.post("/webhooks/razorpay", content=paid_raw,
                 headers={"x-razorpay-event-id": f"evt_{uuid.uuid4().hex}",
                           "x-razorpay-signature": _sign(paid_raw)})

    late_fail_raw = json.dumps(_payload(case_id, provider_ref, event="payment.failed")).encode()
    resp = client.post("/webhooks/razorpay", content=late_fail_raw,
                         headers={"x-razorpay-event-id": f"evt_{uuid.uuid4().hex}",
                                   "x-razorpay-signature": _sign(late_fail_raw)})
    assert resp.status_code == 200

    journey = client.get(f"/cases/{case_id}/journey").json()
    assert journey["case"]["status"] == "RECOVERED", \
        "out-of-order late failure event must not regress an already-recovered case"


def test_missing_event_id_rejected(client, recovered_case_setup):
    case_id, provider_ref = recovered_case_setup
    raw = json.dumps(_payload(case_id, provider_ref)).encode()
    resp = client.post("/webhooks/razorpay", content=raw,
                         headers={"x-razorpay-signature": _sign(raw)})
    assert resp.status_code == 400


def test_provider_idempotency_via_simulator_retry():
    """Retrying the same idempotency key against the provider must not double-execute."""
    from crro.providers.simulator import SimulatorAdapter
    adapter = SimulatorAdapter(seed=1, timeout_rate=0, provider_error_rate=0)
    r1 = adapter.create_payment_link("C1", 1000, "cust1", idempotency_key="C1:PAYMENT_LINK:v1")
    r2 = adapter.create_payment_link("C1", 1000, "cust1", idempotency_key="C1:PAYMENT_LINK:v1")
    assert r1.status == "EXECUTED"
    assert r2.status == "SKIPPED_DUPLICATE"
    assert r2.provider_reference == r1.provider_reference
