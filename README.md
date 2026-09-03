# CRRO — Counterfactual Revenue Recovery Optimizer

CRRO is an end-to-end revenue-recovery decision system for Razorpay Buildathon 2026 Track 03. It estimates **incremental** recovery, allocates scarce intervention capacity across a portfolio, applies deterministic guardrails, persists decisions, executes bounded provider actions, and reconciles outcomes.

## Architecture

`failed payment → context/evidence → counterfactual model → incremental economics → portfolio optimizer → guardrails → approval → provider → webhook reconciliation → audit`

### Implemented layers

- **Causal ML:** T-learner action-conditioned recovery probabilities and uplift against `NO_ACTION`.
- **Portfolio optimization:** contact, incentive and action-cap constrained allocation.
- **Policy:** deterministic eligibility, opt-out, attempt and approval gates. The LLM cannot bypass these controls.
- **PostgreSQL:** cases, evidence, approvals, idempotency records, executions and webhook events. Alembic migration included.
- **AI Recovery Analyst:** optional OpenAI structured JSON output with a strict action allowlist and deterministic fallback when no API key exists.
- **Retrieval:** replaceable `VamanaIndex` interface with a dependency-free graph-search implementation for evidence; ready to swap for a native Vamana/ANN backend.
- **Razorpay Test Mode:** Payment Link creation through the official REST API using test credentials only; webhook signature verification and reconciliation included.
- **Idempotency:** persisted request keys prevent duplicate provider actions; webhook events are also deduplicated.
- **Frontend:** Next.js command center with live optimization, policy benchmark, budget usage, guardrail and allocation views.
- **CI:** backend tests/demo plus a production Next.js build.

Razorpay Payment Links are a supported API mechanism for collecting payments, and Razorpay documents separate Test/Live modes. CRRO intentionally limits the provider adapter in this build to **Test Mode Payment Links**; it never creates live charges. citeturn0search1turn0search10

## Run with Docker

```bash
cp .env.example .env
# optionally set Razorpay TEST credentials and OPENAI_API_KEY
docker compose up --build
```

Open `http://localhost:3000` for the frontend and `http://localhost:8000/docs` for the API.

The API automatically runs the Alembic migration against PostgreSQL on startup.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

For the frontend:

```bash
cd frontend
npm install
npm run dev
```

## Key API endpoints

- `GET /api/health` — service/database health
- `POST /api/optimize` — run the recovery optimization benchmark
- `GET /api/report` — latest report
- `GET /api/cases` — allocated cases
- `POST /api/analyst/analyze` — guarded AI recovery analysis
- `GET /api/evidence/search` — evidence retrieval
- `POST /api/evidence` — add merchant evidence
- `GET /api/approvals` — pending/decided approvals
- `POST /api/approvals/{id}` — approve/reject a decision
- `POST /api/razorpay/payment-link` — create a Razorpay **Test Mode** payment link
- `POST /api/webhooks/razorpay` — signed webhook ingestion + reconciliation

## Safety and integrity

- The benchmark remains synthetic; it does not claim real merchant uplift.
- `NO_ACTION` is a genuine control option.
- The LLM may diagnose/explain and select only pre-approved action names. It cannot set money amounts, invent offers, execute payments, or report fabricated outcomes.
- Razorpay credentials are read from environment variables and are never committed.
- Test Mode must be used for the provider integration. Razorpay documents that Test and Live modes use separate API keys and that Test Mode does not accept real payments. citeturn0search10
- Payment Link creation uses the documented `/v1/payment_links` API. citeturn0search1

## Production hardening still recommended

Before a real merchant deployment, add secret management, authentication/RBAC, rate limiting, structured observability, queue-based execution, encrypted PII handling, provider retry/backoff, a native high-performance Vamana/ANN implementation, model registry/monitoring, and a formal security review. The current system is **production-shaped and runnable**, but those operational controls should be completed before handling live money.
