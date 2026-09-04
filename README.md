# CRRO — Counterfactual Revenue Recovery Optimizer

**Razorpay AI Buildathon 2026 · Track 03 — AI Revenue Recovery**

> **Recover the revenue that would otherwise be lost — without blindly retrying everyone.**

CRRO is an AI-driven recovery decision system for failed payments. It detects revenue at risk, understands the recovery context, estimates the **incremental effect of each intervention**, allocates limited recovery capacity across a portfolio, enforces merchant policy, executes a bounded action, and closes the loop from provider event to measured recovery.

The key idea is simple:

**The best recovery action is not necessarily the action with the highest raw probability of success. It is the action that creates the most incremental revenue relative to doing nothing, after cost, constraints, and policy are taken into account.**

---

## Why this problem matters

A failed payment is not automatically a lost customer. It is a decision point.

A conventional recovery flow may ask:

> “Should we retry?”

CRRO asks the more useful questions:

> “Who should we intervene on?”  
> “Which intervention is most likely to change the outcome?”  
> “What would have happened if we had done nothing?”  
> “Which set of actions creates the most incremental revenue when contact and incentive capacity are limited?”  
> “Can we prove what happened afterwards?”

That turns recovery from a collection of retry rules into a **closed-loop revenue optimization problem**.

---

# The gap CRRO closes

Razorpay already has powerful payment infrastructure and recovery capabilities. CRRO is not trying to replace those systems.

It adds an optimization and decision layer above them:

```text
Payment infrastructure / recovery capability
                  +
        CRRO decision intelligence
                  ↓
   better intervention selection
                  ↓
     better portfolio allocation
                  ↓
      measurable incremental revenue
```

The gap is between:

**“This payment failed, so try a recovery action.”**

and:

**“Given this customer, this failure, this history, this merchant policy and the limited recovery budget, choose the intervention with the highest expected incremental value — and prove the outcome.”**

That is the problem CRRO is designed to solve.

---

# What CRRO does

## 1. Detects revenue at risk

A payment failure becomes a structured recovery case containing:

- customer
- merchant
- amount
- payment method
- failure reason
- attempt count
- time since failure
- customer recovery history

The system turns raw failure events into recovery opportunities.

## 2. Understands the case with an AI Recovery Analyst

The LLM acts as a reasoning and explanation layer.

It:

- diagnoses the likely recovery situation;
- uses retrieved customer history and policy context;
- proposes a small set of candidate actions;
- explains why those actions are worth considering.

The LLM **does not** decide what money movement is allowed.

Every proposed action is checked against the deterministic action registry and the case's eligibility rules before it reaches the model/optimizer.

## 3. Estimates counterfactual impact

For every eligible intervention, CRRO estimates:

```text
P(recovery | customer context, action)
```

and compares it with:

```text
P(recovery | customer context, NO_ACTION)
```

The resulting treatment effect is:

```text
uplift(i,a)
=
P(recovery | X_i, do(a))
-
P(recovery | X_i, do(NO_ACTION))
```

This is the core differentiator.

A 70% recovery probability is not impressive by itself if the customer had a 68% chance of recovering anyway.

A 45% probability can be more valuable if the baseline was only 15%.

## 4. Optimizes the portfolio

CRRO does not independently maximize each customer.

It solves a portfolio allocation problem with OR-Tools CP-SAT while respecting constraints such as:

- daily contact budget;
- incentive budget;
- action caps;
- per-customer action limits;
- cooldowns;
- maximum attempts;
- merchant-approved actions;
- approval thresholds.

`NO_ACTION` is a real option.

The optimizer can legitimately decide that doing nothing is the best decision for a case.

## 5. Enforces deterministic guardrails

The LLM cannot bypass:

```text
LLM proposal
   ↓
schema validation
   ↓
action registry
   ↓
customer state
   ↓
merchant policy
   ↓
optimizer / decision logic
   ↓
approval
   ↓
execution
```

There is intentionally no:

```text
LLM → payment provider
```

## 6. Executes through a provider abstraction

Execution is hidden behind `RecoveryExecutionProvider`.

Two adapters are included:

```text
RecoveryExecutionProvider
├── SimulatorAdapter
└── RazorpayTestAdapter
```

The simulator gives a deterministic local demo.

The Razorpay adapter is Test-Mode-only and supports Payment Links through the official Python SDK. The adapter refuses non-`rzp_test_...` credentials.

## 7. Closes the loop with webhooks

A recovery action is not considered complete just because an API call succeeded.

The system receives the provider event, verifies the signature, handles duplicate delivery, tolerates out-of-order delivery, reconciles the case, and updates the recovery state.

Example:

```text
payment.failed
      ↓
AI analysis
      ↓
counterfactual scoring
      ↓
portfolio / policy decision
      ↓
PAYMENT_LINK
      ↓
Razorpay Test Mode
      ↓
payment_link.paid
      ↓
reconciliation
      ↓
RECOVERED
```

## 8. Measures the business outcome

CRRO records the full decision trail:

- recommendation;
- model score;
- uplift;
- expected incremental value;
- policy decision;
- approval;
- execution;
- provider reference;
- webhook;
- recovered amount.

The headline business metric is:

**Incremental Recovered Revenue**

not simply “number of successful retries”.

---

# Supported recovery actions

The current action registry contains:

| Action | Purpose |
|---|---|
| `NO_ACTION` | Explicitly do nothing |
| `RETRY_NOW` | Retry immediately |
| `RETRY_LATER` | Retry after a cooldown |
| `PAYMENT_LINK` | Send an alternative payment path |
| `ALTERNATIVE_PAYMENT_METHOD` | Recover through another payment route |
| `CUSTOMER_NUDGE` | Customer-facing recovery nudge |
| `SMALL_INCENTIVE` | Approved small incentive where policy allows |

All actions are registry-controlled. The LLM cannot invent new actions or offers.

---

# Architecture

```text
                         ┌───────────────────────┐
                         │ Razorpay Test Mode    │
                         └───────────┬───────────┘
                                     │
                           payment.failed / paid
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │ Webhook / Event Layer │
                         │ signature + idempotency│
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │ Recovery Case Builder │
                         └───────────┬───────────┘
                                     │
                      ┌──────────────┴──────────────┐
                      │                             │
                      ▼                             ▼
             ┌────────────────┐          ┌──────────────────┐
             │ Evidence /     │          │ Customer State   │
             │ Retrieval      │          │ + History        │
             └───────┬────────┘          └────────┬─────────┘
                     │                            │
                     └─────────────┬──────────────┘
                                   ▼
                       ┌────────────────────────┐
                       │ AI Recovery Analyst    │
                       │ diagnosis + candidates │
                       └────────────┬───────────┘
                                    │
                                    ▼
                       ┌────────────────────────┐
                       │ Counterfactual Model   │
                       │ P(action) vs control   │
                       └────────────┬───────────┘
                                    │
                                    ▼
                       ┌────────────────────────┐
                       │ Portfolio Optimizer    │
                       │ OR-Tools CP-SAT         │
                       └────────────┬───────────┘
                                    │
                                    ▼
                       ┌────────────────────────┐
                       │ Policy / Guardrails    │
                       │ + Approval              │
                       └────────────┬───────────┘
                                    │
                                    ▼
                       ┌────────────────────────┐
                       │ Execution Provider     │
                       └────────────┬───────────┘
                                    │
                                    ▼
                         Razorpay / Simulator
                                    │
                                    ▼
                              Webhook event
                                    │
                                    ▼
                             Reconciliation
                                    │
                                    ▼
                           Measured recovery
```

---

# AI safety boundary

CRRO deliberately separates responsibilities:

| Layer | Responsibility |
|---|---|
| LLM | Diagnose, synthesize evidence, explain, propose candidates |
| Uplift model | Estimate action-conditioned recovery and incremental effect |
| Optimizer | Allocate scarce recovery capacity |
| Policy engine | Enforce merchant rules and authorization |
| Approval layer | Gate high-value actions |
| Provider adapter | Execute bounded actions |
| Webhook/reconciliation | Verify and record external outcomes |

This keeps the AI useful without giving an LLM direct control over money movement.

---

# Counterfactual decision example

Suppose a customer has a failed ₹15,000 payment.

A raw probability system might see:

| Action | Recovery probability |
|---|---:|
| Retry now | 52% |
| Retry later | 57% |
| Payment Link | 59% |

CRRO also asks what happens without intervention:

| Action | Recovery probability | Uplift |
|---|---:|---:|
| `NO_ACTION` | 32% | 0% |
| `RETRY_NOW` | 52% | +20% |
| `RETRY_LATER` | 57% | +25% |
| `PAYMENT_LINK` | 59% | +27% |

The optimizer then adds economic and operational constraints.

The result is not:

> “Pick the highest probability.”

It is:

> “Pick the action with the highest expected incremental value that the merchant can actually afford and is allowed to execute.”

---

# Evaluation

The project includes an offline benchmark pipeline and baseline comparisons.

The evaluation includes:

- a real `NO_ACTION` control;
- uplift / treatment-effect scoring;
- AUUC;
- Qini;
- uplift@k;
- policy-value analysis;
- multi-seed evaluation;
- budget-constrained baseline comparisons;
- exact OR-Tools optimization;
- execution reliability simulation;
- persistence and idempotency tests.

The generated benchmark artifact is in:

```text
outputs/results.json
```

and the generated HTML report is in:

```text
outputs/dashboard.html
```

These are benchmark artifacts, not live production metrics.

---

# Repository layout

```text
.
├── api.py
├── run_demo.py
├── demo.sh
├── requirements.txt
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── crro/
│   ├── action_registry.py
│   ├── baselines.py
│   ├── data_generator.py
│   ├── db.py
│   ├── db_models.py
│   ├── evaluate.py
│   ├── evidence.py
│   ├── executor.py
│   ├── feasibility.py
│   ├── llm_analyst.py
│   ├── model.py
│   ├── offers.py
│   ├── optimizer.py
│   ├── oracle.py
│   ├── policy.py
│   ├── state.py
│   ├── state_db.py
│   ├── workflow.py
│   └── providers/
│       ├── base.py
│       ├── factory.py
│       ├── simulator.py
│       └── razorpay_test.py
├── alembic/
│   └── versions/
├── tests/
├── outputs/
└── crro-dashboard/
    ├── src/
    ├── package.json
    └── Dockerfile
```

---

# Setup

## Requirements

For the native setup:

- Python 3.12+
- PostgreSQL 16+
- Node.js 20+
- npm

For the container setup:

- Docker
- Docker Compose

---

# Option A — fastest local setup

This path is the easiest way to see the system without any external API credentials.

## 1. Clone the repository

```bash
git clone https://github.com/KartikaySrivastava258/CRRO.git
cd CRRO
```

## 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

## 4. Start PostgreSQL

Make sure PostgreSQL is running and create a database named `crro`.

Example:

```bash
createdb crro
```

Set the connection string:

```bash
export DATABASE_URL=postgresql+psycopg2://crro:crro@localhost:5432/crro
```

## 5. Apply migrations

```bash
alembic upgrade head
```

## 6. Run the benchmark pipeline

```bash
python3 run_demo.py
python3 gen_dashboard.py
```

This creates the model and benchmark artifacts under `outputs/`.

## 7. Start the API

```bash
python3 -m uvicorn api:app --reload --port 8000
```

Open:

```text
http://localhost:8000/docs
```

## 8. Start the dashboard

In another terminal:

```bash
cd crro-dashboard
npm install
npm run dev
```

Open:

```text
http://localhost:5173
```

---

# Option B — one-command showcase

The repository includes:

```bash
./demo.sh
```

The script:

1. starts PostgreSQL when available;
2. applies migrations;
3. runs the batch benchmark;
4. generates the benchmark report;
5. starts the FastAPI backend;
6. sends a `payment.failed` recovery case;
7. runs the AI analyst;
8. scores counterfactual actions;
9. applies policy and execution;
10. simulates the recovery webhook;
11. reconciles the case;
12. prints the final recovery journey.

The default provider is the deterministic simulator, so the showcase does not require Razorpay credentials.

---

# Live LLM setup

CRRO can use a live LLM analyst.

Recommended configuration:

```bash
export LLM_PROVIDER=groq
export GROQ_API_KEY=YOUR_KEY
export GROQ_MODEL=openai/gpt-oss-120b
```

Alternative:

```bash
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=YOUR_KEY
export ANTHROPIC_MODEL=claude-sonnet-5
```

No credentials are stored in the repository.

For deterministic local testing:

```bash
export LLM_PROVIDER=mock
```

---

# Razorpay Test Mode setup

CRRO supports a real Razorpay Test Mode execution path.

Set:

```bash
export CRRO_PROVIDER=razorpay_test
export RAZORPAY_KEY_ID=rzp_test_...
export RAZORPAY_KEY_SECRET=...
export RAZORPAY_WEBHOOK_SECRET=...
```

The adapter refuses non-Test-Mode-looking key IDs.

Create a Test Mode webhook endpoint in the Razorpay Dashboard and point it at:

```text
POST /webhooks/razorpay
```

Enable the payment events needed for your demonstration.

Razorpay supports separate Test Mode webhooks and recommends signature verification, idempotent handling, and tolerance for duplicate/out-of-order webhook delivery. citeturn497043search2turn497043search1turn497043search3

For Payment Link recovery, the adapter uses Razorpay's Payment Links API. Razorpay recommends Test Mode for testing Payment Links. citeturn497043search6

### Important

Use **Test Mode only** for the buildathon demonstration.

Do not place Razorpay secrets in Git.

---

# Docker setup

The repository includes PostgreSQL, API, and dashboard services.

```bash
cp .env.example .env
docker compose up --build
```

Then open:

```text
API:       http://localhost:8000/docs
Dashboard: http://localhost:5173
```

The Docker setup runs the database migrations before starting the API.

---

# API highlights

### Health

```http
GET /health
```

### Cases

```http
GET /cases
GET /cases/{case_id}
```

### Recovery analysis

```http
GET /recovery/analyze?case_id=<id>
POST /recovery/score?case_id=<id>
GET /recovery/whatif?case_id=<id>
```

### Event-driven workflow

```http
POST /events/payment-failed
```

### Webhooks

```http
POST /webhooks/razorpay
```

### Full case journey

```http
GET /cases/{case_id}/journey
```

### Approvals

```http
GET /approvals/pending
POST /approvals/{decision_id}/approve
POST /approvals/{decision_id}/reject
```

### Dashboard data

```http
GET /dashboard/overview
GET /dashboard/optimizer
GET /cases/{case_id}/explainability
GET /recovery/audit
GET /recovery/results
```

Interactive API documentation is available at:

```text
http://localhost:8000/docs
```

---

# Webhook reliability

The webhook receiver:

- validates the signature using the raw request body;
- requires the `x-razorpay-event-id`;
- stores the event as an idempotency record;
- ignores duplicate event delivery;
- safely ignores unknown recovery cases;
- prevents late failure events from regressing a recovered case;
- persists the reconciliation event.

This mirrors important properties of real payment-event infrastructure, where webhook delivery is asynchronous and duplicates/out-of-order delivery must be expected. citeturn497043search1turn497043search3

---

# Tests

Run:

```bash
pytest tests/ -v
```

The suite covers:

- counterfactual / control realism;
- optimizer budget enforcement;
- positive-value filtering;
- action registry safety;
- execution idempotency;
- AI registry validation;
- PostgreSQL persistence when available;
- webhook signature validation;
- duplicate webhook handling;
- malformed events;
- unknown cases;
- out-of-order events;
- provider-level idempotency.

---

# A five-minute judge demo

The strongest demonstration is a single recovery case played from event to outcome.

## 1. Start with the business problem

Show:

```text
Revenue at risk
Failed payments
Available recovery capacity
```

Explain that the challenge is not simply retrying failed transactions.

## 2. Trigger a payment failure

Create a `payment.failed` recovery case.

## 3. Show the AI Recovery Analyst

Show:

```text
Diagnosis
Evidence
Candidate actions
```

Then point out that the AI can only propose registry-approved actions.

## 4. Show the counterfactual table

For the same customer, show:

```text
NO_ACTION
RETRY_NOW
RETRY_LATER
PAYMENT_LINK
...
```

with probability, uplift and expected incremental value.

## 5. Show the portfolio constraints

Display:

```text
Contact budget
Incentive budget
Action caps
Cooldown
Approval threshold
```

Then show why CRRO selected its action.

## 6. Explain the choice

Use the Explainability screen to answer:

> Why this customer?

> Why this action?

> Why not another action?

> Why not NO_ACTION?

## 7. Execute the bounded action

Use the simulator for a completely local run, or switch to Razorpay Test Mode for the live provider demonstration.

## 8. Complete the recovery

Trigger the payment-link payment in Test Mode.

## 9. Show the webhook

Display:

```text
payment_link.paid
→ signature verified
→ event accepted
→ reconciliation
```

## 10. End with the business result

Show:

```text
Recovered amount
Incremental recovered revenue
Policy decision
Audit trail
```

Then compare CRRO against the baseline policies.

The winning narrative is:

> **AI understands the case → causal modeling estimates incremental impact → optimization allocates scarce recovery capacity → deterministic guardrails keep money movement bounded → Razorpay executes → webhooks prove the outcome → CRRO measures the revenue created.**

---

# Design principles

CRRO is built around five principles:

### 1. Optimize for incrementality, not raw conversion probability

Recovery that would have happened anyway is not the same as revenue recovered because of an intervention.

### 2. Treat `NO_ACTION` as a real action

Doing nothing can be the correct choice.

### 3. Optimize across the portfolio

Scarce customer-contact and incentive capacity should go where they create the most value.

### 4. Keep AI behind hard boundaries

The model can reason and explain. Deterministic systems authorize and execute.

### 5. Measure the closed loop

A recommendation is not the finish line. A verified provider outcome and measured recovery are.

---

# What is intentionally not claimed

The repository distinguishes between:

**Implemented and locally testable**

- live LLM integration path;
- Test-Mode Razorpay adapter;
- event-driven recovery workflow;
- provider abstraction;
- webhook verification;
- webhook idempotency;
- out-of-order protection;
- Postgres persistence;
- approval workflow;
- interactive dashboard;
- counterfactual optimization;
- benchmark evaluation.

and:

**Environment-dependent**

- successful execution against a real Razorpay Test Mode account requires the user's own Test Mode credentials and webhook endpoint;
- a live LLM requires the user's provider credential.

The code does not pretend that an external account was exercised when no credential was available.

---

# Security notes

Before moving beyond a controlled demo:

- use HTTPS for externally reachable webhooks;
- store credentials in a secret manager;
- restrict dashboard/API CORS to the deployed origin;
- add endpoint authentication and authorization;
- rate-limit public endpoints;
- review log redaction;
- keep Test Mode and Live Mode credentials completely separate.

---

# Buildathon positioning

CRRO targets the exact challenge described by Razorpay AI Buildathon 2026 Track 03: detect revenue at risk, choose the right intervention, execute a bounded recovery workflow, and show measured money recovered with compliant escalation, stopping rules, and an audit trail. citeturn497043search0

The project is intentionally presented as a **revenue decision and recovery system**, not as “just an LLM wrapper”.

Its strongest technical signal is the combination of:

**AI reasoning + counterfactual uplift modeling + constrained portfolio optimization + deterministic policy enforcement + provider execution + outcome reconciliation.**

---

# License

Add the license appropriate for your team before public release.
