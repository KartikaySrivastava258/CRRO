# CRRO — AI Counterfactual Revenue Recovery Optimizer

Razorpay Buildathon 2026 · Track 03 — AI Revenue Recovery

> We don't just recover failed payments. We determine where recovery effort will create the most incremental revenue — before we spend the recovery opportunity.

CRRO is a quantitative MVP for counterfactual revenue recovery. It estimates the incremental effect of each pre-approved intervention, allocates scarce recovery capacity across a portfolio, enforces deterministic guardrails, executes through a simulated payment-provider adapter, and evaluates against explicit baselines.

## Core idea

Existing recovery automation asks *"what action should I take?"* CRRO asks:

1. What is the incremental effect of each allowed intervention for this specific case versus doing nothing?
2. Given limited contact slots, incentive budget, and merchant policy, where should scarce recovery effort go to maximize total incremental recovered value?

The system uses an RCT-style synthetic benchmark with a hidden causal outcome generator, a T-learner uplift model, constrained portfolio optimization, deterministic policy checks, idempotent simulated execution, audit logging, and common-random-number evaluation.

## Current MVP

```text
crro/data_generator.py   synthetic failed-payment cases + randomized actions
crro/oracle.py           hidden ground-truth causal outcome model
crro/model.py            T-learner uplift model
crro/action_registry.py  pre-approved action vocabulary
crro/optimizer.py        constrained portfolio allocator
crro/policy.py           deterministic guardrails
crro/executor.py         simulated execution + idempotency + audit trail
crro/baselines.py        control, fixed retry, rule based, per-case optimal
crro/evaluate.py         fair outcome realization + evaluation
run_demo.py              end-to-end benchmark
```

Run:

```bash
pip install -r requirements.txt
python3 run_demo.py
python3 gen_dashboard.py
```

## Design principles

- **NO_ACTION is a real option.** Positive recovery probability alone is not enough; the intervention must have positive expected incremental value.
- **The LLM does not control money.** Actions come from a fixed registry and pass deterministic policy gates.
- **Portfolio constraints matter.** Contact and incentive capacity are shared scarce resources, so independently maximizing each case is not operationally valid.
- **Evaluation is explicit.** CRRO is compared with control and operational baselines on a held-out distribution-shifted set using common random numbers.
- **Claims are honest.** The included benchmark is synthetic and controlled; Razorpay execution is represented by an adapter interface rather than a live merchant transaction.

## Roadmap

P0 upgrades are being implemented directly in this repository: correct control/incremental evaluation, exact portfolio optimization, persistent policy state, merchant-configured economics and guardrails, stronger causal metrics, and multi-seed evaluation. Subsequent layers will add PostgreSQL/FastAPI, an AI Recovery Analyst with structured outputs, evidence retrieval, Razorpay test-mode integration, reconciliation, and a production-style dashboard.
