#!/usr/bin/env bash
# Fresh DB -> start API serving the built dashboard -> seed over HTTP -> browser E2E -> stop.
# Usage: scripts/run_e2e.sh [--screenshots docs/screenshots]
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${CRRO_E2E_PORT:-8100}"; DB="$(mktemp -d)/e2e.db"
CRRO_DEMO_MODE=1 CRRO_DATABASE_URL="sqlite:///$DB" CRRO_LOG_LEVEL=WARNING \
  python3 scripts/serve.py --port "$PORT" --dashboard crro-dashboard/dist > /tmp/crro_e2e_server.log 2>&1 &
PID=$!; trap 'kill $PID 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do curl -sf "http://127.0.0.1:$PORT/health/ready" >/dev/null && break; sleep 0.5; done
python3 scripts/seed_demo.py --api "http://127.0.0.1:$PORT" > /tmp/crro_e2e_seed.log
SHOTS=()
if [ "${1:-}" = "--screenshots" ]; then SHOTS=(--screenshots "$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"); fi
(cd crro-dashboard && CRRO_E2E_BASE="http://127.0.0.1:$PORT" node tests/e2e.mjs "${SHOTS[@]}")
