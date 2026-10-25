#!/usr/bin/env bash
# docker_demo.sh
#
# Runs the same live single-case recovery walkthrough as demo.sh's steps 6-12
# (payment.failed -> AI analyst -> counterfactual scoring -> policy/execution
# -> webhook -> reconciliation -> journey), but against an already-running
# Docker Compose stack instead of starting its own Postgres/API.
#
# Steps 1-5 of the original demo.sh (start Postgres, migrate, run the batch
# benchmark from run_demo.py, generate the report, start the API) are already
# done by `docker compose up --build` -- run_demo.py executes at image build
# time (see Dockerfile), and the API + Postgres are already live containers.
#
# Usage:
#   ./docker_demo.sh [API_URL]
# Default API_URL: http://localhost:8000

set -euo pipefail

API_URL="${1:-http://localhost:8000}"

echo "== checking API is reachable at $API_URL =="
curl -sf "$API_URL/health" > /dev/null || {
  echo "API not reachable at $API_URL -- is 'docker compose up' running?"
  exit 1
}
curl -s "$API_URL/health"; echo

echo "== simulating one live recovery case =="
CASE_ID="DEMO-$(date +%s)"
TRACE=$(curl -s -X POST "$API_URL/events/payment-failed" \
  -H "Content-Type: application/json" \
  -d "{\"case_id\":\"$CASE_ID\",\"merchant_id\":\"merchant_demo\",\"customer_id\":\"cust_demo\",\"amount_inr\":15000,\"payment_method\":\"card\",\"failure_reason\":\"card_expired\",\"attempt_count\":1,\"minutes_since_failure\":10}")
echo "$TRACE" | python3 -m json.tool
PROVIDER_REF=$(echo "$TRACE" | python3 -c "import json,sys; print(json.load(sys.stdin)['decision'].get('provider_reference') or '')")

if [ -n "$PROVIDER_REF" ]; then
  echo "== simulating the payment_link.paid webhook + reconciliation =="
  python3 - "$CASE_ID" "$PROVIDER_REF" "$API_URL" << 'PYEOF'
import sys, json, hmac, hashlib, urllib.request
case_id, provider_ref, api_url = sys.argv[1], sys.argv[2], sys.argv[3]
payload = {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {
    "id": provider_ref, "reference_id": f"{case_id}:X:v1", "amount": 1500000}}}}
raw = json.dumps(payload).encode()
sig = hmac.new(b"simulator_dev_secret", raw, hashlib.sha256).hexdigest()
req = urllib.request.Request(f"{api_url}/webhooks/razorpay", data=raw, method="POST",
    headers={"Content-Type": "application/json", "x-razorpay-event-id": f"evt_demo_{provider_ref}",
             "x-razorpay-signature": sig})
print(urllib.request.urlopen(req).read().decode())
PYEOF
  echo
  echo "== recovery journey =="
  curl -s "$API_URL/cases/$CASE_ID/journey" | python3 -m json.tool
else
  echo "action was NO_ACTION or blocked this run; nothing to reconcile (this is a valid outcome, not an error)"
fi

echo
echo "== benchmark report (already generated at image build time via run_demo.py): =="
echo "   outputs/results.json / outputs/dashboard.html inside the api container"
echo "== API docs: $API_URL/docs =="
echo "Demo case complete: $CASE_ID"
