# Decision science

## Problem

For a failed payment with amount `A`, choose one action `a` (or none) from a fixed registry to
maximise **expected incremental net value relative to doing nothing**, subject to portfolio
budgets and deterministic guardrails:

```
v(a) = (p_a − p_0)·A − p_a·D_a − c_a
```

`p_a = P(recover | x, a)`, `p_0 = P(recover | x, NO_ACTION)`, `D_a` the incentive conceded on
payment, `c_a` the action cost (docs/FINANCIAL_SEMANTICS.md). Ranking by raw `p_a` ignores that
many customers pay anyway; the benchmark shows that baseline loses ₹3.19 L [2.59, 3.80] per
10,000 cases to the uplift-based policy under identical budgets.

`NO_ACTION` is always available. It is chosen when no feasible action has `v(a) > 0`.

## Data and identification (synthetic)

`crro/data_generator.py` simulates failed payments with **randomised action assignment**, so
`E[Y | x, a]` is identified from the data without adjustment; this would *not* hold for
observational logs from a real retry policy. Outcomes come from a hidden oracle
(`crro/oracle.py`) with latent customer traits, observed noisy proxies, action × segment
interactions and a mild covariate/effect shift in the test distribution. The oracle is never a
model input; it is used only to realise simulated outcomes and simulator-only diagnostics.

Features: amount, attempt count, minutes since failure, two behavioural rates, time of day,
payment method, failure reason, merchant category. Hidden columns (`_latent_trait`,
`_true_p_recovery`), `assigned_action`, `outcome_recovered` and ids are excluded and a test asserts
predictions are invariant to permuting them.

## Models

* **Pooled S-learner (default, `PooledUpliftModel`)** — one HistGradientBoosting classifier on
  features + action as a categorical feature; `p_a` is obtained by setting the action.
* **T-learner (`UpliftModel`, original)** — one calibrated GBM per action.

### Model selection

On development seeds 1–2 (2 × 4,000 test cases) the pooled model gave higher incremental
expected net value at every training size (6k/20k/40k rows) and fit ~20× faster; the T-learner
splits data seven ways. Reported results use **disjoint seeds 101–110**, where the T-learner
reached ₹44.40 L [43.29, 45.52] vs ₹48.47 L [47.69, 49.25] for the pooled model.

Calibration (control action, 10 seeds): Brier 0.127, ECE 0.023 in-distribution; 0.119 / 0.029 on
the shifted test. No probability recalibration is applied to the pooled model; uncertainty is not
estimated (point predictions only).

## Allocation

`crro/optimizer.py` solves the 0/1 assignment exactly as a MILP (HiGHS via SciPy; CP-SAT if
OR-Tools is installed) with one-action-per-case, contact budget, incentive-exposure budget,
per-action caps and per-customer contact caps. Only `OPTIMAL` is reported as optimal; time limits
yield `FEASIBLE_TIME_LIMIT`; failures fall back to greedy with an explicit label. Greedy sorts by
value with deterministic tie-breaking. Every untreated case gets a reason: `NO_POSITIVE_EXPECTED_VALUE`
or `BLOCKED_BY:<constraints>` (the constraint(s) its best candidate would violate given the final
allocation; at a proven optimum one always exists). Correctness is checked against brute force on
40 random instances; at 500–20,000 cases exact was OPTIMAL and 5.1–7.1% above greedy.

The live path (one event at a time) cannot solve a portfolio problem per request. It applies the
same value function and guardrails per case and enforces budgets through atomic reservations, a
greedy-in-arrival-order approximation of the batch optimum.

## Evaluation protocol

Per seed: fresh train/test draws; model fit on train only; 3% customer opt-outs → identical
feasible sets; identical budgets; common random numbers. Policies: no-action control, fixed
retry, rule-based, historical best action, raw-probability (budgeted), uplift-greedy (budgeted),
CRRO exact (budgeted), uplift unconstrained (diagnostic), oracle constrained and unconstrained
(simulator-only ceilings). Metrics: incremental expected and realised net, gross, costs,
incentives, actions, contacts, recovery rate, feasibility violations; Student-t 95% intervals over
10 seeds and paired differences.

**What common random numbers do and do not prove.** They make paired comparisons low-variance by
giving each case the same uniform draw under every policy. They do not make the simulator
realistic; all differences are differences between policies *inside this simulator*.

**Metrics with stated definitions.**
* *Qini coefficient* (`qini_rct`): standard Qini on randomised held-out rows using **observed**
  outcomes only; rows ranked by estimated uplift; area between the Qini curve and the straight
  line to its endpoint, divided by N (units: recovered cases). Computable from a real RCT.
* *Oracle uplift@k*: mean **true** uplift of the model's chosen action in the top-k by estimated
  uplift. Simulator-only.
* *Policy regret* (`causal_metrics.policy_regret`): per-case true-uplift shortfall × amount,
  ignoring costs. Simulator-only.

## Results (synthetic; see docs/BENCHMARKS.md)

CRRO exact: incremental expected net ₹48.47 L [47.69, 49.25] per 10,000 cases; 91.5% [90.4, 92.6]
of the budget-constrained oracle; ahead of every deployable baseline with confidence intervals
excluding zero. Value saturates with contact budget (diminishing returns beyond ~2,000 contacts per
10,000 cases) and degrades gracefully as action costs rise (−8% at 20× costs).

## Limitations

* Everything is synthetic; the oracle's structure (e.g. strong incentive effects for low-reliability
  customers) drives which actions win. Real effects are unknown.
* Real recovery logs are not randomised; a deployment would need an exploration policy or
  off-policy evaluation with known propensities before any causal claim.
* No uncertainty quantification, no feature attribution, no fairness constraint (segments reported).
* Live decisions are per-case; only the batch simulation solves the portfolio exactly.
* Behavioural features may proxy for socio-economic attributes in real data.
