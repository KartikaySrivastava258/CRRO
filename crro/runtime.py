"""
RecoveryRuntime: wires configuration, ledger, provider, model and decision engine.

Synchronous dispatch is used for the demo (decide -> record -> dispatch in the
request). Because intent is committed before the provider call, a crash after
commit leaves an attempt QUEUED/DISPATCHING that `resolve_stuck()` (or the
POST /v1/attempts/{id}/resolve endpoint) completes later; nothing is lost and
nothing is duplicated. A production deployment would run dispatch in a worker.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .decision import model_store
from .decision.engine import decide
from .ledger.db import Database, loads
from .ledger.errors import NotFound, ValidationFailed
from .ledger.providers import RazorpayTestModeProvider, SimulatedProvider
from .ledger.schema import migrate
from .ledger.service import LedgerService

log = logging.getLogger("crro")
DEV_SIM_SECRET = "dev_only_simulator_webhook_secret"
# Local-demo credentials (CRRO_DEMO_MODE=1 + simulator only). Not secrets; documented in README.
DEMO_TOKENS = {
    "demo-operator-token-local-only": {"merchant_id": "merchant_demo", "actor": "demo.operator", "role": "operator"},
    "demo-viewer-token-local-only": {"merchant_id": "merchant_demo", "actor": "demo.viewer", "role": "viewer"},
    "demo-other-merchant-token-local": {"merchant_id": "merchant_other", "actor": "other.operator", "role": "operator"},
}


@dataclass
class Settings:
    database_url: str = "sqlite:///./crro_demo.db"
    provider: str = "simulator"                     # simulator | razorpay_test
    simulator_webhook_secret: str = DEV_SIM_SECRET
    model_dir: str = "outputs/model"
    operator_tokens: dict = field(default_factory=dict)   # token -> {"merchant_id","actor","role"}
    allowed_origins: tuple = ("http://localhost:5173", "http://127.0.0.1:5173")
    enable_simulator_endpoints: bool = True
    auto_dispatch: bool = True
    data_origin: str = "SYNTHETIC_DEMO"
    rate_limit_scale: float = 1.0                   # >1 relaxes limits; benchmarks only

    @classmethod
    def from_env(cls):
        tokens = {}
        # Format: token:merchant_id:actor[:role],token2:...   (role: operator|viewer)
        for item in filter(None, os.environ.get("CRRO_OPERATOR_TOKENS", "").split(",")):
            parts = item.strip().split(":")
            if len(parts) < 3 or len(parts[0]) < 16:
                raise ValueError("CRRO_OPERATOR_TOKENS entries must be token(>=16 chars):merchant:actor[:role]")
            tokens[parts[0]] = {"merchant_id": parts[1], "actor": parts[2],
                                "role": parts[3] if len(parts) > 3 else "operator"}
        prov = os.environ.get("CRRO_PROVIDER", "simulator")
        if not tokens:
            if os.environ.get("CRRO_DEMO_MODE") != "1" or prov != "simulator":
                raise RuntimeError("No CRRO_OPERATOR_TOKENS configured. Set them, or set CRRO_DEMO_MODE=1 "
                                   "(simulator only) to use the documented local-demo credentials.")
            tokens = dict(DEMO_TOKENS)
        return cls(
            database_url=os.environ.get("CRRO_DATABASE_URL") or os.environ.get("DATABASE_URL", cls.database_url),
            provider=prov,
            simulator_webhook_secret=os.environ.get("CRRO_SIMULATOR_WEBHOOK_SECRET", DEV_SIM_SECRET),
            model_dir=os.environ.get("CRRO_MODEL_DIR", cls.model_dir),
            operator_tokens=tokens,
            allowed_origins=tuple(o for o in os.environ.get("CRRO_ALLOWED_ORIGINS",
                                                            ",".join(cls.allowed_origins)).split(",") if o),
            enable_simulator_endpoints=os.environ.get("CRRO_ENABLE_SIMULATOR_ENDPOINTS", "1") == "1"
            and prov == "simulator",
            data_origin="TEST_MODE" if prov == "razorpay_test" else "SYNTHETIC_DEMO",
            rate_limit_scale=float(os.environ.get("CRRO_RATE_LIMIT_SCALE", "1")),
        )


class RecoveryRuntime:
    def __init__(self, settings: Settings, model=None, card=None, clock=None, faults=None):
        self.settings = settings
        self.db = Database(settings.database_url)
        self.applied_migrations = migrate(self.db)
        if settings.provider == "razorpay_test":
            self.provider = RazorpayTestModeProvider()
        else:
            if settings.simulator_webhook_secret == DEV_SIM_SECRET:
                log.warning(json.dumps({"event": "dev_simulator_secret_in_use",
                                        "note": "local demo only; set CRRO_SIMULATOR_WEBHOOK_SECRET"}))
            self.provider = SimulatedProvider(self.db, settings.simulator_webhook_secret, faults)
        self.ledger = LedgerService(self.db, self.provider, clock=clock)
        if model is None:
            model, card = model_store.load_or_train(Path(settings.model_dir))
        self.model, self.card = model, card
        self.started_at = time.time()

    # -------------------------------------------------------------- lifecycle
    def handle_payment_failed(self, merchant_id, body, idempotency_key, actor="api", sim_fault=None):
        status, resp, replayed = self.ledger.open_case(merchant_id, body, idempotency_key,
                                                       self.settings.data_origin, actor)
        if status == 201 and not replayed:
            self._decide_and_dispatch(merchant_id, resp["case_id"], sim_fault)
        detail = self.ledger.get_case(merchant_id, resp["case_id"])
        return status, {**resp, "replayed": replayed, "current_status": detail["case"]["status"],
                        "selected_action": (detail["decision"] or {}).get("selected_action")}, replayed

    def _decide_and_dispatch(self, merchant_id, case_id, sim_fault=None):
        case = self.ledger.get_case(merchant_id, case_id)["case"]
        policy = self.ledger.get_policy(merchant_id)
        guard = self.ledger.guardrail_state(merchant_id, case["customer_ref"])
        decision = decide(case, self.model, self.card, policy, guard, self.ledger.now())
        decision.pop("features_used", None)
        out = self.ledger.record_decision(case_id, decision)
        if out.get("status") == "ACTION_QUEUED" and self.settings.auto_dispatch:
            att = self.ledger.get_case(merchant_id, case_id)["attempt"]
            if sim_fault and isinstance(self.provider, SimulatedProvider):
                self.provider.faults[att["idempotency_key"]] = [sim_fault]   # demo/test fault injection only
            self.ledger.dispatch(att["attempt_id"])
        return out

    def decide_approval(self, merchant_id, approval_id, decision, actor, note):
        res = self.ledger.decide_approval(merchant_id, approval_id, decision, actor, note)
        att = res.get("attempt")
        if att and att.get("status") == "QUEUED" and self.settings.auto_dispatch:
            self.ledger.dispatch(att["attempt_id"])
            res["attempt"] = self.ledger.get_case(merchant_id, res["case_id"])["attempt"]
        return res

    def resolve_attempt(self, merchant_id, attempt_id, actor):
        with self.db.transaction(write=False) as tx:
            att = tx.one("SELECT merchant_id FROM action_attempts WHERE attempt_id = ?", [attempt_id])
        if not att or att["merchant_id"] != merchant_id:
            raise NotFound("attempt not found")
        return self.ledger.resolve_attempt(attempt_id, actor)

    def resolve_stuck(self, older_than_s=60):
        """Operational sweeper: finish QUEUED (crash before dispatch) and resolve
        UNKNOWN/stale DISPATCHING attempts."""
        with self.db.transaction(write=False) as tx:
            rows = tx.all("SELECT attempt_id, status FROM action_attempts WHERE status IN ('QUEUED','UNKNOWN','DISPATCHING')")
        out = []
        for r in rows:
            try:
                fn = self.ledger.dispatch if r["status"] == "QUEUED" else \
                    (lambda a: self.ledger.resolve_attempt(a, stale_after_s=older_than_s))
                out.append({"attempt_id": r["attempt_id"], "status": fn(r["attempt_id"])["status"]})
            except Exception as e:  # report and continue; never abort the sweep
                out.append({"attempt_id": r["attempt_id"], "error": type(e).__name__})
        return out

    # ---------------------------------------------------- synthetic optimizer
    def run_optimizer(self, merchant_id, cfg: dict) -> dict:
        from .experiments import prepare, run_policies
        from .optimizer import Budgets
        clean = validate_optimizer_config(cfg)
        run_id = "opt_" + uuid.uuid4().hex[:14]
        t0 = time.perf_counter()
        with self.db.transaction() as tx:
            tx.execute("INSERT INTO optimizer_runs (run_id, merchant_id, status, seed, config, created_at)"
                       " VALUES (?,?,'RUNNING',?,?,?)", [run_id, merchant_id, clean["seed"], clean,
                                                         self.ledger.now_iso()])
        try:
            ctx = prepare(clean["seed"], 0, clean["n_cases"], model=self.model)
            b = Budgets(daily_contacts=clean["contact_budget"], daily_incentive_inr=clean["incentive_budget_inr"],
                        per_action_daily_cap=clean["action_caps"], per_customer_daily_contact_cap=1)
            res = run_policies(ctx, b, include_oracle=True, cost_multiplier=clean["cost_multiplier"],
                               include_segments=True)
            alloc = res.pop("_allocation")
            sweep = []
            for frac in (0.25, 0.5, 1.0, 2.0):
                bb = Budgets(daily_contacts=int(clean["contact_budget"] * frac),
                             daily_incentive_inr=clean["incentive_budget_inr"],
                             per_action_daily_cap=clean["action_caps"], per_customer_daily_contact_cap=1)
                rr = run_policies(ctx, bb, include_oracle=False, cost_multiplier=clean["cost_multiplier"])
                crro = next(p for p in rr["policies"] if p["policy"] == "CRRO_EXACT")
                sweep.append({"contact_budget": bb.daily_contacts,
                              "incremental_expected_net_paise": crro["incremental_expected_net_paise"],
                              "incremental_net_paise": crro["incremental_net_paise"],
                              "contacts_used": crro["contacts_used"], "status": rr["solver_usage"]["CRRO_EXACT"]["status"]})
            sample = alloc[alloc["action"] != "NO_ACTION"].head(25)
            result = {**res, "contact_budget_sweep": sweep, "data_label": "SYNTHETIC_SIMULATION",
                      "model_version": self.card["model_version"],
                      "sample_allocations": [{"case_id": r.case_id, "action": r.action,
                                              "expected_value_paise": float(r.expected_incremental_value_paise)}
                                             for r in sample.itertuples()],
                      "timings_ms": ctx["timings_ms"]}
            dur = int((time.perf_counter() - t0) * 1000)
            with self.db.transaction() as tx:
                tx.execute("UPDATE optimizer_runs SET status = 'SUCCEEDED', result = ?, duration_ms = ? WHERE run_id = ?",
                           [json.loads(json.dumps(result, default=_json_default)), dur, run_id])
                self.ledger._audit(tx, "OPTIMIZER_RUN_COMPLETED", "optimizer", merchant_id=merchant_id,
                                   data={"run_id": run_id, "seed": clean["seed"], "duration_ms": dur})
        except Exception as e:
            with self.db.transaction() as tx:
                tx.execute("UPDATE optimizer_runs SET status = 'FAILED', error = ? WHERE run_id = ?",
                           [type(e).__name__, run_id])
            raise
        return self.get_optimizer_run(merchant_id, run_id)

    def get_optimizer_run(self, merchant_id, run_id):
        with self.db.transaction(write=False) as tx:
            r = tx.one("SELECT * FROM optimizer_runs WHERE run_id = ? AND merchant_id = ?", [run_id, merchant_id])
        if not r:
            raise NotFound("optimizer run not found")
        r["config"], r["result"] = loads(r["config"]), loads(r["result"])
        return r

    def list_optimizer_runs(self, merchant_id, limit=20):
        with self.db.transaction(write=False) as tx:
            rows = tx.all("SELECT run_id, status, seed, config, duration_ms, created_at, data_origin FROM optimizer_runs"
                          " WHERE merchant_id = ? ORDER BY created_at DESC LIMIT ?", [merchant_id, int(limit)])
        for r in rows:
            r["config"] = loads(r["config"])
        return rows


def _json_default(o):
    import numpy as np
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return str(o)


def validate_optimizer_config(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise ValidationFailed("body must be an object")
    allowed = {"n_cases", "seed", "contact_budget", "incentive_budget_inr", "cost_multiplier", "action_caps"}
    if set(cfg) - allowed:
        raise ValidationFailed(f"unknown fields: {sorted(set(cfg) - allowed)}")

    def _int(k, lo, hi, default):
        v = cfg.get(k, default)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ValidationFailed(f"{k} must be an integer in [{lo}, {hi}]")
        return v

    def _num(k, lo, hi, default):
        v = cfg.get(k, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= float(v) <= hi:
            raise ValidationFailed(f"{k} must be a number in [{lo}, {hi}]")
        return float(v)

    n = _int("n_cases", 200, 5000, 1500)
    caps = cfg.get("action_caps", {"ALTERNATIVE_PAYMENT_METHOD": max(1, n // 25)})
    from .action_registry import ACTION_REGISTRY
    if not isinstance(caps, dict) or any(a not in ACTION_REGISTRY or isinstance(c, bool) or not isinstance(c, int)
                                         or c < 0 for a, c in caps.items()):
        raise ValidationFailed("action_caps must map registry actions to non-negative integers")
    return {"n_cases": n, "seed": _int("seed", 0, 1_000_000, 2026),
            "contact_budget": _int("contact_budget", 0, 100_000, max(1, n // 10)),
            "incentive_budget_inr": _num("incentive_budget_inr", 0, 10_000_000, 5.0 * n),
            "cost_multiplier": _num("cost_multiplier", 0, 20, 1.0), "action_caps": caps}
