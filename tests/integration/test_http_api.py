"""
End-to-end HTTP tests: real sockets against scripts/serve.py's transport,
the real HttpApp, ledger, decision engine and simulated provider.
Webhooks are posted as raw signed bytes to /v1/webhooks/razorpay.
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from crro.api.http import HttpApp
from crro.decision.model_store import load, train_and_save
from crro.ledger.providers import hmac_sha256_hex
from crro.runtime import DEMO_TOKENS, RecoveryRuntime, Settings
from scripts.serve import make_handler

OP, VIEW, OTHER = "demo-operator-token-local-only", "demo-viewer-token-local-only", "demo-other-merchant-token-local"
SECRET = "http_test_secret"
_MODEL = {}


def shared_model():
    if not _MODEL:
        d = tempfile.mkdtemp()
        train_and_save(n_train=8000, n_holdout=1000, out_dir=d)
        _MODEL["m"], _MODEL["card"] = load(d)
    return _MODEL["m"], _MODEL["card"]


class ApiServer:
    def __init__(self):
        d = tempfile.mkdtemp()
        m, card = shared_model()
        self.rt = RecoveryRuntime(Settings(database_url=f"sqlite:///{d}/api.db", simulator_webhook_secret=SECRET,
                                           operator_tokens=dict(DEMO_TOKENS)), model=m, card=card)
        self.app = HttpApp(self.rt)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def call(self, method, path, body=None, token=OP, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = {"Content-Type": "application/json", **(headers or {})}
        if token:
            h["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                payload, status, hdrs = r.read(), r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            payload, status, hdrs = e.read(), e.code, dict(e.headers)
        try:
            return status, json.loads(payload), hdrs
        except ValueError:
            return status, payload.decode(), hdrs


def pf(ext, amount="2500.00", **kw):
    b = {"external_payment_id": ext, "customer_ref": kw.pop("customer_ref", "cust_" + ext), "amount_inr": amount,
         "payment_method": "card", "failure_reason": "card_expired", "attempt_count": 1}
    b.update(kw)
    return b


class TestHttpApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = ApiServer()

    @classmethod
    def tearDownClass(cls):
        cls.s.close()

    def submit(self, ext, **kw):
        st, body, h = self.s.call("POST", "/v1/events/payment-failed", pf(ext, **kw), headers={"Idempotency-Key": "k-" + ext})
        self.assertIn(st, (200, 201), body)
        return body

    def case(self, case_id, token=OP):
        return self.s.call("GET", f"/v1/cases/{case_id}", token=token)

    def test_health_and_metrics(self):
        self.assertEqual(self.s.call("GET", "/health/live", token=None)[0], 200)
        st, b, _ = self.s.call("GET", "/health/ready", token=None)
        self.assertEqual((st, b["database"], b["model_loaded"]), (200, True, True))
        st, text, _ = self.s.call("GET", "/metrics", token=None)
        self.assertIn("crro_http_requests_total", text)

    def test_auth_roles_and_merchant_isolation(self):
        self.assertEqual(self.s.call("GET", "/v1/overview", token=None)[0], 401)
        self.assertEqual(self.s.call("GET", "/v1/overview", token="nope")[0], 401)
        self.assertEqual(self.s.call("GET", "/v1/overview", token=VIEW)[0], 200)
        st, b, _ = self.s.call("POST", "/v1/events/payment-failed", pf("v1"), token=VIEW, headers={"Idempotency-Key": "kv"})
        self.assertEqual((st, b["error"]["code"]), (403, "forbidden"))
        body = self.submit("iso1")
        self.assertEqual(self.case(body["case_id"], token=OTHER)[0], 404)
        self.assertEqual(self.case(body["case_id"])[0], 200)
        st, b, _ = self.s.call("GET", "/v1/cases", token=OTHER)
        self.assertNotIn(body["case_id"], [c["case_id"] for c in b["items"]])

    def test_payment_failed_idempotency_over_http(self):
        st, b1, h1 = self.s.call("POST", "/v1/events/payment-failed", pf("idem1"), headers={"Idempotency-Key": "K1"})
        st2, b2, h2 = self.s.call("POST", "/v1/events/payment-failed", pf("idem1"), headers={"Idempotency-Key": "K1"})
        self.assertEqual((st, h1["Idempotent-Replayed"], st2, h2["Idempotent-Replayed"]), (201, "false", 201, "true"))
        self.assertEqual(b1["case_id"], b2["case_id"])
        st3, b3, _ = self.s.call("POST", "/v1/events/payment-failed", pf("idem1", amount="1.00"),
                                 headers={"Idempotency-Key": "K1"})
        self.assertEqual((st3, b3["error"]["code"]), (409, "idempotency_key_reused_with_different_payload"))
        self.assertEqual(self.s.call("POST", "/v1/events/payment-failed", pf("idem2"))[0], 400)
        st4, b4, _ = self.s.call("POST", "/v1/events/payment-failed", pf("idem3", amount="-5"),
                                 headers={"Idempotency-Key": "K3"})
        self.assertEqual(st4, 422)
        self.assertEqual(self.s.call("POST", "/v1/events/payment-failed", raw=b"{bad", headers={"Idempotency-Key": "K4"})[0], 400)

    def test_full_lifecycle_with_signed_webhook(self):
        body = self.submit("life1", amount="4999.50")
        st, d, _ = self.case(body["case_id"])
        self.assertEqual(d["case"]["status"], "PROVIDER_PENDING", d["decision"])
        ref = d["attempt"]["provider_reference"]
        raw, h = self.s.rt.provider.build_webhook("payment_link.paid", ref)
        self.assertEqual(self.s.call("POST", "/v1/webhooks/razorpay", raw=raw, token=None,
                                     headers={**h, "x-razorpay-signature": "0" * 64})[0], 401)
        st, out, _ = self.s.call("POST", "/v1/webhooks/razorpay", raw=raw, token=None, headers=h)
        self.assertEqual((st, out["outcome"]), (200, "RECOVERED"))
        st, dup, _ = self.s.call("POST", "/v1/webhooks/razorpay", raw=raw, token=None, headers=h)
        self.assertEqual(dup["status"], "duplicate_ignored")
        d = self.case(body["case_id"])[1]
        self.assertEqual(d["recovery"]["cash_collected_paise"], d["attempt"]["collect_amount_paise"])
        types = [t["event_type"] for t in d["timeline"]]
        self.assertEqual(types[0], "PAYMENT_FAILED_RECEIVED")
        self.assertIn("DECISION_RECORDED", types)
        st, aud, _ = self.s.call("GET", f"/v1/audit?case_id={body['case_id']}")
        self.assertEqual({a["correlation_id"] for a in aud["items"]}, {d["case"]["correlation_id"]})

    def test_simulator_endpoint_uses_real_webhook_path(self):
        body = self.submit("sim1")
        ref = self.case(body["case_id"])[1]["attempt"]["provider_reference"]
        st, out, _ = self.s.call("POST", f"/v1/simulator/payment-links/{ref}/events",
                                 {"event_type": "payment_link.paid", "deliveries": 3})
        statuses = [x["body"]["status"] for x in out["deliveries"]]
        self.assertEqual(statuses, ["processed", "duplicate_ignored", "duplicate_ignored"])
        st, ov, _ = self.s.call("GET", "/v1/overview")
        self.assertEqual(ov["confirmed_recovered_by_provider_mode"]["SIMULATOR"]["cases"],
                         ov["funnel"]["recovered"])
        self.assertEqual(self.s.call("POST", f"/v1/simulator/payment-links/{ref}/events", {"event_type": "x"})[0], 422)
        self.assertEqual(self.s.call("POST", f"/v1/simulator/payment-links/{ref}/events", {}, token=OTHER)[0], 404)

    def test_approval_flow_over_http(self):
        # Deterministic: every action needs approval above ₹1,000 for merchant_other.
        thr = {a: 100_000 for a in ("RETRY_NOW", "RETRY_LATER", "PAYMENT_LINK", "ALTERNATIVE_PAYMENT_METHOD",
                                    "CUSTOMER_NUDGE", "SMALL_INCENTIVE")}
        self.assertEqual(self.s.call("PATCH", "/v1/policy", {"approval_threshold_paise": thr}, token=OTHER)[0], 200)
        st, body, _ = self.s.call("POST", "/v1/events/payment-failed", pf("big1", amount="9000.00"), token=OTHER,
                                  headers={"Idempotency-Key": "kbig1"})
        d = self.case(body["case_id"], token=OTHER)[1]
        self.assertEqual(d["case"]["status"], "PENDING_APPROVAL", d["decision"]["selected_action"])
        apr = d["approval"]["approval_id"]
        st, q, _ = self.s.call("GET", "/v1/approvals", token=OTHER)
        self.assertIn(apr, [a["approval_id"] for a in q["items"]])
        self.assertEqual(self.s.call("POST", f"/v1/approvals/{apr}/decision", {"decision": "APPROVE"})[0], 404)
        st, b, _ = self.s.call("POST", f"/v1/approvals/{apr}/decision", {"decision": "APPROVE", "note": "verified"},
                               token=OTHER)
        self.assertEqual((st, b["replayed"], b["attempt"]["status"]), (200, False, "PROVIDER_ACCEPTED"))
        st, b, _ = self.s.call("POST", f"/v1/approvals/{apr}/decision", {"decision": "APPROVE", "note": "verified"},
                               token=OTHER)
        self.assertEqual((st, b["replayed"]), (200, True))
        self.assertEqual(self.s.call("POST", f"/v1/approvals/{apr}/decision", {"decision": "REJECT", "note": "late"},
                                     token=OTHER)[0], 409)
        self.assertEqual(self.s.call("POST", f"/v1/approvals/{apr}/decision", {"decision": "MAYBE"}, token=OTHER)[0], 422)
        st, b, _ = self.s.call("POST", f"/v1/approvals/{apr}/decision", {"decision": "APPROVE"}, token=VIEW)
        self.assertEqual(st, 403)

    def test_provider_timeout_then_resolve(self):
        st, body, _ = self.s.call("POST", "/v1/events/payment-failed", pf("to1"),
                                  headers={"Idempotency-Key": "kto1", "X-Simulator-Fault": "timeout_after"})
        d = self.case(body["case_id"])[1]
        self.assertEqual(d["case"]["status"], "OUTCOME_UNKNOWN")
        before = self.s.rt.provider.count_objects()
        st, att, _ = self.s.call("POST", f"/v1/attempts/{d['attempt']['attempt_id']}/resolve")
        self.assertEqual((st, att["status"]), (200, "PROVIDER_ACCEPTED"))
        self.assertEqual(self.s.rt.provider.count_objects(), before)        # no duplicate link
        self.assertEqual(self.case(body["case_id"])[1]["case"]["status"], "PROVIDER_PENDING")

    def test_policy_validation_and_opt_out_guardrail(self):
        self.assertEqual(self.s.call("PATCH", "/v1/policy", {"daily_contact_budget": -3})[0], 422)
        st, pol, _ = self.s.call("PATCH", "/v1/policy", {"approval_ttl_hours": 48})
        self.assertEqual((st, pol["approval_ttl_hours"]), (200, 48))
        self.assertEqual(self.s.call("POST", "/v1/customers/cust_opt/opt-out", {"opted_out": True})[0], 200)
        body = self.submit("opt1", customer_ref="cust_opt")
        d = self.case(body["case_id"])[1]
        for c in d["decision"]["candidates"]:
            if c["requires_customer_contact"]:
                self.assertIn("CUSTOMER_OPTED_OUT", c["rejection_reasons"])

    def test_optimizer_run_and_exports(self):
        st, run, _ = self.s.call("POST", "/v1/optimizer/runs", {"n_cases": 300, "seed": 7, "contact_budget": 30})
        self.assertEqual((st, run["status"]), (201, "SUCCEEDED"), run)
        res = run["result"]
        self.assertEqual(res["data_label"], "SYNTHETIC_SIMULATION")
        self.assertIn(res["solver_usage"]["CRRO_EXACT"]["status"], ("OPTIMAL", "FEASIBLE_TIME_LIMIT", "EMPTY"))
        for p in res["policies"]:
            if p["constrained"]:
                self.assertEqual(p["feasibility_violations"], [])
        st, csv, h = self.s.call("GET", f"/v1/optimizer/runs/{run['run_id']}/policies.csv")
        self.assertTrue(csv.startswith("# run_id=") and "SYNTHETIC_SIMULATION" in csv)
        self.assertEqual(self.s.call("POST", "/v1/optimizer/runs", {"n_cases": 10})[0], 422)
        self.assertEqual(self.s.call("GET", f"/v1/optimizer/runs/{run['run_id']}", token=OTHER)[0], 404)
        st, exp, h = self.s.call("GET", "/v1/audit/export")
        self.assertEqual(st, 200)
        self.assertIn("attachment", h["Content-Disposition"])

    def test_rate_limit_cors_request_id_and_errors(self):
        codes = [self.s.call("POST", "/v1/attempts/att_nope/resolve")[0] for _ in range(12)]
        self.assertIn(429, codes)
        self.assertEqual(codes[0], 404)
        st, _, h = self.s.call("GET", "/v1/me", headers={"Origin": "http://localhost:5173", "X-Request-ID": "trace_abc12345"})
        self.assertEqual((h.get("Access-Control-Allow-Origin"), h["X-Request-ID"]), ("http://localhost:5173", "trace_abc12345"))
        st, _, h = self.s.call("GET", "/v1/me", headers={"Origin": "https://evil.example"})
        self.assertNotIn("Access-Control-Allow-Origin", h)
        st, b, _ = self.s.call("DELETE", "/v1/overview")
        self.assertEqual((st, b["error"]["code"]), (405, "method_not_allowed"))
        st, b, _ = self.s.call("GET", "/v1/nope")
        self.assertEqual(st, 404)
        self.assertIn("request_id", b)
        self.assertEqual(self.s.call("GET", "/v1/cases?limit=9999")[0], 422)
        self.assertEqual(self.s.call("GET", "/v1/cases?cursor=%%%")[0], 400)

    def test_oversized_body_rejected(self):
        st, b, _ = self.s.call("POST", "/v1/events/payment-failed", raw=b"{" + b" " * (300 * 1024) + b"}",
                               headers={"Idempotency-Key": "big"})
        self.assertEqual(st, 413)


class TestStaticServing(unittest.TestCase):
    def test_path_traversal_blocked(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, "index.html"), "w").write("<html>ok</html>")
        m, card = shared_model()
        rt = RecoveryRuntime(Settings(database_url=f"sqlite:///{tempfile.mkdtemp()}/s.db",
                                      operator_tokens=dict(DEMO_TOKENS)), model=m, card=card)
        app = HttpApp(rt, d)
        from crro.api.http import Request
        self.assertEqual(app.handle(Request("GET", "/")).status, 200)
        r = app.handle(Request("GET", "/../../etc/passwd"))
        self.assertTrue(r.status == 404 or b"root:" not in r.bytes())


if __name__ == "__main__":
    unittest.main()
