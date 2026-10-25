# Implementation audit

Audit of the uploaded `CRRO-main` against its own README and the upgrade brief, followed by
the fixes made. "Reproduced (executed)" means the defect was triggered by running code in the
build environment; "Reproduced (inspection)" means it was established by reading the code, and
the fix carries a regression test that would fail on the old behaviour.

## Build environment constraints (read first)

The environment used for this upgrade had **no network access** and lacked FastAPI, SQLAlchemy,
Pydantic, Alembic, OR-Tools, pytest, PostgreSQL and Docker. It had Python 3.12 with NumPy,
pandas, scikit-learn and SciPy, SQLite 3.45, Node 22 (React 19, esbuild) and headless Chromium.
Consequences:

* The ledger, decision engine, HTTP application and dashboard were verified end to end on
  **SQLite** through the **standard-library HTTP transport** (`scripts/serve.py`).
* `api.py` (FastAPI transport), PostgreSQL, `Dockerfile`, `docker-compose.yml` and the CI
  workflow are written but **were not executed**. CI is configured to run the full suite on
  PostgreSQL and to require the FastAPI adapter test (no skips allowed).
* The exact optimizer ran on SciPy's HiGHS MILP; the OR-Tools CP-SAT path exists but was not run.

## Baseline (before changes)

| Check | Result |
|---|---|
| `python run_demo.py` | Core pipeline ran; the configured `exact` optimizer **silently fell back to greedy** (OR-Tools missing); crashed at the end writing parquet (pyarrow missing — environment). |
| Baseline policy result (single seed, old economics) | "CRRO" incremental net ₹90.4 L vs budget-constrained raw-probability baseline ₹92.7 L — the advertised method **lost** to a simpler baseline. |
| Existing tests | Could not be collected here (pytest/SQLAlchemy/FastAPI absent). By inspection, all webhook tests and two state tests are `skipif` Postgres is unreachable, and the webhook module additionally skips if `outputs/model.pkl` is absent — so in a default CI run they could silently skip. |

## Defect matrix

| # | Area | Observed behaviour | Reproduction | Root cause | Sev. | Fix | Regression test | Status |
|---|---|---|---|---|---|---|---|---|
| R1 | Decision science | `crro.experiments` failed on import | executed | imported non-existent `raw_probability_policy`, `uplift_only_policy` | High | Rewrote module on the shared protocol | `test_previously_broken_modules_now_run` | Fixed |
| R2 | Decision science | `budget_response_curve` crashed | executed | wrong `score_all_actions` signature; unknown kwarg `incentive_pct` | Med | Rebuilt on `crro.experiments` | same | Fixed |
| R3 | Metrics | `causal_metrics._case_truth` crashed | executed | passed a pandas row into a vectorised oracle | Med | Vectorised | same + `policy_regret` smoke | Fixed |
| R4 | Architecture | `app.main` (target of `make run`) could not import | executed (missing names) + inspection | missing `crro.baselines` names, missing `app/action_registry.py`, `pydantic_settings` not in requirements | High | Second app retired to `legacy/`; single HTTP app | `legacy/README.md` | Fixed |
| R5 | Money | Optimizer priced `SMALL_INCENTIVE` at ₹500 on ₹10,000 (5% offer, charged unconditionally); evaluator charged ₹200 (hard-coded 2%) | executed | three modules priced incentives independently; the 5% offer also requires approval | **Critical** | `crro/economics.py`: one pricing function; discount only on recovery; only auto-approvable offers | `test_optimizer_and_evaluator_price_incentives_identically` | Fixed |
| R6 | Optimizer | "exact" mode returned greedy results labelled by solver only | executed | silent `ImportError` fallback | Med | HiGHS exact backend; explicit status vocabulary; fallback labelled `greedy_fallback_after_*` | `test_exact_matches_brute_force…` | Fixed |
| R7 | Webhook security | An invalid-signature delivery stored its event id in the dedup ledger; the genuine event with that id was later ignored as a duplicate | inspection | dedup row written before signature acceptance | **Critical** | Reject before any ledger write; rejections stored separately without payload | `test_invalid_signature_cannot_poison_event_id` | Fixed |
| R8 | Reconciliation | Recovered amount taken from the webhook payload without validation; `seed_demo.py` sent ₹15,000 for every case | inspection | no amount/currency/merchant check | **Critical** | Amount must equal the attempt's collect amount; currency and merchant must match; else `NEEDS_REVIEW` | `test_amount_mismatch…`, `test_currency_mismatch…`, `test_wrong_merchant…` | Fixed |
| R9 | Idempotency | `payment.failed` had no idempotency check (docstring claimed one) | inspection | not implemented | High | Idempotency-Key + request hash + business-key uniqueness | `TestIntakeIdempotency` (5 tests, incl. 12-thread race) | Fixed |
| R10 | Idempotency | Provider idempotency lived in a per-process Python `set` | inspection | in-memory state | High | Durable attempt row with unique key; provider-side dedup/lookup; restart test | `test_process_restart…`, `test_timeout_after_provider_success…` | Fixed |
| R11 | Consistency | Provider called before decision persisted; decision, history and audit in separate transactions | inspection | no transaction boundaries | High | Outbox-style: intent committed before call, result after; state + audit in one transaction | `test_rollback_on_injected_failure…` | Fixed |
| R12 | Concurrency | Concurrent duplicate webhooks → check-then-insert race → 500 | inspection | no `ON CONFLICT` | Med | `INSERT … ON CONFLICT DO NOTHING` in the same transaction as effects | `test_concurrent_duplicate_deliveries_one_recovery` | Fixed |
| R13 | Concurrency | Approval endpoint could double-dispatch under concurrent approvals | inspection | no CAS / lock | High | Row lock + status check + CAS update | `test_concurrent_approvals_single_attempt` | Fixed (see note M2) |
| R14 | Provider | Razorpay "retry" created a ₹0 payment link | inspection | `retry_payment` reused link creation with amount 0 | Med | Returns `UNSUPPORTED`; case → `ACTION_FAILED` | `test_provider_error_fails_case…` (path) | Fixed |
| R15 | Money | Amounts persisted as float rupees | inspection | schema used `Float` | High | Integer paise throughout; `CHECK` constraints | `TestMoney` | Fixed |
| R16 | Double counting | Nothing prevented two recoveries for a case | inspection | no constraint | **Critical** | `recoveries.case_id UNIQUE`, `provider_payment_id UNIQUE`, `CHECK cash + incentive = gross` | `test_same_payment_different_event_ids…`, `test_second_distinct_payment_flags_review…`, webhook trials | Fixed |
| R17 | Data leakage | Every dataset used ids `R-000000…`, so train and test shared case ids | executed | id scheme | Med | Dataset-unique ids `S{seed}[X]-…` | `test_generator_is_reproducible_and_ids_unique…` | Fixed |
| R18 | Evaluation | Oracle ceiling ignored budgets; constrained policies compared to an unconstrained ceiling | inspection | design | Med | Added `ORACLE_CONSTRAINED` (same budgets) | `test_all_constrained_policies_are_feasible` | Fixed |
| R19 | Evaluation | CRRO evaluated as if approval-pending actions executed; multi-seed CI used one model and z=1.96 with n=3 | inspection | design | Med | Protocol retrains per seed, 10 disjoint seeds, Student-t CI | `test_mean_ci95_uses_t_distribution` | Fixed |
| R20 | Model | T-learner underperformed (each per-action model sees ~1/7 of data) | executed (dev seeds 1–2) | high-variance design | Med | Pooled S-learner default; T-learner kept for comparison | benchmark reports both | Improved |
| R21 | Overview metrics | "Incremental recovered value" summed *expected* values of `EXECUTED` decisions, which disappeared once reconciled | inspection | mislabelled metric | High | Ledger-derived overview; confirmed vs expected separated and tagged | `test_overview_separates_confirmed_from_expected`, E2E KPI check | Fixed |
| R22 | Security | CORS `*`; no authentication or merchant scoping on any endpoint | inspection | not implemented | High | Bearer tokens → (merchant, actor, role); all queries merchant-scoped; CORS allow-list; viewer role | `test_auth_roles_and_merchant_isolation` | Fixed (demo-grade, see SECURITY) |
| R23 | Migrations | Two Alembic trees, conflicting `webhook_events`; `create_all()` at startup | inspection | two apps | Med | One migration runner for SQLite + PostgreSQL; non-colliding table names; old trees in `legacy/` | migrations applied in every test | Fixed |
| R25 | Performance | New-case path p50 444 ms / 14.6 req/s at 8 concurrent clients | executed (benchmark + cProfile) | 7 separate model calls per case, each with fixed preprocessing overhead (~85% of time) | Med | Batched per-case scoring (`predict_actions`) | `test_batched_live_scoring_equals_per_action_scoring` | Fixed: p50 89 ms, 48 req/s; p95 tail not yet profiled |
| R26 | Benchmark validity | First latency run returned 580/600 HTTP 429 | executed | rate limiter active during load test | Med | `CRRO_RATE_LIMIT_SCALE` for benchmarks; scenarios flagged invalid unless 100% 2xx | `valid` field in results | Fixed; invalid run discarded |
| R24 | Dashboard | Mobile layout overflowed horizontally by 129–184 px | executed (E2E found it) | `1fr` grid track min-content | Low | `minmax(0, 1fr)` | E2E responsive steps | Fixed |

### Notes from mutation testing (see docs/TESTING.md)

* **M1** — Removing the `RECOVERED` guard in reconciliation initially survived because the state
  machine and the stale-timestamp check also prevented the downgrade; a test with a *newer*
  failure event was added and now kills the mutant.
* **M2** — Removing the approval compare-and-set survives on SQLite because `BEGIN IMMEDIATE`
  and the status check already serialise writers. The CAS matters on PostgreSQL; that path is
  exercised only by the (unexecuted) PostgreSQL CI job.
* **M3** — Setting a 50% MIP gap tolerance survives the brute-force optimality test because
  HiGHS presolve solves the tiny instances exactly. Optimality at scale relies on the solver's
  reported status, which is surfaced and never upgraded.

## Remaining work and known gaps (prioritised)

1. **Run CI** (PostgreSQL suite, FastAPI adapter test, Docker build). Not executed here.
2. Razorpay Test Mode adapter is unit-shaped but never exercised against Razorpay.
3. LLM analyst retired to `legacy/` (not wired into v1). Re-integration must remain advisory.
4. Dispatch is synchronous in the request; a worker + sweeper schedule would be needed for
   production (`RecoveryRuntime.resolve_stuck` exists but is not scheduled).
5. Rate limiting and metrics are per-process.
6. Model uncertainty is not estimated; no feature attribution is implemented.
7. `package-lock.json` must be regenerated with network access (`npm install`).
