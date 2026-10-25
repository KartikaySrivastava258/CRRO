# Financial semantics

All persisted amounts are **integer paise** (`BIGINT`, `CHECK > 0` where applicable). Conversion
from rupees happens once at the API boundary with `Decimal` and ROUND_HALF_UP (`crro/money.py`).
Only INR is accepted. Rupee strings appear only in presentation.

## Definitions

| Term | Definition | Where |
|---|---|---|
| Invoice amount `A` | Amount of the failed payment | `payment_cases.amount_paise` |
| Action cost `c_a` | Modelled operating cost of executing action *a* (registry), incurred when the provider accepts the action, regardless of outcome | `action_attempts.action_cost_paise` |
| Incentive discount `D_a` | Discount conceded **only if the customer pays** after an incentive action. Only offers that need no approval are auto-selected (2%, capped at ₹300) | `crro/economics.py` |
| Collect amount | `A − D_a`: what the provider is asked to collect | `action_attempts.collect_amount_paise` |
| **Gross recovered** | Invoice amount `A` of cases with a reconciled recovery | `recoveries.gross_recovered_paise` |
| **Cash collected** | Amount actually paid, `A − D_a` | `recoveries.cash_collected_paise` |
| **Incentive cost** | `gross − cash`, enforced by a CHECK constraint | `recoveries.incentive_cost_paise` |
| **Net recovered value** | `cash collected − action costs of provider-accepted attempts` | Overview |
| **Expected incremental value** | `(p_a − p_0)·A − p_a·D_a − c_a`: model expectation of net value relative to doing nothing | `case_decisions.expected_value_paise` |
| **Observed (confirmed) recovery** | A recovery row created from a signature-verified provider event whose amount, currency and merchant matched | `recoveries` |
| **Simulated recovery** | Outcome drawn from the synthetic oracle in offline evaluation; never a ledger recovery | `crro/evaluate.py` |
| Provider mode | `SIMULATOR`, `TEST_MODE` or `LIVE`, stored on every attempt and recovery, shown as a tag | `provider_mode` columns |
| Pending / unknown value | Never counted as recovered; appears only in "open exposure" | Overview |

## Accounting invariants (enforced and tested)

1. A case has at most one recovery (`UNIQUE(case_id)`); a provider payment credits at most one case.
2. `cash_collected + incentive_cost = gross_recovered` (CHECK).
3. A recovery requires: verified signature, first-seen event id, matching provider reference,
   `amount_paid == collect_amount`, `currency == INR`, matching merchant. Otherwise `NEEDS_REVIEW`.
4. `RECOVERED` is terminal; stale or later failure events cannot downgrade it.
5. A second, different payment on a recovered case raises a review flag (possible refund) and is
   **not** added to recovered value.
6. Expected values are labelled MODEL ESTIMATE and are never summed with confirmed values.

## Offline (synthetic) metrics

For each policy on the **same** held-out synthetic cases, budgets and random draws:
`gross = Σ recovered·A`, `incentive = Σ recovered·D_a`, `action cost = Σ_executed c_a`,
`net = gross − incentive − action cost`. **Incremental** = policy minus the no-action control on the
same cases. Two variants are reported:

* **realised** — one Monte-Carlo draw per case using common random numbers;
* **expected** — `Σ p_true·(A − D_a) − c_a` using the simulator's true probabilities (no sampling
  noise; used as the headline because it isolates the decision rule from draw luck).

Both are synthetic-simulation values in simulated rupees, not merchant revenue.

## Budget semantics

Contact budget counts customer-contacting actions accepted for dispatch per UTC day. The incentive
budget caps **worst-case exposure** `Σ D_a` committed when offers are sent, not the expected cost.
Reservations are atomic and released if the provider call fails.

## Rounding tests

`TestMoney`: ₹0.01 → 1 paisa; `0.1 + 0.2` float → 30 paise; half-up at the third decimal;
₹99,999,999.99 boundary; rejection of 0, negative, NaN, ∞, booleans and above-maximum values;
percent-of-paise rounding; Indian digit grouping.
