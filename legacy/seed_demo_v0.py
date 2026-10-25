"""
Seeds a batch of realistic payment.failed cases into a running CRRO API
(e.g. http://localhost:8000), then simulates payment_link.paid webhooks
for some of them so the dashboard shows a mix of recovered / pending /
blocked cases -- like demo.sh does for a single case, but for many.

Usage:
    python3 seed_demo.py [API_URL] [NUM_CASES]

Defaults: API_URL=http://localhost:8000, NUM_CASES=15
"""
import sys
import json
import time
import random
import hmac
import hashlib
import urllib.request

API_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
NUM_CASES = int(sys.argv[2]) if len(sys.argv) > 2 else 15

FAILURE_REASONS = ["insufficient_funds", "bank_decline", "network_error",
                    "risk_block", "otp_timeout", "card_expired"]
PAYMENT_METHODS = ["upi", "card", "netbanking", "wallet"]
MERCHANTS = ["merchant_ecom", "merchant_subs", "merchant_travel",
             "merchant_food", "merchant_edtech"]

random.seed(42)


def post(path, body):
    raw = json.dumps(body).encode()
    req = urllib.request.Request(API_URL + path, data=raw, method="POST",
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def simulate_webhook(case_id, provider_ref):
    payload = {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {
        "id": provider_ref, "reference_id": f"{case_id}:X:v1", "amount": 1500000}}}}
    raw = json.dumps(payload).encode()
    sig = hmac.new(b"simulator_dev_secret", raw, hashlib.sha256).hexdigest()
    req = urllib.request.Request(API_URL + "/webhooks/razorpay", data=raw, method="POST",
                                  headers={"Content-Type": "application/json",
                                           "x-razorpay-event-id": f"evt_seed_{provider_ref}",
                                           "x-razorpay-signature": sig})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def main():
    print(f"Seeding {NUM_CASES} demo cases against {API_URL} ...")
    recovered, pending, blocked = 0, 0, 0

    for i in range(NUM_CASES):
        case_id = f"SEED-{int(time.time())}-{i:03d}"
        event = {
            "case_id": case_id,
            "merchant_id": random.choice(MERCHANTS),
            "customer_id": f"cust_seed_{i:03d}",
            "amount_inr": random.choice([2500, 5000, 8500, 12000, 15000, 22000, 35000]),
            "payment_method": random.choice(PAYMENT_METHODS),
            "failure_reason": random.choice(FAILURE_REASONS),
            "attempt_count": random.choice([1, 1, 1, 2, 3]),
            "minutes_since_failure": random.choice([5, 10, 30, 60, 120]),
        }
        try:
            trace = post("/events/payment-failed", event)
        except Exception as e:
            print(f"  [{case_id}] FAILED to submit: {e}")
            continue

        decision = trace.get("decision", {})
        provider_ref = decision.get("provider_reference")
        action = decision.get("action", "?")

        if provider_ref and random.random() < 0.6:
            # simulate ~60% of executed actions getting paid
            time.sleep(0.05)
            try:
                simulate_webhook(case_id, provider_ref)
                recovered += 1
                print(f"  [{case_id}] action={action} -> RECOVERED")
            except Exception as e:
                pending += 1
                print(f"  [{case_id}] action={action} -> webhook failed: {e}")
        elif provider_ref:
            pending += 1
            print(f"  [{case_id}] action={action} -> pending")
        else:
            blocked += 1
            print(f"  [{case_id}] action={action} -> NO_ACTION / blocked")

        time.sleep(0.05)

    print("\nDone.")
    print(f"  recovered: {recovered}  pending: {pending}  no_action/blocked: {blocked}")
    print("Refresh the dashboard to see the updated numbers.")


if __name__ == "__main__":
    main()
