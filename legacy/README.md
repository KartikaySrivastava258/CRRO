# legacy/ — superseded code kept for traceability

Nothing here is imported by the current application, tests, CI or Docker image.
It is preserved (moved with `git mv`, history intact) so reviewers can see what
was replaced and why. See docs/IMPLEMENTATION_AUDIT.md for the defect list.

| Path | What it was | Why it was retired |
|---|---|---|
| `app/`, `migrations_app/` | Second FastAPI app (`app.main`) + its own Alembic tree | Could not import (missing `crro.baselines` names, missing `app/action_registry.py`, `pydantic_settings` not in requirements); its `webhook_events` table conflicted with the other schema; used `create_all()` at startup. |
| `alembic_v0/` | Alembic tree for the old SQLAlchemy schema used by `api_v0.py` | Replaced by the ledger schema (`crro/ledger/schema.py`), one migration authority for SQLite + PostgreSQL. New tables use non-colliding names, so an old database is not modified. |
| `api_v0.py` | Previous event API | Invalid-signature webhooks poisoned the dedup ledger; no idempotency on `payment.failed`; reconciliation trusted payload amounts (`seed_demo` sent ₹15,000 for every case); float rupees; provider call before persisting intent. |
| `crro_v0/` | SQLAlchemy workflow/state store, in-process idempotency `set`, old provider adapters, LLM analyst | Superseded by `crro/ledger/*` and `crro/decision/*`. The LLM analyst (advisory text only) is not wired into v1; re-integration would require schema-validated output and must never gate money movement. |
| `frontend-next/` | Unused Next.js stub calling `app/` routes | Disconnected from the maintained dashboard (`crro-dashboard/`). |
| `outputs_v0/` | Artifacts from the old pipeline | Produced with inconsistent incentive pricing (optimizer 5% vs evaluator 2%); not comparable with current results. |
| `tests/*_v0.py` | Original tests | Scenarios ported to `tests/unit/test_legacy_regressions.py`, `tests/integration/test_ledger.py`, `tests/integration/test_http_api.py`. |
