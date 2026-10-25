#!/usr/bin/env bash
# One-command local demo (no Docker, no credentials): train model, build dashboard,
# start API on :8000 with SQLite + simulator, seed synthetic data.
set -euo pipefail
cd "$(dirname "$0")"
export CRRO_DEMO_MODE=1 CRRO_DATABASE_URL="${CRRO_DATABASE_URL:-sqlite:///./crro_demo.db}"
[ -f outputs/model/model_card.json ] || python3 scripts/train_model.py
[ -f crro-dashboard/dist/index.html ] || (cd crro-dashboard && npm install && npm run build)
python3 scripts/serve.py --port 8000 --dashboard crro-dashboard/dist & PID=$!
trap 'kill $PID' EXIT
for _ in $(seq 1 60); do curl -sf http://127.0.0.1:8000/health/ready >/dev/null && break; sleep 0.5; done
python3 scripts/seed_demo.py --api http://127.0.0.1:8000
echo "Dashboard: http://127.0.0.1:8000  (click 'Use demo operator')   Ctrl+C to stop"
wait $PID
