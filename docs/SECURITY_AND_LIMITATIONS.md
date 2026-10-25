# Security and limitations

CRRO is a **portfolio research prototype**. It is not production software, is not affiliated with
Visa, Razorpay or any payment network, has no PCI DSS scope or certification, processes no card
data, and has produced no real-world revenue. All demo data is synthetic.

## Implemented

| Control | Implementation | Evidence |
|---|---|---|
| Webhook authenticity | HMAC-SHA256 over the **exact raw bytes**, constant-time compare (`hmac.compare_digest`), non-ASCII signatures rejected | `test_signature_is_checked_over_raw_bytes` (re-serialised JSON rejected) |
| Replay / duplicates | `UNIQUE(provider, provider_event_id)` in the same transaction as effects | concurrent-delivery tests, webhook trials |
| No dedup poisoning | Unverified requests never touch the event ledger; rejections store only hash + size | `test_invalid_signature_cannot_poison_event_id` |
| Payload limits | 64 KB webhooks, 256 KB API bodies → 413 | tests |
| Authentication | Bearer tokens mapping to `(merchant, actor, role)`; `viewer` cannot mutate (403) | `test_auth_roles_and_merchant_isolation` |
| Tenant isolation | Every query filtered by the token's merchant; other merchants' objects return 404 (no existence leak) | ledger + HTTP tests |
| Input validation | Allow-listed fields, identifier regexes, paise/decimal checks, currency allow-list, enum checks, policy patch validation | `test_missing_key_and_invalid_payloads_rejected`, `test_policy_validation` |
| Rate limiting | Token bucket per merchant/IP per route (429 + Retry-After) | `test_rate_limit_cors_request_id_and_errors` |
| CORS | Explicit origin allow-list (no `*`) | same |
| Error hygiene | Stable error schema with request id; unhandled errors return a generic 500, logged by type only | same |
| Secrets | Read from environment only; `.env.example` has placeholders; demo credentials only when `CRRO_DEMO_MODE=1` **and** the simulator provider is used; Razorpay adapter refuses non-`rzp_test_` keys | `Settings.from_env` |
| Audit | Append-only at the database level (triggers block UPDATE/DELETE) | `test_audit_is_append_only_and_correlated` |
| Model artifact | SHA-256 recorded in the model card and verified on load | `model_store.load` |
| Static files | Path-traversal guard | `test_path_traversal_blocked` |
| Least data | Pseudonymous customer refs; no names, phones, emails or card data stored; provider payloads summarised, not stored | schema |

## Not implemented / limitations

* **Authentication is demo-grade.** Static bearer tokens from configuration; no OAuth/OIDC, no
  rotation, no per-user secrets, no MFA. Demo tokens are public by design (local only).
* **Audit is append-only but not tamper-evident** (no hash chain or external anchoring); a DB
  superuser can drop triggers.
* **Rate limits and metrics are per process**; multiple workers do not share them.
* **PostgreSQL, FastAPI, Docker and CI were not executed** in the build environment.
* **Razorpay Test Mode** adapter was never run against Razorpay; retries are not supported via
  Payment Links and return `UNSUPPORTED`.
* **Synchronous dispatch** inside the request; no background worker or scheduled sweeper.
* **Exactly-once is not claimed.** Dispatch is at-least-once with idempotent effects that depend
  on the provider honouring the idempotency/reference key.
* **Pickle** model artifacts must only be loaded from a trusted path.
* **Clock/day boundaries** use UTC days for budgets.
* **Model limitations:** trained only on synthetic randomised data; no uncertainty estimates; no
  feature attribution; features include behavioural rates (`customer_success_rate`), which could
  proxy for socio-economic attributes in real data. No sensitive attributes are used. Segment
  results are reported but no fairness guardrail is enforced.
* **LLM analyst** (previous version) is not part of v1; it never controlled money movement.

## Before any production use

Real identity provider and per-user authorisation; secret manager and rotation; mTLS or IP
allow-listing for provider webhooks; a worker + outbox relay + scheduled reconciliation sweeps;
shared rate limiting; tamper-evident audit storage; PostgreSQL HA, backups and migration review;
load testing on the target stack; provider certification and legal/compliance review (including
data-protection obligations and customer-contact consent); model validation on real, consented data.
