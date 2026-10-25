"""
Ledger schema + migration runner (single authority for the ledger tables on
both SQLite and PostgreSQL). Table names deliberately do not collide with the
legacy SQLAlchemy/Alembic schema (`cases`, `webhook_events`, ...), so applying
these migrations to an existing database is additive and non-destructive.

Placeholders: {TS} timestamp, {JSON} json, {BOOL} boolean, {SEQPK} sequence PK.
"""
from __future__ import annotations

from .db import Database, utcnow_iso

_TYPES = {
    "sqlite": {"TS": "TEXT", "JSON": "TEXT", "BOOL": "INTEGER",
               "SEQPK": "INTEGER PRIMARY KEY AUTOINCREMENT"},
    "postgresql": {"TS": "TIMESTAMPTZ", "JSON": "JSONB", "BOOL": "BOOLEAN",
                   "SEQPK": "BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY"},
}

CASE_STATUSES = ("OPEN", "NO_ACTION", "PENDING_APPROVAL", "ACTION_REJECTED", "ACTION_QUEUED",
                 "PROVIDER_PENDING", "OUTCOME_UNKNOWN", "ACTION_FAILED", "RECOVERED",
                 "EXPIRED", "STILL_FAILED", "NEEDS_REVIEW")
_status_check = ",".join(f"'{s}'" for s in CASE_STATUSES)

MIGRATION_0001 = f"""
CREATE TABLE IF NOT EXISTS merchant_policies (
  merchant_id TEXT PRIMARY KEY,
  policy {{JSON}} NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  updated_by TEXT NOT NULL,
  updated_at {{TS}} NOT NULL
);
CREATE TABLE IF NOT EXISTS customer_contact_state (
  merchant_id TEXT NOT NULL,
  customer_ref TEXT NOT NULL,
  opted_out {{BOOL}} NOT NULL DEFAULT 0,
  opted_out_at {{TS}},
  PRIMARY KEY (merchant_id, customer_ref)
);
CREATE TABLE IF NOT EXISTS payment_cases (
  case_id TEXT PRIMARY KEY,
  merchant_id TEXT NOT NULL,
  external_payment_id TEXT NOT NULL,
  customer_ref TEXT NOT NULL,
  amount_paise BIGINT NOT NULL CHECK (amount_paise > 0),
  currency TEXT NOT NULL CHECK (currency = 'INR'),
  payment_method TEXT NOT NULL,
  failure_reason TEXT NOT NULL,
  attempt_count INTEGER NOT NULL CHECK (attempt_count >= 1),
  merchant_category TEXT,
  failed_at {{TS}} NOT NULL,
  features {{JSON}},
  status TEXT NOT NULL CHECK (status IN ({_status_check})),
  status_reason TEXT,
  review_reason TEXT,
  data_origin TEXT NOT NULL CHECK (data_origin IN ('SYNTHETIC_DEMO','TEST_MODE','LIVE')),
  correlation_id TEXT NOT NULL,
  last_provider_event_at {{TS}},
  version INTEGER NOT NULL DEFAULT 1,
  created_at {{TS}} NOT NULL,
  updated_at {{TS}} NOT NULL,
  UNIQUE (merchant_id, external_payment_id)
);
CREATE INDEX IF NOT EXISTS ix_cases_merchant_status ON payment_cases (merchant_id, status);
CREATE INDEX IF NOT EXISTS ix_cases_merchant_created ON payment_cases (merchant_id, created_at);
CREATE INDEX IF NOT EXISTS ix_cases_correlation ON payment_cases (correlation_id);

CREATE TABLE IF NOT EXISTS case_decisions (
  decision_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL UNIQUE REFERENCES payment_cases(case_id),
  merchant_id TEXT NOT NULL,
  selected_action TEXT NOT NULL,
  reason_code TEXT,
  candidates {{JSON}} NOT NULL,
  expected_value_paise BIGINT NOT NULL,
  p_control REAL,
  p_selected REAL,
  requires_approval {{BOOL}} NOT NULL DEFAULT 0,
  approval_reason TEXT,
  model_version TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  feature_imputations {{JSON}},
  advisory_analysis {{JSON}},
  created_at {{TS}} NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_requests (
  approval_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL UNIQUE REFERENCES payment_cases(case_id),
  decision_id TEXT NOT NULL REFERENCES case_decisions(decision_id),
  merchant_id TEXT NOT NULL,
  action TEXT NOT NULL,
  amount_paise BIGINT NOT NULL,
  threshold_paise BIGINT NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PENDING','APPROVED','REJECTED','EXPIRED')),
  decided_by TEXT,
  decision_note TEXT,
  decided_at {{TS}},
  expires_at {{TS}} NOT NULL,
  created_at {{TS}} NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_approvals_merchant_status ON approval_requests (merchant_id, status);

CREATE TABLE IF NOT EXISTS budget_usage (
  merchant_id TEXT NOT NULL,
  day TEXT NOT NULL,
  contacts_used INTEGER NOT NULL DEFAULT 0 CHECK (contacts_used >= 0),
  incentive_committed_paise BIGINT NOT NULL DEFAULT 0 CHECK (incentive_committed_paise >= 0),
  PRIMARY KEY (merchant_id, day)
);

CREATE TABLE IF NOT EXISTS action_attempts (
  attempt_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL UNIQUE REFERENCES payment_cases(case_id),
  merchant_id TEXT NOT NULL,
  action TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  collect_amount_paise BIGINT NOT NULL CHECK (collect_amount_paise > 0),
  discount_paise BIGINT NOT NULL DEFAULT 0 CHECK (discount_paise >= 0),
  action_cost_paise BIGINT NOT NULL DEFAULT 0 CHECK (action_cost_paise >= 0),
  offer_id TEXT,
  requires_contact {{BOOL}} NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('QUEUED','DISPATCHING','PROVIDER_ACCEPTED','FAILED','UNKNOWN','UNSUPPORTED')),
  provider TEXT NOT NULL,
  provider_mode TEXT NOT NULL CHECK (provider_mode IN ('SIMULATOR','TEST_MODE','LIVE')),
  provider_reference TEXT UNIQUE,
  provider_status TEXT,
  last_error TEXT,
  dispatch_count INTEGER NOT NULL DEFAULT 0,
  created_at {{TS}} NOT NULL,
  updated_at {{TS}} NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_attempts_merchant_status ON action_attempts (merchant_id, status);

CREATE TABLE IF NOT EXISTS provider_events (
  event_row_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  provider_event_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  provider_created_at {{TS}},
  received_at {{TS}} NOT NULL,
  payload_sha256 TEXT NOT NULL,
  summary {{JSON}} NOT NULL,
  match_status TEXT NOT NULL CHECK (match_status IN ('MATCHED','UNMATCHED','UNSUPPORTED')),
  merchant_id TEXT,
  case_id TEXT REFERENCES payment_cases(case_id),
  attempt_id TEXT REFERENCES action_attempts(attempt_id),
  outcome TEXT NOT NULL,
  outcome_reason TEXT,
  UNIQUE (provider, provider_event_id)
);
CREATE INDEX IF NOT EXISTS ix_provider_events_case ON provider_events (case_id);
CREATE INDEX IF NOT EXISTS ix_provider_events_match ON provider_events (match_status);

CREATE TABLE IF NOT EXISTS webhook_rejections (
  rejection_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  reason TEXT NOT NULL,
  body_sha256 TEXT NOT NULL,
  body_size INTEGER NOT NULL,
  received_at {{TS}} NOT NULL
);

CREATE TABLE IF NOT EXISTS recoveries (
  recovery_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL UNIQUE REFERENCES payment_cases(case_id),
  attempt_id TEXT NOT NULL REFERENCES action_attempts(attempt_id),
  merchant_id TEXT NOT NULL,
  provider_reference TEXT NOT NULL,
  provider_payment_id TEXT UNIQUE,
  provider_event_id TEXT NOT NULL,
  currency TEXT NOT NULL CHECK (currency = 'INR'),
  gross_recovered_paise BIGINT NOT NULL CHECK (gross_recovered_paise > 0),
  cash_collected_paise BIGINT NOT NULL CHECK (cash_collected_paise > 0),
  incentive_cost_paise BIGINT NOT NULL CHECK (incentive_cost_paise >= 0),
  action_cost_paise BIGINT NOT NULL CHECK (action_cost_paise >= 0),
  provider_mode TEXT NOT NULL CHECK (provider_mode IN ('SIMULATOR','TEST_MODE','LIVE')),
  confirmed_at {{TS}} NOT NULL,
  CHECK (cash_collected_paise + incentive_cost_paise = gross_recovered_paise)
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
  merchant_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  idem_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  response {{JSON}} NOT NULL,
  created_at {{TS}} NOT NULL,
  PRIMARY KEY (merchant_id, scope, idem_key)
);

CREATE TABLE IF NOT EXISTS audit_log (
  seq {{SEQPK}},
  audit_id TEXT NOT NULL UNIQUE,
  ts {{TS}} NOT NULL,
  merchant_id TEXT,
  case_id TEXT,
  correlation_id TEXT,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  data {{JSON}} NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_case ON audit_log (case_id, seq);
CREATE INDEX IF NOT EXISTS ix_audit_merchant_ts ON audit_log (merchant_id, ts);
CREATE INDEX IF NOT EXISTS ix_audit_type ON audit_log (event_type);

CREATE TABLE IF NOT EXISTS optimizer_runs (
  run_id TEXT PRIMARY KEY,
  merchant_id TEXT NOT NULL,
  status TEXT NOT NULL,
  seed INTEGER NOT NULL,
  config {{JSON}} NOT NULL,
  result {{JSON}},
  error TEXT,
  duration_ms INTEGER,
  data_origin TEXT NOT NULL DEFAULT 'SYNTHETIC_SIMULATION',
  created_at {{TS}} NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_optimizer_runs_merchant ON optimizer_runs (merchant_id, created_at);

CREATE TABLE IF NOT EXISTS sim_provider_objects (
  provider_reference TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  reference_id TEXT NOT NULL UNIQUE,
  amount_paise BIGINT NOT NULL,
  currency TEXT NOT NULL,
  status TEXT NOT NULL,
  notes {{JSON}} NOT NULL,
  created_at {{TS}} NOT NULL
);
"""

APPEND_ONLY = {
    "sqlite": """
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
""",
    "postgresql": """
CREATE OR REPLACE FUNCTION crro_audit_append_only() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit_log is append-only'; END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log;
CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION crro_audit_append_only();
""",
}

MIGRATIONS = [("0001_ledger", MIGRATION_0001), ("0002_audit_append_only", None)]


def _statements(sql: str):
    # Split on ';' at line ends; the PL/pgSQL body is handled separately.
    return [s.strip() for s in sql.split(";\n") if s.strip()]


def migrate(db: Database) -> list[str]:
    """Apply pending migrations. Idempotent; returns versions applied now."""
    applied_now = []
    with db.transaction() as tx:
        tx.execute(f"CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, "
                   f"applied_at {_TYPES[db.dialect]['TS']} NOT NULL)")
    for version, sql in MIGRATIONS:
        with db.transaction() as tx:
            if tx.one("SELECT version FROM schema_migrations WHERE version = ?", [version]):
                continue
            if version == "0002_audit_append_only":
                body = APPEND_ONLY[db.dialect]
                if db.dialect == "postgresql":
                    tx.execute(body)  # psycopg2 executes multi-statement strings
                else:
                    for stmt in _statements(body):  # trigger bodies contain '; ' not ';\n'
                        tx.execute(stmt)
            else:
                for stmt in _statements(sql.format(**_TYPES[db.dialect])):
                    tx.execute(stmt)
            tx.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                       [version, utcnow_iso()])
            applied_now.append(version)
    return applied_now
