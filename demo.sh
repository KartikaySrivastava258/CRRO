set -euo pipefail
cd "$(dirname "$0")"

echo "== 1/6 starting PostgreSQL =="
service postgresql start >/dev/null 2>&1 || true
for i in $(seq 1 10); do
  pg_isready -q && break
  sleep 1
done

echo "== 2/6 applying migrations =="
alembic upgrade head

echo "== 3/6 running batch benchmark (offline pipeline, ~90s) =="
python3 run_demo.py
python3 gen_dashboard.py

echo "== 4/6 starting API =="
python3 -m uvicorn api:app --port 8000 > /tmp/crro_api.log 2>&1 &
API_PID=$!
trap "kill $API_PID 2>/dev/null || true" EXIT
for i in $(seq 1 15); do
  curl -s http://localhost:8000/health | grep -q '"status"' && break
  sleep 1
done
curl -s http://localhost:8000/health; echo

echo "== 5/6 simulating one live recovery case =="
CASE_ID="DEMO-$(date +%s)"
TRACE=$(curl -s -X POST "http://localhost:8000/events/payment-failed" \
  -H "Content-Type: application/json" \
  -d "{\"case_id\":\"$CASE_ID\",\"merchant_id\":\"merchant_demo\",\"customer_id\":\"cust_demo\",\"amount_inr\":15000,\"payment_method\":\"card\",\"failure_reason\":\"card_expired\",\"attempt_count\":1,\"minutes_since_failure\":10}")
echo "$TRACE" | python3 -m json.tool
PROVIDER_REF=$(echo "$TRACE" | python3 -c "import json,sys; print(json.load(sys.stdin)['decision'].get('provider_reference') or '')")

if [ -n "$PROVIDER_REF" ]; then
  echo "== 6/6 simulating the payment_link.paid webhook + reconciliation =="
  python3 - "$CASE_ID" "$PROVIDER_REF" << 'PYEOF'
import sys, json, hmac, hashlib, urllib.request
case_id, provider_ref = sys.argv[1], sys.argv[2]
payload = {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {
    "id": provider_ref, "reference_id": f"{case_id}:X:v1", "amount": 1500000}}}}
raw = json.dumps(payload).encode()
sig = hmac.new(b"simulator_dev_secret", raw, hashlib.sha256).hexdigest()
req = urllib.request.Request("http://localhost:8000/webhooks/razorpay", data=raw, method="POST",
    headers={"Content-Type": "application/json", "x-razorpay-event-id": f"evt_demo_{provider_ref}",
             "x-razorpay-signature": sig})
print(urllib.request.urlopen(req).read().decode())
PYEOF
  echo
  echo "== recovery journey =="
  curl -s "http://localhost:8000/cases/$CASE_ID/journey" | python3 -m json.tool
else
  echo "action was NO_ACTION or blocked this run; nothing to reconcile (this is a valid outcome, not an error)"
fi

echo
echo "== dashboard: outputs/dashboard.html =="
echo "== API docs: http://localhost:8000/docs =="
echo "Demo complete. API is still running (PID $API_PID) -- Ctrl+C to stop."
wait $API_PID
