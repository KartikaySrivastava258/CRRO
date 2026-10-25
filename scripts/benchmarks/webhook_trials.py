#!/usr/bin/env python3
"""
Webhook idempotency/ordering trials against the real ledger (SQLite file DB,
real transactions, concurrent threads). For each of N cases:
  1) 8 concurrent deliveries of the same signed payment_link.paid event
  2) a second, distinct event id for the same payment (logical duplicate)
  3) a stale payment_link.expired (older timestamp) and a newer one
Counts: recoveries per case (must be 1), recovered-amount double counts, downgrades.
"""
import json, os, sys, tempfile, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from crro.ledger.db import Database  # noqa: E402
from crro.ledger.providers import SimulatedProvider  # noqa: E402
from crro.ledger.schema import migrate  # noqa: E402
from crro.ledger.service import LedgerService  # noqa: E402
from scripts.benchmarks._env import environment  # noqa: E402

N = int(os.environ.get("TRIALS", 200))
db = Database(f"sqlite:///{tempfile.mkdtemp()}/trials.db")
migrate(db)
prov = SimulatedProvider(db, "trial_secret")
svc = LedgerService(db, prov)
M = "merchant_trials"
dec = {"selected_action": "PAYMENT_LINK", "candidates": [], "expected_value_paise": 1, "model_version": "trial",
       "requires_contact": True, "action_cost_paise": 500}
svc.update_policy(M, {"daily_contact_budget": N + 10}, "bench")
deliveries, latencies, statuses = 0, [], {}
t_start = time.perf_counter()
for i in range(N):
    _, r, _ = svc.open_case(M, {"external_payment_id": f"tp{i}", "customer_ref": f"tc{i}", "amount_paise": 100_000 + i,
                                "payment_method": "upi", "failure_reason": "network_error"}, f"tk{i}")
    svc.record_decision(r["case_id"], dec)
    att = svc.get_case(M, r["case_id"])["attempt"]
    att = svc.dispatch(att["attempt_id"])
    t0 = int(time.time())
    raw, h = prov.build_webhook("payment_link.paid", att["provider_reference"], created_at=t0, payment_id=f"pay_{i}")

    def deliver(raw=raw, h=h):
        s = time.perf_counter()
        st, b = svc.ingest_webhook(raw, h)
        latencies.append((time.perf_counter() - s) * 1000)
        statuses[b["status"]] = statuses.get(b["status"], 0) + 1
    ts = [threading.Thread(target=deliver) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    raw2, h2 = prov.build_webhook("payment_link.paid", att["provider_reference"], created_at=t0, payment_id=f"pay_{i}")
    for args in ((raw2, h2), prov.build_webhook("payment_link.expired", att["provider_reference"], created_at=t0 - 60),
                 prov.build_webhook("payment_link.expired", att["provider_reference"], created_at=t0 + 60)):
        svc.ingest_webhook(*args)
    deliveries += 11
with db.transaction(write=False) as tx:
    per_case = tx.all("SELECT c.case_id, c.status, c.amount_paise, COUNT(r.recovery_id) AS n, "
                      "COALESCE(SUM(r.gross_recovered_paise),0) AS gross FROM payment_cases c "
                      "LEFT JOIN recoveries r ON r.case_id = c.case_id WHERE c.merchant_id = ? GROUP BY c.case_id", [M])
    events = tx.scalar("SELECT COUNT(*) FROM provider_events")
lat = sorted(latencies)
pct = lambda p: round(lat[min(len(lat) - 1, int(p / 100 * len(lat)))], 3)
out = {"environment": environment(), "workload": f"{N} cases x (8 concurrent identical deliveries + 1 logical duplicate "
       "with new event id + 1 stale expiry + 1 newer expiry)", "database": "SQLite (WAL) file, BEGIN IMMEDIATE",
       "total_deliveries": deliveries, "distinct_events_recorded": int(events), "status_counts_concurrent": statuses,
       "cases_with_recovery_count_ne_1": sum(1 for r in per_case if int(r["n"]) != 1),
       "cases_double_counted_amount": sum(1 for r in per_case if int(r["gross"]) != int(r["amount_paise"])),
       "cases_not_recovered_at_end": sum(1 for r in per_case if r["status"] != "RECOVERED"),
       "ingest_latency_ms_concurrent": {"p50": pct(50), "p95": pct(95), "p99": pct(99), "n": len(lat)},
       "wall_s": round(time.perf_counter() - t_start, 1)}
os.makedirs("docs/benchmarks/results", exist_ok=True)
json.dump(out, open("docs/benchmarks/results/webhook_trials.json", "w"), indent=1)
print(json.dumps({k: v for k, v in out.items() if k != "environment"}, indent=1))
