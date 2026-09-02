# CRRO — Counterfactual Revenue Recovery Optimizer

CRRO is a working end-to-end reference implementation for Razorpay Buildathon 2026 Track 03: **AI Revenue Recovery**.

## What is different

Most recovery systems answer: **“Should I retry this failed payment?”**
CRRO answers: **“Given this entire at-risk portfolio and scarce merchant resources, which intervention creates the most incremental revenue?”**

It combines:
1. RCT-style synthetic payment history with a hidden causal outcome model.
2. A T-learner counterfactual model estimating recovery probability for every allowed action.
3. Incremental value economics: uplift × recoverable amount − action/incentive cost.
4. Portfolio allocation under contact, incentive and action caps.
5. Deterministic guardrails and human-approval routing.
6. Idempotent simulated provider execution and an audit trail.
7. Held-out distribution-shift evaluation against control, fixed retry, rule-based, raw-probability, uplift-only and unconstrained oracle policies.
8. FastAPI API + browser dashboard + Docker deployment.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run_demo.py
uvicorn app.main:app --reload
```

Open `http://localhost:8000`.

API: `GET /api/health`, `GET /api/report`, `GET /api/cases`, `POST /api/optimize`.

## Run with Docker

```bash
docker compose up --build
```

## Integrity note

The benchmark is synthetic. Recovery claims are measured from the controlled benchmark, not real merchant revenue. The execution layer is a safe simulated provider; production Razorpay credentials are not required.

The LLM is intentionally outside money control: an eventual AI analyst can explain evidence and propose only registry actions, while the model, optimizer, policy engine and execution layer remain typed boundaries.
