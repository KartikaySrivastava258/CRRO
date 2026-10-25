"""FastAPI transport test. Requires fastapi + httpx (installed in CI via
requirements-dev.txt). Skips ONLY when FastAPI is not importable AND
CRRO_REQUIRE_FASTAPI is unset; CI sets CRRO_REQUIRE_FASTAPI=1 so it must run there."""
import json
import os
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
    HAVE_FASTAPI = True
except ImportError:  # offline sandbox used to build this version
    HAVE_FASTAPI = False

REQUIRED = os.environ.get("CRRO_REQUIRE_FASTAPI") == "1"


@unittest.skipIf(not HAVE_FASTAPI and not REQUIRED, "fastapi not installed (set CRRO_REQUIRE_FASTAPI=1 to fail instead)")
class TestFastApiAdapter(unittest.TestCase):
    def test_raw_body_webhook_and_auth_through_fastapi(self):
        d = tempfile.mkdtemp()
        os.environ.update(CRRO_DEMO_MODE="1", CRRO_DATABASE_URL=f"sqlite:///{d}/fa.db", CRRO_MODEL_DIR=f"{d}/model",
                          CRRO_SIMULATOR_WEBHOOK_SECRET="fastapi_test_secret")
        from crro.decision.model_store import train_and_save
        train_and_save(n_train=4000, n_holdout=500, out_dir=f"{d}/model")
        import api
        api._http = None
        with TestClient(api.app) as c:
            self.assertEqual(c.get("/health/ready").status_code, 200)
            self.assertEqual(c.get("/v1/overview").status_code, 401)
            h = {"Authorization": "Bearer demo-operator-token-local-only", "Idempotency-Key": "fa1"}
            r = c.post("/v1/events/payment-failed", headers=h, json={
                "external_payment_id": "fa_pay", "customer_ref": "fa_c", "amount_inr": "2500.00",
                "payment_method": "card", "failure_reason": "card_expired"})
            self.assertEqual(r.status_code, 201, r.text)
            case = c.get(f"/v1/cases/{r.json()['case_id']}", headers=h).json()
            raw, wh = api.get_http().rt.provider.build_webhook("payment_link.paid", case["attempt"]["provider_reference"])
            ok = c.post("/v1/webhooks/razorpay", content=raw, headers=wh)
            self.assertEqual((ok.status_code, ok.json()["outcome"]), (200, "RECOVERED"))
            pretty = json.dumps(json.loads(raw), indent=2).encode()     # same JSON, different bytes
            self.assertEqual(c.post("/v1/webhooks/razorpay", content=pretty, headers=wh).status_code, 401)


if __name__ == "__main__":
    unittest.main()
