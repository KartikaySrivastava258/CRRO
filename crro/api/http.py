"""
Transport-agnostic HTTP application.

`HttpApp.handle(Request) -> Response` contains ALL request handling: routing,
authentication, merchant scoping, validation, rate limiting, error mapping,
request IDs, CORS, metrics and logging. Two thin transports call it:
  * api.py                 -- FastAPI/uvicorn (the documented production-style entry point)
  * scripts/serve.py       -- Python standard library ThreadingHTTPServer
so tests against either exercise the same code path.

Auth model (local demo / portfolio scope, see docs/SECURITY_AND_LIMITATIONS.md):
  Authorization: Bearer <token>; tokens map to (merchant_id, actor, role).
  Every query is scoped to the token's merchant -> other merchants' objects 404.
  role=viewer cannot mutate. Webhooks authenticate by HMAC signature instead.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import re
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..ledger.errors import (BadRequest, Forbidden, MethodNotAllowed, NotFound, PayloadTooLarge, RateLimited, ServiceError,
                             Unauthorized, ValidationFailed)

log = logging.getLogger("crro.http")
MAX_BODY = 256 * 1024
_REQ_ID = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")


@dataclass
class Request:
    method: str
    path: str
    query: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    client: str = "unknown"

    def __post_init__(self):
        self.headers = {k.lower(): v for k, v in self.headers.items()}

    def json(self):
        if not self.body:
            return {}
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeDecodeError):
            raise BadRequest("request body is not valid JSON") from None


@dataclass
class Response:
    status: int
    body: object = None
    headers: dict = field(default_factory=dict)
    content_type: str = "application/json"

    def bytes(self) -> bytes:
        if isinstance(self.body, (bytes, bytearray)):
            return bytes(self.body)
        if isinstance(self.body, str) and self.content_type != "application/json":
            return self.body.encode()
        return json.dumps(self.body, default=str, separators=(",", ":")).encode()


class TokenBucket:
    """Per-process token bucket (documented limitation: not shared across workers)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {}

    def allow(self, key, rate_per_min: float, burst: int) -> tuple[bool, float]:
        now = time.monotonic()
        with self.lock:
            tokens, last = self.state.get(key, (burst, now))
            tokens = min(burst, tokens + (now - last) * rate_per_min / 60.0)
            if tokens < 1:
                self.state[key] = (tokens, now)
                return False, (1 - tokens) * 60.0 / rate_per_min
            self.state[key] = (tokens - 1, now)
            return True, 0.0


class Metrics:
    BUCKETS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)

    def __init__(self):
        self.lock = threading.Lock()
        self.requests = defaultdict(int)
        self.latency = defaultdict(lambda: [0] * (len(self.BUCKETS) + 1))
        self.latency_sum = defaultdict(float)
        self.events = defaultdict(int)

    def observe(self, route, status, ms):
        with self.lock:
            self.requests[(route, status)] += 1
            i = next((k for k, b in enumerate(self.BUCKETS) if ms <= b), len(self.BUCKETS))
            self.latency[route][i] += 1
            self.latency_sum[route] += ms

    def inc(self, name, n=1):
        with self.lock:
            self.events[name] += n

    def render(self, gauges: dict) -> str:
        out = ["# TYPE crro_http_requests_total counter"]
        with self.lock:
            for (route, status), n in sorted(self.requests.items()):
                out.append(f'crro_http_requests_total{{route="{route}",status="{status}"}} {n}')
            out.append("# TYPE crro_http_request_duration_ms histogram")
            for route, counts in sorted(self.latency.items()):
                cum = 0
                for b, c in zip(list(self.BUCKETS) + ["+Inf"], counts):
                    cum += c
                    out.append(f'crro_http_request_duration_ms_bucket{{route="{route}",le="{b}"}} {cum}')
                out.append(f'crro_http_request_duration_ms_sum{{route="{route}"}} {self.latency_sum[route]:.3f}')
                out.append(f'crro_http_request_duration_ms_count{{route="{route}"}} {cum}')
            for name, n in sorted(self.events.items()):
                out.append(f"# TYPE crro_{name}_total counter")
                out.append(f"crro_{name}_total {n}")
        for name, v in sorted(gauges.items()):
            out.append(f"# TYPE crro_{name} gauge")
            out.append(f"crro_{name} {v}")
        return "\n".join(out) + "\n"


@dataclass
class Route:
    method: str
    pattern: str
    handler: Callable
    auth: str = "viewer"                 # none | viewer | operator | signature
    rate: Optional[tuple] = None         # (per_minute, burst)
    summary: str = ""

    def __post_init__(self):
        self.regex = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[A-Za-z0-9_\-.:]+)", self.pattern) + "$")


class HttpApp:
    def __init__(self, runtime, dashboard_dir: Optional[str] = None):
        self.rt = runtime
        self.metrics = Metrics()
        self.limiter = TokenBucket()
        self.dashboard_dir = Path(dashboard_dir).resolve() if dashboard_dir else None
        R = Route
        self.routes = [
            R("GET", "/health/live", self.h_live, "none", summary="Process liveness"),
            R("GET", "/health/ready", self.h_ready, "none", summary="Database + model readiness"),
            R("GET", "/metrics", self.h_metrics, "none", summary="Prometheus metrics"),
            R("GET", "/openapi.json", self.h_openapi, "none", summary="Route summary"),
            R("GET", "/v1/me", self.h_me, summary="Authenticated principal and environment labels"),
            R("GET", "/v1/overview", self.h_overview, summary="Portfolio overview (ledger-derived)"),
            R("GET", "/v1/cases", self.h_cases, summary="List cases (cursor pagination)"),
            R("GET", "/v1/cases/{case_id}", self.h_case, summary="Case detail, decision, timeline"),
            R("POST", "/v1/events/payment-failed", self.h_payment_failed, "operator", (120, 60),
              "Ingest payment.failed (Idempotency-Key required)"),
            R("GET", "/v1/approvals", self.h_approvals, summary="Approval queue"),
            R("POST", "/v1/approvals/{approval_id}/decision", self.h_approval_decision, "operator", (60, 20),
              "Approve or reject (idempotent)"),
            R("POST", "/v1/attempts/{attempt_id}/resolve", self.h_resolve, "operator", (30, 10),
              "Resolve an UNKNOWN provider outcome"),
            R("POST", "/v1/customers/{customer_ref}/opt-out", self.h_opt_out, "operator", (60, 20),
              "Set customer contact opt-out"),
            R("GET", "/v1/policy", self.h_policy, summary="Merchant policy"),
            R("PATCH", "/v1/policy", self.h_policy_patch, "operator", (30, 10), "Update merchant policy"),
            R("GET", "/v1/audit", self.h_audit, summary="Audit log (filters + cursor)"),
            R("GET", "/v1/audit/export", self.h_audit_export, rate=(10, 5), summary="Audit JSON export"),
            R("GET", "/v1/provider-events/unmatched", self.h_unmatched, summary="Unmatched provider events"),
            R("POST", "/v1/optimizer/runs", self.h_opt_run, "operator", (6, 3),
              "Run a synthetic portfolio simulation"),
            R("GET", "/v1/optimizer/runs", self.h_opt_list, summary="Recent optimizer runs"),
            R("GET", "/v1/optimizer/runs/{run_id}", self.h_opt_get, summary="Optimizer run result"),
            R("GET", "/v1/optimizer/runs/{run_id}/policies.csv", self.h_opt_csv, summary="Policy table CSV"),
            R("GET", "/v1/model", self.h_model, summary="Model card"),
            R("GET", "/v1/benchmarks/latest", self.h_bench, summary="Latest offline benchmark summary"),
            R("POST", "/v1/webhooks/razorpay", self.h_webhook, "signature", (600, 200),
              "Provider webhook (HMAC-SHA256 over raw body)"),
            R("POST", "/v1/simulator/payment-links/{provider_reference}/events", self.h_sim_event, "operator",
              (60, 30), "Simulator only: emit a signed provider webhook through the real webhook path"),
        ]

    # ----------------------------------------------------------------- core
    def handle(self, req: Request) -> Response:
        t0 = time.perf_counter()
        rid = req.headers.get("x-request-id")
        rid = rid if rid and _REQ_ID.match(rid) else "req_" + uuid.uuid4().hex[:16]
        route_name, principal = "unmatched", None
        try:
            if req.method == "OPTIONS":
                resp = Response(204, b"")
            else:
                if len(req.body) > MAX_BODY:
                    raise PayloadTooLarge("request body too large")
                route, params = self._match(req)
                if route is None:
                    resp = self._static(req)
                else:
                    route_name = f"{route.method} {route.pattern}"
                    principal = self._authenticate(req, route)
                    if route.rate:
                        key = (principal or {}).get("merchant_id") or req.client
                        k = max(self.rt.settings.rate_limit_scale, 1e-9)
                        ok, retry = self.limiter.allow((route.pattern, key), route.rate[0] * k,
                                                       max(1, int(route.rate[1] * k)))
                        if not ok:
                            self.metrics.inc("rate_limited")
                            raise RateLimited("rate limit exceeded", retry_after_s=round(retry, 1))
                    out = route.handler(req, principal, **params)
                    resp = out if isinstance(out, Response) else Response(*out) if isinstance(out, tuple) \
                        else Response(200, out)
        except ServiceError as e:
            body = {"error": {"code": e.code, "message": e.message, "details": e.details}, "request_id": rid}
            resp = Response(e.status, body)
            if isinstance(e, RateLimited):
                resp.headers["Retry-After"] = str(int(e.details.get("retry_after_s", 1)) + 1)
        except Exception as e:  # never leak internals
            log.error(json.dumps({"event": "unhandled_error", "request_id": rid, "route": route_name,
                                  "error_type": type(e).__name__}))
            resp = Response(500, {"error": {"code": "internal_error", "message": "internal error"},
                                  "request_id": rid})
        ms = (time.perf_counter() - t0) * 1000
        resp.headers["X-Request-ID"] = rid
        resp.headers["X-CRRO-Environment"] = self._env_label()
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        origin = req.headers.get("origin")
        if origin and origin in self.rt.settings.allowed_origins:
            resp.headers.update({"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                                 "Access-Control-Allow-Headers": "Authorization, Content-Type, Idempotency-Key, X-Request-ID",
                                 "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
                                 "Access-Control-Expose-Headers": "X-Request-ID, X-CRRO-Environment, Idempotent-Replayed"})
        self.metrics.observe(route_name, resp.status, ms)
        if route_name != "GET /metrics":
            log.info(json.dumps({"event": "http_request", "request_id": rid, "route": route_name, "status": resp.status,
                                 "latency_ms": round(ms, 2), "merchant_id": (principal or {}).get("merchant_id")}))
        return resp

    def _match(self, req):
        method_mismatch = False
        for r in self.routes:
            m = r.regex.match(req.path)
            if m:
                if r.method == req.method:
                    return r, m.groupdict()
                method_mismatch = True
        if method_mismatch:
            raise MethodNotAllowed(f"{req.method} not allowed on {req.path}")
        return None, {}

    def _authenticate(self, req, route):
        if route.auth in ("none", "signature"):
            return None
        auth = req.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            raise Unauthorized("missing bearer token")
        p = self.rt.settings.operator_tokens.get(auth[7:].strip())
        if p is None:
            raise Unauthorized("invalid token")
        if route.auth == "operator" and p["role"] != "operator":
            raise Forbidden("operator role required")
        return p

    def _env_label(self):
        s = self.rt.settings
        return "local-demo-simulator" if s.provider == "simulator" else "razorpay-test-mode"

    def _static(self, req):
        if self.dashboard_dir is None or req.method != "GET" or req.path.startswith(("/v1/", "/health")):
            raise NotFound("route not found")
        rel = req.path.lstrip("/") or "index.html"
        target = (self.dashboard_dir / rel).resolve()
        if self.dashboard_dir not in target.parents and target != self.dashboard_dir:
            raise NotFound("route not found")           # path traversal guard
        if not target.is_file():
            target = self.dashboard_dir / "index.html"  # SPA fallback
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        return Response(200, target.read_bytes(), {"Cache-Control": "no-cache"}, ctype)

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _q_int(req, name, default, lo, hi):
        v = req.query.get(name)
        if v is None or v == "":
            return default
        try:
            n = int(v)
        except ValueError:
            raise ValidationFailed(f"{name} must be an integer") from None
        if not lo <= n <= hi:
            raise ValidationFailed(f"{name} must be in [{lo}, {hi}]")
        return n

    # ------------------------------------------------------------- handlers
    def h_live(self, req, p):
        return {"status": "alive", "uptime_s": round(time.time() - self.rt.started_at, 1)}

    def h_ready(self, req, p):
        db_ok = self.rt.db.ping()
        model_ok = self.rt.model is not None
        status = 200 if db_ok and model_ok else 503
        return status, {"status": "ready" if status == 200 else "not_ready", "database": db_ok,
                        "database_dialect": self.rt.db.dialect, "model_loaded": model_ok,
                        "model_version": self.rt.card["model_version"] if model_ok else None,
                        "provider": self.rt.provider.name, "provider_mode": self.rt.provider.mode}

    def h_metrics(self, req, p):
        with self.rt.db.transaction(write=False) as tx:
            g = {"approval_backlog": tx.scalar("SELECT COUNT(*) FROM approval_requests WHERE status='PENDING'"),
                 "outcome_unknown_attempts": tx.scalar("SELECT COUNT(*) FROM action_attempts WHERE status='UNKNOWN'"),
                 "pending_reconciliation_cases": tx.scalar("SELECT COUNT(*) FROM payment_cases WHERE status='PROVIDER_PENDING'"),
                 "unmatched_provider_events": tx.scalar("SELECT COUNT(*) FROM provider_events WHERE match_status='UNMATCHED'"),
                 "webhook_rejections": tx.scalar("SELECT COUNT(*) FROM webhook_rejections")}
        return Response(200, self.metrics.render(g), content_type="text/plain; version=0.0.4")

    def h_openapi(self, req, p):
        paths = defaultdict(dict)
        for r in self.routes:
            paths[r.pattern][r.method.lower()] = {
                "summary": r.summary,
                "security": [] if r.auth in ("none", "signature") else [{"bearer": []}],
                "x-crro-auth": r.auth, "x-crro-rate-limit-per-min": r.rate[0] if r.rate else None}
        return {"openapi": "3.1.0", "info": {"title": "CRRO API", "version": "1.0.0"},
                "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}},
                "paths": dict(paths)}

    def h_me(self, req, p):
        s = self.rt.settings
        return {"merchant_id": p["merchant_id"], "actor": p["actor"], "role": p["role"],
                "environment": self._env_label(), "provider_mode": self.rt.provider.mode,
                "data_origin": s.data_origin, "simulator_endpoints": s.enable_simulator_endpoints,
                "model_version": self.rt.card["model_version"]}

    def h_overview(self, req, p):
        return self.rt.ledger.overview(p["merchant_id"])

    def h_cases(self, req, p):
        return self.rt.ledger.list_cases(p["merchant_id"], req.query.get("status"), req.query.get("q"),
                                         self._q_int(req, "limit", 25, 1, 200), req.query.get("cursor"))

    def h_case(self, req, p, case_id):
        return self.rt.ledger.get_case(p["merchant_id"], case_id)

    def h_payment_failed(self, req, p):
        fault = req.headers.get("x-simulator-fault")
        if fault and not (self.rt.settings.enable_simulator_endpoints and
                          fault in ("timeout_after", "timeout_before", "error")):
            raise BadRequest("X-Simulator-Fault is only accepted by the simulator provider")
        status, body, replayed = self.rt.handle_payment_failed(p["merchant_id"], req.json(),
                                                               req.headers.get("idempotency-key"),
                                                               actor=p["actor"], sim_fault=fault)
        return Response(status, body, {"Idempotent-Replayed": "true" if replayed else "false"})

    def h_approvals(self, req, p):
        st = req.query.get("status", "PENDING")
        if st not in ("PENDING", "APPROVED", "REJECTED", "EXPIRED", "ALL"):
            raise ValidationFailed("status must be PENDING|APPROVED|REJECTED|EXPIRED|ALL")
        return self.rt.ledger.list_approvals(p["merchant_id"], st, self._q_int(req, "limit", 50, 1, 200),
                                             req.query.get("cursor"))

    def h_approval_decision(self, req, p, approval_id):
        b = req.json()
        if not isinstance(b, dict) or set(b) - {"decision", "note"}:
            raise ValidationFailed("body must be {decision: APPROVE|REJECT, note: string}")
        res = self.rt.decide_approval(p["merchant_id"], approval_id, b.get("decision"), p["actor"], b.get("note", ""))
        if res.get("expired"):
            return Response(409, {"error": {"code": "approval_expired", "message": "approval expired before decision",
                                            "details": {"approval_id": approval_id}}})
        return res

    def h_resolve(self, req, p, attempt_id):
        return self.rt.resolve_attempt(p["merchant_id"], attempt_id, p["actor"])

    def h_opt_out(self, req, p, customer_ref):
        b = req.json()
        if not isinstance(b, dict) or not isinstance(b.get("opted_out"), bool):
            raise ValidationFailed("body must be {opted_out: boolean}")
        return self.rt.ledger.set_opt_out(p["merchant_id"], customer_ref, b["opted_out"], p["actor"])

    def h_policy(self, req, p):
        return self.rt.ledger.get_policy(p["merchant_id"])

    def h_policy_patch(self, req, p):
        return self.rt.ledger.update_policy(p["merchant_id"], req.json(), p["actor"])

    def _audit_filters(self, req):
        q = req.query
        return dict(case_id=q.get("case_id"), event_type=q.get("event_type"), correlation_id=q.get("correlation_id"),
                    date_from=q.get("from"), date_to=q.get("to"))

    def h_audit(self, req, p):
        return self.rt.ledger.list_audit(p["merchant_id"], **self._audit_filters(req),
                                         limit=self._q_int(req, "limit", 100, 1, 500), cursor=req.query.get("cursor"))

    def h_audit_export(self, req, p):
        f = self._audit_filters(req)
        items, cur = [], None
        while len(items) < 5000:
            page = self.rt.ledger.list_audit(p["merchant_id"], **f, limit=500, cursor=cur)
            items += page["items"]
            cur = page["next_cursor"]
            if not cur:
                break
        body = {"exported_at": self.rt.ledger.now_iso(), "merchant_id": p["merchant_id"], "filters": f,
                "environment": self._env_label(), "truncated": bool(cur), "count": len(items),
                "note": "Pseudonymous identifiers only; no payment credentials are stored.", "items": items}
        return Response(200, body, {"Content-Disposition": 'attachment; filename="crro-audit-export.json"'})

    def h_unmatched(self, req, p):
        return {"items": self.rt.ledger.unmatched_events(self._q_int(req, "limit", 50, 1, 200))}

    def h_opt_run(self, req, p):
        t0 = time.perf_counter()
        run = self.rt.run_optimizer(p["merchant_id"], req.json())
        self.metrics.inc("optimizer_runs")
        self.metrics.inc("optimizer_run_ms", int((time.perf_counter() - t0) * 1000))
        return 201, run

    def h_opt_list(self, req, p):
        return {"items": self.rt.list_optimizer_runs(p["merchant_id"], self._q_int(req, "limit", 20, 1, 100))}

    def h_opt_get(self, req, p, run_id):
        return self.rt.get_optimizer_run(p["merchant_id"], run_id)

    def h_opt_csv(self, req, p, run_id):
        run = self.rt.get_optimizer_run(p["merchant_id"], run_id)
        if not run.get("result"):
            raise NotFound("run has no result")
        cols = ["policy", "n_cases", "recovery_rate", "gross_recovered_paise", "action_cost_paise",
                "incentive_cost_paise", "net_value_paise", "incremental_net_paise", "incremental_expected_net_paise",
                "actions_taken", "contacts_used", "incentive_exposure_paise"]
        lines = [f"# run_id={run_id} seed={run['seed']} label=SYNTHETIC_SIMULATION model={run['result']['model_version']}",
                 f"# config={json.dumps(run['config'], sort_keys=True)}", ",".join(cols)]
        for row in run["result"]["policies"]:
            lines.append(",".join('"%s"' % str(row.get(c, "")).replace('"', "'") for c in cols))
        return Response(200, "\n".join(lines) + "\n",
                        {"Content-Disposition": f'attachment; filename="{run_id}-policies.csv"'}, "text/csv")

    def h_model(self, req, p):
        return self.rt.card

    def h_bench(self, req, p):
        path = Path("docs/benchmarks/results/policy_benchmark_summary.json")
        if not path.exists():
            raise NotFound("no benchmark summary found; run scripts/benchmarks/policy_benchmark.py")
        return json.loads(path.read_text())

    def h_webhook(self, req, p):
        status, body = self.rt.ledger.ingest_webhook(req.body, req.headers)
        self.metrics.inc("webhook_duplicates" if body.get("status") == "duplicate_ignored" else "webhooks_processed")
        return status, body

    def h_sim_event(self, req, p, provider_reference):
        if not self.rt.settings.enable_simulator_endpoints:
            raise NotFound("route not found")
        b = req.json()
        allowed = {"event_type", "amount_paid_paise", "deliveries", "event_id", "created_at"}
        if not isinstance(b, dict) or set(b) - allowed:
            raise ValidationFailed(f"allowed fields: {sorted(allowed)}")
        et = b.get("event_type", "payment_link.paid")
        if et not in ("payment_link.paid", "payment_link.expired", "payment_link.cancelled",
                      "payment_link.partially_paid", "payment.captured", "payment.failed"):
            raise ValidationFailed("unsupported event_type")
        deliveries = b.get("deliveries", 1)
        if isinstance(deliveries, bool) or not isinstance(deliveries, int) or not 1 <= deliveries <= 5:
            raise ValidationFailed("deliveries must be an integer in [1, 5]")
        obj = self.rt.provider.object(provider_reference)
        if obj is not None and (obj["notes"] or {}).get("merchant_id") not in (None, p["merchant_id"]):
            raise NotFound("provider object not found")
        raw, headers = self.rt.provider.build_webhook(et, provider_reference,
                                                      amount_paid_paise=b.get("amount_paid_paise"),
                                                      event_id=b.get("event_id"), created_at=b.get("created_at"))
        results = []
        for _ in range(deliveries):  # each delivery goes through the real webhook handler (raw bytes + HMAC)
            r = self.handle(Request("POST", "/v1/webhooks/razorpay", headers=headers, body=raw, client="simulator"))
            results.append({"status": r.status, "body": json.loads(r.bytes())})
        return {"event_id": headers["x-razorpay-event-id"], "event_type": et, "deliveries": results}
