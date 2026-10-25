# Testing

All tests are standard-library `unittest` classes, so they run with `python -m unittest` (no extra
dependencies) **and** with `pytest` (as in CI). Browser tests use Playwright + Chromium.

## Results recorded in the build environment

| Suite | Command | Result |
|---|---|---|
| All Python suites | `python3 -m unittest discover -s tests -t . -v` | **93 run, 92 passed, 0 failed, 1 skipped** (~40 s) |
| Browser E2E (fresh DB, seeded over HTTP) | `scripts/run_e2e.sh --screenshots docs/screenshots` | **16 passed, 0 failed, 0 skipped** |

The single skip is `tests/integration/test_fastapi_adapter.py`: FastAPI is not installed in the
build environment. It skips **only** when FastAPI is absent *and* `CRRO_REQUIRE_FASTAPI` is unset;
CI sets `CRRO_REQUIRE_FASTAPI=1` and additionally fails the job if any test reports SKIPPED.
A rendered copy of the actual test output is in `docs/screenshots/08-tests.png`.

## What each suite covers

**`tests/integration/test_ledger.py` (44)** — real database (fresh SQLite file per test; set
`CRRO_TEST_DATABASE_URL` to run on PostgreSQL, `CRRO_REQUIRE_POSTGRES=1` to fail if absent).
* Intake: same key/same payload replay; same key/different payload → 409; business-key dedup;
  invalid payloads (zero, negative, fractional paise, USD, unknown fields, naive timestamps);
  **12 concurrent duplicate submissions → one case**.
* Dispatch: one provider object on repeated and **8 concurrent** dispatches; **timeout after
  provider success** resolved without a second link; timeout before commit re-dispatched with the
  same key; **process restart** (new service + provider objects) creates no duplicate; provider
  error releases budget; key stability and ≤40 chars.
* Webhooks: valid paid → one recovery; signature over raw bytes (re-serialised JSON, wrong,
  missing and non-ASCII signatures rejected, nothing recorded); dedup poisoning regression; 5
  sequential and **16 concurrent duplicate deliveries → one recovery**; same payment under a new
  event id; second distinct payment → review flag, no double count; stale and newer failure
  events cannot downgrade `RECOVERED`; late payment after expiry; stale expiry ignored; amount,
  currency and merchant mismatch → `NEEDS_REVIEW`; unknown reference → UNMATCHED; malformed,
  oversized, missing event id; webhook arriving before the dispatch result; retry declined;
  incentive accounting (gross/cash/discount).
* Approvals: approve once (idempotent replay), conflicting decision 409, reject needs a reason,
  cross-merchant 404, **10 concurrent approvals → one attempt**, expired approval cannot execute.
* Budgets/audit: atomic reservation stops at the limit; policy validation; append-only audit with
  one correlation id per case; injected failure rolls back everything; overview separates
  confirmed from expected; keyset pagination is complete and duplicate-free.

**`tests/unit/test_decision_science.py` (29)** — money rounding/limits; economics formula and
"incentive only on recovery"; optimizer/evaluator price parity (regression R5); generator
reproducibility and dataset-unique ids; **leakage invariance** (permuting test outcomes, assigned
actions and oracle columns leaves predictions identical); uplift = p_a − p_0; unknown categories;
real non-zero control; **exact solver equals brute force on 40 random instances** and is ≥ greedy;
a knapsack case where greedy is provably suboptimal; zero budgets, empty and non-positive inputs;
deterministic ties; calibration/Qini edge cases (empty, one class, no control, ties, direction);
Student-t intervals; same-case pairing; feasibility of every constrained policy; live engine
guardrails (opt-out, cooldown, budgets, merchant allow-list, eligibility, approval threshold,
imputation recording, argmax of feasible value).

**`tests/integration/test_http_api.py` (12)** — real sockets against the stdlib transport: health
and metrics; 401/403/404 isolation; Idempotency-Replayed header and 409; full lifecycle with a
signed webhook and a correlated timeline; simulator endpoint routes through the real webhook path;
approval flow (deterministic policy); provider timeout then resolve (no duplicate); policy
validation and opt-out guardrail; optimizer run, CSV export and cross-merchant isolation; rate
limit, CORS allow/deny, request-id propagation, 405/404/422/400; 413; static path traversal.

**`tests/unit/test_legacy_regressions.py` (7)** — every assertion of the original `test_crro.py`,
ported to current APIs (both model variants for the budget test).

**`crro-dashboard/tests/e2e.mjs` (16 steps)** — sign-in; overview KPIs equal API values and carry
RECONCILED/SIMULATED/MODEL ESTIMATE tags; case detail shows candidates and opt-out reasons;
optimizer validation blocks bad input and a **rapid double-click starts exactly one run**; approval
through the confirmation dialog with a double-click → one dispatch; simulated signed webhook
reconciles the approved case and the timeline shows the chain; audit filter (checked against the
API) and JSON export; explainability; API failure → error state → retry; keyboard navigation;
read-only viewer cannot approve; **no horizontal overflow at 320/375/768/1024 px**; no uncaught
browser errors.

## Mutation checks (manual, recorded)

Each safety check was removed in turn and the suites re-run:

| Mutation | Caught? |
|---|---|
| Webhook dedup removed; amount check removed; currency check removed; stale-time check removed; merchant check removed; idempotency payload hash ignored; dispatch CAS claim removed; merchant scoping on approvals removed; invalid signatures recorded (old bug); duplicate payment not flagged | Yes (each) |
| `RECOVERED` guard removed | Initially no (state machine + timestamp check covered it); test added → yes |
| Approval compare-and-set removed | **No on SQLite** — `BEGIN IMMEDIATE` + status check serialise writers; relevant on PostgreSQL |
| Exact solver per-customer cap removed; greedy incentive budget removed; outcome leaked as a model feature | Yes |
| HiGHS 50% MIP gap tolerance | **No** — tiny instances are solved exactly by presolve; optimality at scale relies on the reported solver status |

## Not executed here

PostgreSQL suite, FastAPI adapter test, Docker build, GitHub Actions workflow, `npm install` from
the registry (the dashboard was built from locally available React 19.2.5 and esbuild 0.27.7).
