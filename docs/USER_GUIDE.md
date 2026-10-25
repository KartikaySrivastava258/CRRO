# User guide (operator console)

Start the local demo (`./demo.sh`, or see README Quick start), open http://127.0.0.1:8000 and
choose **Use demo operator** (or **Use read-only viewer**). Tokens live in this browser tab only.

The top bar always shows **SYNTHETIC** (all data is generated) and the provider mode
(**SIMULATED** for the local simulator). Tags on numbers:

* **RECONCILED** — matched to a signature-verified provider event with validated amount and currency.
* **SIMULATED / TEST MODE / LIVE PROVIDER** — who confirmed it.
* **MODEL ESTIMATE** — an expectation, not an outcome.

## Overview
Open exposure, confirmed recovered (gross and cash), net of incentives and action costs, expected
incremental value of open decisions, and the approval backlog. **Needs attention** links to unknown
provider outcomes, reconciliation mismatches, provider errors, unmatched events and rejected webhooks.
The funnel counts cases from failure to provider-confirmed recovery. Capacity meters show today's
contact and incentive budgets.

## Cases → Case detail
Filter by status or search an exact id (case, payment, customer or provider reference). The detail
page shows the decision (P(recover) with and without each action, uplift, costs, expected value,
and every rejection reason), imputed inputs, approval, provider attempt (idempotency key,
reference, status), reconciled recovery and the full timeline. With the simulator you can send a
signed payment/expiry webhook through the real webhook endpoint, and resolve an unknown outcome.

## Approvals
Pending high-value actions with amount, threshold, reason and estimate. Approve/Reject open a
confirmation; rejection needs a reason. The server enforces roles, merchant scope, expiry and
compare-and-set; repeating a decision is a no-op, conflicting decisions return 409.

## Optimizer
Runs every policy on the same synthetic cases, budgets and random draws with the live model. Shows
the comparison table, exact-vs-greedy objective/status/runtime, binding constraints, why cases
stayed at NO_ACTION, and a contact-budget sweep. Export CSV/JSON. Single runs are one seed — use the
multi-seed benchmark (Overview → Decision evidence, or docs/BENCHMARKS.md) for intervals.

## Explainability
Pick a decided case: the expected-value decomposition, guardrails, versions, and every alternative
with reasons. The model card shows training data, calibration and stated limitations.

## Audit
Filter by case, event type, correlation id and time; follow a correlation id from failure to
reconciliation; export JSON. Unmatched provider events are listed at the bottom.

## Troubleshooting
* *Can't reach the API* — check `/health/ready`; the page offers Retry and keeps stale data labelled.
* *401* — token expired or invalid; you are returned to sign-in.
* *OUTCOME UNKNOWN* — open the case and click **Resolve unknown outcome**.
* *NEEDS REVIEW* — the provider reported an amount/currency/merchant mismatch; investigate before refunding or resolving.
