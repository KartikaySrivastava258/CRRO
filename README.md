# CRRO — Counterfactual Revenue Recovery Optimizer

**Razorpay AI Buildathon 2026 · Track 03 — AI Revenue Recovery**

> **Recover the revenue that would otherwise be lost — without blindly retrying everyone.**

CRRO is an AI-driven recovery decision system for failed payments. It detects revenue at risk, understands recovery context, estimates the **incremental effect of each intervention**, allocates limited recovery capacity across a portfolio, enforces merchant policy, executes bounded actions, and closes the loop from provider event to measured recovery.

The core idea is simple:

**The best recovery action is not necessarily the action with the highest raw probability of success. It is the action that creates the most incremental revenue relative to doing nothing, after costs, constraints, and policy are taken into account.**

---

## The problem

A failed payment is not automatically a lost customer. It is a decision point.

A conventional recovery flow asks:

> Should we retry?

CRRO asks:

> Which customer should we intervene on? Which intervention is most likely to change the outcome? What would probably happen with no intervention? Which set of actions creates the most incremental revenue when recovery capacity is limited? Can we prove the outcome afterwards?

That turns recovery from a collection of retry rules into a **closed-loop revenue optimization problem**.

---

# The gap CRRO closes

Razorpay already provides payment infrastructure and recovery capabilities. CRRO is designed as a decision and optimization layer that can sit above those capabilities.

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

**“The payment failed, so start a recovery action.”**

and:

**“Given this customer, this failure, this history, this merchant policy and the limited recovery budget, choose the intervention with the highest expected incremental value — then verify and measure the result.”**

That is the problem CRRO is designed to solve.

---

# What CRRO does

## 1. Detect revenue at risk

A failed payment becomes a structured recovery case containing customer, merchant, amount, payment method, failure reason, attempt count, time since failure, and recovery history.

## 2. Understand the case with AI

The **AI Recovery Analyst** diagnoses the situation, uses grounded customer context, proposes a small set of candidate actions, and explains why they are worth considering.

The LLM is not the execution authority. Its output is schema-validated and filtered against the deterministic action registry before downstream scoring.

## 3. Estimate counterfactual impact

For each eligible action:

```text
P(recovery | customer context, action)
```

is compared with:

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

This matters because a customer with a 70% raw recovery probability is not necessarily a good intervention target when the customer already has a 68% chance of recovering on their own.

## 4. Optimize the portfolio

CRRO uses OR-Tools CP-SAT to allocate actions across the portfolio while respecting contact budget, incentive budget, action caps, per-customer limits, cooldowns, maximum attempts, merchant policy, and approval thresholds.

`NO_ACTION` is a real choice.

## 5. Enforce deterministic guardrails

The safety boundary is:

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
provider execution
```

There is intentionally no `LLM → payment provider` path.

## 6. Execute through a provider abstraction

```text
RecoveryExecutionProvider
├── SimulatorAdapter
└── RazorpayTestAdapter
```

The simulator gives a deterministic local demo. The Razorpay adapter provides a Test Mode Payment Link path behind the same interface.

## 7. Close the loop with events

```text
payment.failed
      ↓
AI analysis
      ↓
counterfactual scoring
      ↓
portfolio / policy decision
      ↓
bounded recovery action
      ↓
provider
      ↓
payment_link.paid
      ↓
reconciliation
      ↓
RECOVERED
```

The webhook receiver verifies signatures, deduplicates events, safely handles unknown cases, and prevents a late failure event from regressing an already recovered case.

## 8. Measure the business result

Every recovery decision can carry the recommendation, model score, uplift, expected incremental value, policy result, approval, execution result, provider reference, webhook event, and recovered amount.

The business KPI is **Incremental Recovered Revenue**, not just successful retries.

---

# Recovery actions

| Action | Purpose |
|---|---|
| `NO_ACTION` | Explicitly do nothing |
| `RETRY_NOW` | Retry immediately |
| `RETRY_LATER` | Retry after a cooldown |
| `PAYMENT_LINK` | Offer a separate payment path |
| `ALTERNATIVE_PAYMENT_METHOD` | Recover through another payment route |
| `CUSTOMER_NUDGE` | Customer-facing recovery nudge |
| `SMALL_INCENTIVE` | Approved incentive where policy allows |

The action vocabulary is closed. The LLM cannot create a new action or invent an offer.

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
                         │ Event + Webhook Layer │
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
              ┌───────────────┐           ┌─────────────────┐
              │ Evidence /    │           │ Customer State  │
              │ Retrieval     │           │ + History       │
              └───────┬───────┘           └────────┬────────┘
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
                       │ action vs control      │
                       └────────────┬───────────┘
                                    │
                                    ▼
                       ┌────────────────────────┐
                       │ Portfolio Optimizer    │
                       │ OR-Tools CP-SAT        │
                       └────────────┬───────────┘
                                    │
                                    ▼
                       ┌────────────────────────┐
                       │ Policy + Approval      │
                       │ deterministic gates    │
                       └────────────┬───────────┘
                                    │
                                    ▼
                       ┌────────────────────────┐
                       │ Execution Provider     │
                       └────────────┬───────────┘
                                    │
                                    ▼
                            Provider / Razorpay
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

| Layer | Responsibility |
|---|---|
| LLM | Diagnose, synthesize evidence, explain, propose candidates |
| Uplift model | Estimate action-conditioned recovery and incremental effect |
| Optimizer | Allocate scarce recovery capacity |
| Policy engine | Enforce merchant rules and authorization |
| Approval layer | Gate high-value actions |
| Provider adapter | Execute bounded actions |
| Webhook / reconciliation | Verify and record external outcomes |

---

# Evaluation

The repository includes an offline benchmark with a real `NO_ACTION` control, treatment-effect scoring, AUUC, Qini, uplift@k, policy-value analysis, multi-seed evaluation, budget-constrained baselines, exact CP-SAT optimization, execution-reliability simulation, PostgreSQL persistence, and idempotency tests.

Generated benchmark artifacts are kept in `outputs/`.

These benchmark metrics are synthetic evaluation results; they are not presented as real merchant uplift.

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
├── tests/
├── outputs/
└── crro-dashboard/
```

---

# Setup

## Native requirements

- Python 3.12+
- PostgreSQL 16+
- Node.js 20+
- npm

## 1. Clone

```bash
git clone https://github.com/KartikaySrivastava258/CRRO.git
cd CRRO
```

## 2. Create the Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Start PostgreSQL

Create a database named `crro` and set the connection string. For example:

```bash
export DATABASE_URL=postgresql+psycopg2://crro:crro@localhost:5432/crro
```

## 4. Run migrations

```bash
alembic upgrade head
```

## 5. Generate the model and benchmark artifacts

```bash
python3 run_demo.py
python3 gen_dashboard.py
```

## 6. Start the API

```bash
python3 -m uvicorn api:app --reload --port 8000
```

API docs:

```text
http://localhost:8000/docs
```

## 7. Start the dashboard

In a second terminal:

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

# One-command showcase

The fastest local walkthrough is:

```bash
./demo.sh
```

The script runs the benchmark, starts the API, submits a sample `payment.failed` case, runs the recovery workflow, uses the simulator provider, posts a signed `payment_link.paid` event, reconciles the case, and prints the persisted journey.

The default provider is the simulator, so no external API keys are required for this path.

---

# Docker

```bash
cp .env.example .env
docker compose up --build
```

Open:

```text
http://localhost:8000/docs
http://localhost:5173
```

The API container applies Alembic migrations before serving requests.

---

# Live LLM setup

Recommended Groq configuration:

```bash
export LLM_PROVIDER=groq
export GROQ_API_KEY=YOUR_KEY
export GROQ_MODEL=openai/gpt-oss-120b
```

Anthropic is also supported:

```bash
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=YOUR_KEY
export ANTHROPIC_MODEL=claude-sonnet-5
```

For deterministic development and tests:

```bash
export LLM_PROVIDER=mock
```

Credentials are never stored in Git.

---

# Razorpay Test Mode setup

Set:

```bash
export CRRO_PROVIDER=razorpay_test
export RAZORPAY_KEY_ID=rzp_test_...
export RAZORPAY_KEY_SECRET=...
export RAZORPAY_WEBHOOK_SECRET=...
```

Configure the corresponding Test Mode webhook in the Razorpay Dashboard and point it at:

```text
POST /webhooks/razorpay
```

Use Test Mode credentials only. The adapter refuses credentials that do not look like Test Mode keys.

---

# Key API endpoints

```text
GET  /health
GET  /cases
GET  /cases/{case_id}
POST /recovery/score
POST /recovery/approve
GET  /recovery/analyze
GET  /recovery/whatif
POST /events/payment-failed
GET  /cases/{case_id}/journey
POST /webhooks/razorpay
GET  /approvals/pending
POST /approvals/{decision_id}/approve
POST /approvals/{decision_id}/reject
GET  /dashboard/overview
GET  /dashboard/optimizer
GET  /cases/{case_id}/explainability
GET  /recovery/audit
GET  /recovery/results
```

---

# Five-minute judge demo

### 1. Show the money at risk

Start with the dashboard: failed payments, revenue at risk, recovery capacity, and baseline results.

### 2. Trigger one payment failure

Send a single recovery case through the event-driven endpoint.

### 3. Show the AI analyst

Display the diagnosis, grounded evidence, and candidate actions.

### 4. Show the counterfactual decision

Compare `NO_ACTION` with the intervention candidates using probability, uplift, and expected incremental value.

### 5. Show constraints

Display contact budget, incentive budget, action limits, cooldown and approval rules.

### 6. Explain the final choice

Use the Explainability screen to answer:

- Why this customer?
- Why this action?
- Why not another action?
- Why not `NO_ACTION`?

### 7. Execute

Use the simulator for a completely local demo or switch to Razorpay Test Mode for the provider demonstration.

### 8. Prove the outcome

Show the signed webhook, reconciliation and recovered case.

### 9. Finish with business value

Show recovered amount and incremental recovered revenue, then compare CRRO with the baseline policies.

The closing message is:

> **AI understands the case → causal modeling estimates incremental impact → optimization allocates scarce recovery capacity → deterministic guardrails keep money movement bounded → the provider executes → webhooks prove the outcome → CRRO measures the revenue created.**

---

# Buildathon fit

Razorpay's 2026 Buildathon Track 03 asks builders to detect revenue at risk, determine the right intervention, execute a bounded recovery workflow, and show measured money recovered with compliant escalation, stopping rules, and an audit trail. CRRO is structured around that complete loop.

The project is deliberately more than an LLM wrapper:

**AI reasoning + counterfactual uplift modeling + constrained portfolio optimization + deterministic policy enforcement + provider execution + outcome reconciliation.**

---

# Honest scope

The benchmark is synthetic and used for policy evaluation. A real Razorpay Test Mode run requires Test Mode credentials and a reachable webhook endpoint. A live LLM run requires the chosen provider's credentials.

Before handling real merchant traffic or Live Mode payments, add authentication/RBAC, secret management, rate limiting, structured observability, queue-based execution, encryption for sensitive data, provider retry/backoff, model monitoring, and a security review.

---

# License

Add the license appropriate for your team before public release.
