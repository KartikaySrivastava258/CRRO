# Benchmarks

Every number here was produced by a script in `scripts/benchmarks/` and is stored raw under
`docs/benchmarks/results/` with environment metadata (git commit, Python/library versions, CPU).
Policy results are **synthetic simulations in simulated INR — not merchant revenue.**

**Environment:** Linux x86-64, 1 vCPU (Intel Xeon @ 2.80 GHz), 4 GB RAM; Python 3.12.3, NumPy
2.4.4, pandas 3.0.2, scikit-learn 1.8.0, SciPy 1.17.1 (HiGHS); OR-Tools not installed; SQLite 3.45.

## Summary table

| Experiment | Workload | Metric | Baseline | CRRO | Difference | Repetitions / CI | Status |
|---|---|---|---:|---:|---:|---|---|
| Policy economics | 10 seeds × 10,000 held-out shifted synthetic cases, identical budgets | Incremental expected net vs no-action | Raw-probability: ₹45.28 L | **₹48.47 L** | **+₹3.19 L** [2.59, 3.80] | 10 seeds, paired, Student-t 95% | Measured |
| Policy economics | same | vs rule-based heuristic | ₹40.42 L | ₹48.47 L | +₹8.05 L [7.37, 8.73] | same | Measured |
| Policy economics | same | Share of budget-constrained oracle (simulator ceiling) | — | **91.5 %** [90.4, 92.6] | — | same | Measured |
| Recovery targeting | same, RCT held-out rows | Qini coefficient (observed outcomes), per action | random = 0 | 5.3 – 19.6 (all CIs > 0) | — | 10 seeds | Measured |
| Optimization | Fixed cases/actions/budgets, 500–20,000 cases | Exact (HiGHS) vs greedy objective | Greedy | Exact, status OPTIMAL at every size | +5.1 % to +7.1 % | 3 repeats/size, deterministic | Measured |
| Webhook handling | 200 cases × 11 deliveries (8 concurrent duplicates, logical duplicate, stale + newer expiry) | Cases with ≠ 1 recovery / double-counted amount | old handler: not measured | 0 / 0 of 200 | — | 2,200 deliveries | Measured |
| API performance | 600 requests, 8 concurrent clients, 50 warm-up, local stdlib server + SQLite | `POST /v1/events/payment-failed` p50 / p95 / p99 | 443.6 / 1,190.0 / 2,007.9 ms (before batching) | **88.7 / 609.9 / 1,027.1 ms** | p50 −80 %; 14.6 → 48.4 req/s | single run per scenario | Measured |
| API performance (FastAPI + PostgreSQL) | — | p50/p95/p99 | — | — | — | — | **Not measured** |

## 1. Policy benchmark (`scripts/benchmarks/policy_benchmark.py`)

**Protocol.** For each seed s ∈ {101,…,110}: train = 40,000 cases (seed 1000s+1), held-out test =
10,000 cases with distribution shift (seed 1000s+3). Model refit on train only for every seed.
3% of test customers opted out (same feasible sets for all policies). Common random numbers
(seed 1000s+7). Budgets per seed: 1,000 contacts, ₹50,000 incentive exposure, ALTERNATIVE_PAYMENT_METHOD
cap 400, one contact per customer. **Seeds 101–110 are disjoint from development seeds 1–2**
used to choose the pooled model. Headline metric: incremental *expected* net value (true
simulator probabilities, no draw noise); realised single-draw values are reported alongside.

| Policy | Incremental expected net (₹) mean [95% CI] | Realised draw (₹) | Actions | Contacts |
|---|---:|---:|---:|---:|
| FIXED_RETRY | 22,46,137 [22,22,485 – 22,69,789] | 22,41,372 | 6,654 | 0 |
| HISTORICAL_BEST_ACTION | 32,52,476 [31,86,248 – 33,18,705] | 32,91,963 | 7,107 | 1,000 |
| RULE_BASED | 40,41,909 [40,03,226 – 40,80,593] | 40,61,994 | 6,314 | 1,000 |
| RAW_PROBABILITY (greedy on p·A) | 45,27,704 [44,22,825 – 46,32,584] | 45,16,641 | 7,166 | 1,000 |
| UPLIFT_GREEDY (same model, heuristic allocator) | 46,20,498 [45,11,528 – 47,29,467] | 46,44,977 | 7,078 | 1,000 |
| **CRRO_EXACT** (uplift + exact MILP) | **48,47,085 [47,68,878 – 49,25,291]** | 48,54,663 | 7,430 | 1,000 |
| ORACLE_CONSTRAINED (simulator-only ceiling) | 52,99,110 [52,44,097 – 53,54,124] | 53,49,301 | 7,226 | 1,000 |
| UPLIFT_UNCONSTRAINED (diagnostic, ignores budgets) | 71,02,483 | 69,96,289 | 9,885 | 8,382 |
| ORACLE_UNCONSTRAINED (simulator-only, ignores budgets) | 79,16,143 | 78,51,750 | 9,898 | 7,690 |

Paired differences CRRO_EXACT − baseline (mean [95% CI], ₹): vs FIXED_RETRY +26,00,948
[25,26,255 – 26,75,640]; vs HISTORICAL +15,94,608 [15,25,393 – 16,63,824]; vs RULE_BASED +8,05,175
[7,37,151 – 8,73,200]; vs RAW_PROBABILITY +3,19,380 [2,58,935 – 3,79,825]; vs UPLIFT_GREEDY
+2,26,587 [1,67,483 – 2,85,691]; vs ORACLE_CONSTRAINED −4,52,026 [−5,09,960 – −3,94,091].

**Constraint violations:** 0 for every constrained policy on every seed (independent post-hoc check).

**Model comparison (same seeds/budgets):** CRRO_EXACT with the original T-learner reached
₹44,40,430 [43,29,156 – 45,51,704] vs ₹48,47,085 with the pooled S-learner.

**Model quality (control action, mean over seeds):** Brier 0.127 / ECE 0.023 in-distribution;
Brier 0.119 / ECE 0.029 on the shifted test. Qini coefficients from observed RCT outcomes (units:
recovered cases): RETRY_NOW 19.6 [15.2, 24.1], SMALL_INCENTIVE 19.0 [14.7, 23.3],
ALTERNATIVE_PAYMENT_METHOD 16.6 [14.3, 18.9], RETRY_LATER 12.0 [9.2, 14.8], PAYMENT_LINK 6.0
[1.5, 10.4], CUSTOMER_NUDGE 5.3 [0.2, 10.4]. Simulator-only: true uplift of the model's chosen
action is 0.245 in the top 20% ranked by estimated uplift vs 0.154 overall.

**Sensitivity (CRRO_EXACT, mean over seeds, incremental expected net):**

| Contact budget | 250 | 500 | 1,000 | 2,000 | 4,000 |
|---|---:|---:|---:|---:|---:|
| ₹ | 42,22,033 | 45,43,380 | 48,47,085 | 50,43,475 | 50,85,065 |

Diminishing returns: the 1,000 → 2,000 step adds ₹1.96 L; the 2,000 → 4,000 step adds ₹0.42 L.

| Incentive budget factor | 0× | 0.5× | 1× | 2× |   | Action-cost multiplier | 1× | 5× | 20× |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| ₹ | 45,58,038 | 47,19,994 | 48,47,085 | 50,18,887 | | ₹ | 48,47,085 | 47,63,678 | 44,50,906 |

**Segments (CRRO_EXACT, mean cases per seed, incremental realised net vs control):** amount
<₹1k: 756 cases, ₹44,302; ₹1k–5k: 5,599, ₹12,86,743; ₹5k–20k: 3,360, ₹28,48,916; ≥₹20k: 286,
₹6,74,701. By failure reason the largest gains are insufficient_funds (₹16.1 L) and
network_error (₹11.5 L); risk_block is lowest (₹1.7 L). Raw: `policy_seed_*.json`, `policy_benchmark_summary.json`.

## 2. Optimizer scaling (`optimizer_scaling.py` → `optimizer_scaling.json`)

| Cases | Positive candidates | Exact (HiGHS) median | Greedy median | Exact objective gain over greedy | Status | Deterministic |
|---:|---:|---:|---:|---:|---|---|
| 500 | 2,482 | 130 ms | 11 ms | +7.10 % | OPTIMAL | yes |
| 2,000 | 10,011 | 964 ms | 35 ms | +6.50 % | OPTIMAL | yes |
| 5,000 | 25,125 | 2,507 ms | 84 ms | +5.07 % | OPTIMAL | yes |
| 10,000 | 50,310 | 6,021 ms | 177 ms | +5.96 % | OPTIMAL | yes |
| 20,000 | 100,287 | 11,639 ms | 361 ms | +5.51 % | OPTIMAL | yes |

Objective = model-estimated expected incremental value; 3 repeats per size; 0 feasibility
violations. Trade-off: greedy is ~30× faster and loses 5–7% of objective; exact remains usable
for batch allocation at these sizes on one vCPU. In the policy benchmark the realised advantage
of exact over greedy (same model) was +₹2.27 L [1.67, 2.86] per 10,000 cases.

## 3. Webhook trials (`webhook_trials.py` → `webhook_trials.json`)

200 cases; each received 8 concurrent identical `payment_link.paid` deliveries, a second event
id for the same payment, a stale expiry and a newer expiry (2,200 deliveries). Results: 200
processed + 1,400 `duplicate_ignored` among concurrent deliveries; 800 distinct events recorded;
**0 cases with recovery count ≠ 1, 0 double-counted amounts, 0 cases downgraded from RECOVERED**.
Ingest latency under 8-way concurrency: p50 5.0 ms, p95 11.1 ms, p99 13.3 ms (SQLite WAL).
"Zero" applies only to this workload and backend.

## 4. API latency (`api_latency.py` → `api_latency.json`, `api_latency_before_batching.json`)

Server: `scripts/serve.py` (stdlib ThreadingHTTPServer) + SQLite file, same host as the client,
1 vCPU. 50 warm-up + 600 measured requests per scenario at concurrency 8. Rate limits were relaxed
with `CRRO_RATE_LIMIT_SCALE` (an earlier run without it was **discarded**: 580/600 requests were
429s, so it measured the limiter). Every reported scenario returned 100% 2xx.

| Scenario | p50 | p95 | p99 | Throughput |
|---|---:|---:|---:|---:|
| GET /v1/overview | 12.6 ms | 19.8 ms | 24.6 ms | 607 req/s |
| GET /v1/cases?limit=25 | 12.8 ms | 19.3 ms | 23.2 ms | 604 req/s |
| POST payment-failed, idempotent replay | 16.2 ms | 27.1 ms | 36.5 ms | 423 req/s |
| POST payment-failed, new case (validate → decide → persist → dispatch) | 88.7 ms | 609.9 ms | 1,027.1 ms | 48.4 req/s |
| same, before batching | 443.6 ms | 1,190.0 ms | 2,007.9 ms | 14.6 req/s |

**Profiling-driven fix.** cProfile of the decision path showed ~85% of time in 7 separate model
calls per case (fixed pandas/sklearn overhead each). Scoring all actions in one batched call
(`PooledUpliftModel.predict_actions`; equality with per-action scoring is unit-tested) cut serial
in-process time from 65.3 ms to 24.5 ms per request. The remaining tail (p95 610 ms) is **not yet
profiled**; the working hypothesis is SQLite's single-writer lock plus the GIL on one vCPU under
eight concurrent writers. These numbers do not represent uvicorn or PostgreSQL.

## Not measured

FastAPI/uvicorn and PostgreSQL latency; behaviour under multiple worker processes; Razorpay Test
Mode round-trip times; real-world recovery outcomes (none exist); OR-Tools CP-SAT runtime.

## Reproduce

```bash
python3 scripts/benchmarks/policy_benchmark.py     # ~30 min on 1 vCPU; resumable (--fresh to recompute)
python3 scripts/train_model.py                     # needed by the next two
python3 scripts/benchmarks/optimizer_scaling.py
python3 scripts/benchmarks/webhook_trials.py
N=600 C=8 python3 scripts/benchmarks/api_latency.py
```
