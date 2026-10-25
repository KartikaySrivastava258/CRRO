"""
Recovery execution providers for the ledger.

Contract: every create call carries a stable idempotency key, which is also
sent as the provider-side `reference_id`. The provider must either dedupe on
it or let us look the object up by it, so that a retry after an ambiguous
timeout cannot create a second payment link (at-least-once dispatch +
idempotent effect -- NOT exactly-once delivery).

SimulatedProvider keeps its objects in its own table (sim_provider_objects),
committed in a transaction separate from the ledger's, to model a remote
system: an object can exist at the "provider" even when our ledger recorded a
timeout. This is what makes the timeout-after-success test meaningful.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .db import Database, loads, utcnow_iso


@dataclass
class ProviderResult:
    status: str                      # ACCEPTED | ERROR | TIMEOUT | UNSUPPORTED
    provider_reference: Optional[str] = None
    provider_status: Optional[str] = None
    detail: Optional[str] = None
    duplicate: bool = False


def hmac_sha256_hex(secret: str, raw: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def verify_signature(secret: str, raw_body: bytes, signature) -> bool:
    """Constant-time HMAC-SHA256 check over the exact raw bytes received."""
    if not secret or not isinstance(signature, str) or not signature:
        return False
    try:
        sig_bytes = signature.strip().encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(hmac_sha256_hex(secret, raw_body).encode("ascii"), sig_bytes)


class SimulatedProvider:
    name = "simulator"
    mode = "SIMULATOR"

    def __init__(self, db: Database, webhook_secret: str, faults: Optional[dict] = None):
        self.db = db
        self.webhook_secret = webhook_secret
        # idempotency_key -> list of fault modes consumed one per call:
        #   "error", "timeout_before" (nothing created), "timeout_after" (created, caller sees timeout)
        self.faults = faults if faults is not None else {}
        self.calls = 0

    def _next_fault(self, key):
        modes = self.faults.get(key) or []
        return modes.pop(0) if modes else None

    def _create(self, kind, idempotency_key, amount_paise, currency, notes) -> ProviderResult:
        self.calls += 1
        fault = self._next_fault(idempotency_key)
        if fault == "error":
            return ProviderResult("ERROR", detail="simulated provider 5xx")
        if fault == "timeout_before":
            return ProviderResult("TIMEOUT", detail="simulated timeout before provider commit")
        with self.db.transaction() as tx:
            existing = tx.one("SELECT * FROM sim_provider_objects WHERE reference_id = ?", [idempotency_key])
            if existing:
                res = ProviderResult("ACCEPTED", existing["provider_reference"], existing["status"],
                                     duplicate=True)
            else:
                ref = ("plink_" if kind == "payment_link" else "rtry_") + uuid.uuid4().hex[:14]
                tx.execute("INSERT INTO sim_provider_objects (provider_reference, kind, reference_id, amount_paise,"
                           " currency, status, notes, created_at) VALUES (?,?,?,?,?,?,?,?)",
                           [ref, kind, idempotency_key, int(amount_paise), currency, "created", notes, utcnow_iso()])
                res = ProviderResult("ACCEPTED", ref, "created")
        if fault == "timeout_after":
            return ProviderResult("TIMEOUT", detail="simulated timeout after provider commit")
        return res

    def create_payment_link(self, idempotency_key, amount_paise, currency, notes) -> ProviderResult:
        return self._create("payment_link", idempotency_key, amount_paise, currency, notes)

    def retry_payment(self, idempotency_key, amount_paise, currency, notes) -> ProviderResult:
        return self._create("retry", idempotency_key, amount_paise, currency, notes)

    def lookup(self, idempotency_key) -> Optional[ProviderResult]:
        with self.db.transaction(write=False) as tx:
            row = tx.one("SELECT * FROM sim_provider_objects WHERE reference_id = ?", [idempotency_key])
        if not row:
            return None
        return ProviderResult("ACCEPTED", row["provider_reference"], row["status"])

    def object(self, provider_reference) -> Optional[dict]:
        with self.db.transaction(write=False) as tx:
            row = tx.one("SELECT * FROM sim_provider_objects WHERE provider_reference = ?", [provider_reference])
        if row:
            row["notes"] = loads(row["notes"])
        return row

    def count_objects(self) -> int:
        with self.db.transaction(write=False) as tx:
            return int(tx.scalar("SELECT COUNT(*) AS n FROM sim_provider_objects"))

    def verify_webhook_signature(self, raw_body: bytes, signature) -> bool:
        return verify_signature(self.webhook_secret, raw_body, signature)

    # --- test/demo helpers: build Razorpay-shaped signed webhooks -----------------
    def build_webhook(self, event_type: str, provider_reference: str, *, amount_paid_paise=None,
                      currency=None, event_id=None, created_at=None, payment_id=None,
                      merchant_id_override=None):
        obj = self.object(provider_reference)
        if obj is None:  # unknown reference: still produce a structurally valid event
            obj = {"provider_reference": provider_reference, "kind": "payment_link",
                   "reference_id": "unknown", "amount_paise": amount_paid_paise or 100,
                   "currency": currency or "INR", "notes": {}}
        notes = dict(obj["notes"] or {})
        if merchant_id_override:
            notes["merchant_id"] = merchant_id_override
        amount = obj["amount_paise"] if amount_paid_paise is None else amount_paid_paise
        cur = currency or obj["currency"]
        pay_id = payment_id or ("pay_" + hashlib.sha256(provider_reference.encode()).hexdigest()[:14])
        created = int(created_at if created_at is not None else time.time())
        payment = {"entity": {"id": pay_id, "entity": "payment", "amount": amount, "currency": cur,
                              "status": "captured" if event_type in ("payment_link.paid", "payment.captured")
                              else "failed", "notes": notes}}
        if obj["kind"] == "payment_link" or event_type.startswith("payment_link."):
            status = {"payment_link.paid": "paid", "payment_link.expired": "expired",
                      "payment_link.cancelled": "cancelled",
                      "payment_link.partially_paid": "partially_paid"}.get(event_type, "created")
            body = {"entity": "event", "event": event_type,
                    "contains": ["payment_link", "payment"] if event_type.endswith("paid") else ["payment_link"],
                    "payload": {"payment_link": {"entity": {
                        "id": provider_reference, "entity": "payment_link", "amount": obj["amount_paise"],
                        "amount_paid": amount if event_type.endswith("paid") else 0,
                        "currency": cur, "reference_id": obj["reference_id"], "status": status,
                        "notes": notes}}},
                    "created_at": created}
            if event_type.endswith("paid"):
                body["payload"]["payment"] = payment
        else:
            payment["entity"]["notes"] = {**notes, "crro_reference_id": obj["reference_id"],
                                          "crro_provider_reference": provider_reference}
            body = {"entity": "event", "event": event_type, "contains": ["payment"],
                    "payload": {"payment": payment}, "created_at": created}
        raw = json.dumps(body, separators=(",", ":")).encode()
        headers = {"x-razorpay-signature": hmac_sha256_hex(self.webhook_secret, raw),
                   "x-razorpay-event-id": event_id or ("evt_" + uuid.uuid4().hex[:16]),
                   "content-type": "application/json"}
        return raw, headers


class RazorpayTestModeProvider:
    """Razorpay Payment Links (Test Mode only) over stdlib HTTP.

    NOT exercised against a real Razorpay account in this repository's test
    runs (no credentials available); covered by unit tests with a fake opener.
    Retries of a failed card/UPI payment are not something a merchant can
    trigger server-side via Payment Links, so retry_payment is UNSUPPORTED.
    """
    name = "razorpay"
    mode = "TEST_MODE"
    BASE = "https://api.razorpay.com/v1"

    def __init__(self, key_id=None, key_secret=None, webhook_secret=None, timeout_s=8.0, opener=None):
        self.key_id = key_id or os.environ.get("RAZORPAY_KEY_ID", "")
        self.key_secret = key_secret or os.environ.get("RAZORPAY_KEY_SECRET", "")
        self.webhook_secret = webhook_secret or os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
        if not (self.key_id and self.key_secret and self.webhook_secret):
            raise RuntimeError("RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET and RAZORPAY_WEBHOOK_SECRET are required")
        if not self.key_id.startswith("rzp_test_"):
            raise RuntimeError("refusing non-test Razorpay key: only rzp_test_ keys are allowed")
        self.timeout_s = timeout_s
        self._open = opener or urllib.request.urlopen

    def _req(self, method, path, body=None):
        import base64
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.BASE + path, data=data, method=method)
        token = base64.b64encode(f"{self.key_id}:{self.key_secret}".encode()).decode()
        req.add_header("Authorization", "Basic " + token)
        req.add_header("Content-Type", "application/json")
        with self._open(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read().decode())

    def create_payment_link(self, idempotency_key, amount_paise, currency, notes) -> ProviderResult:
        payload = {"amount": int(amount_paise), "currency": currency, "accept_partial": False,
                   "reference_id": idempotency_key, "description": "Payment recovery",
                   "notify": {"sms": False, "email": False}, "reminder_enable": False,
                   "notes": {k: str(v) for k, v in notes.items()}}
        try:
            resp = self._req("POST", "/payment_links", payload)
            return ProviderResult("ACCEPTED", resp.get("id"), resp.get("status"))
        except TimeoutError as e:
            return ProviderResult("TIMEOUT", detail=type(e).__name__)
        except urllib.error.HTTPError as e:
            # Razorpay rejects a reused reference_id; treat as "may already exist" -> lookup path.
            return ProviderResult("TIMEOUT" if e.code == 400 else "ERROR", detail=f"HTTP {e.code}")
        except urllib.error.URLError as e:
            timeout = "timed out" in str(e.reason).lower()
            return ProviderResult("TIMEOUT" if timeout else "ERROR", detail=type(e.reason).__name__)

    def retry_payment(self, idempotency_key, amount_paise, currency, notes) -> ProviderResult:
        return ProviderResult("UNSUPPORTED", detail="server-side retry is not available via Payment Links")

    def lookup(self, idempotency_key) -> Optional[ProviderResult]:
        q = urllib.parse.urlencode({"reference_id": idempotency_key})
        resp = self._req("GET", f"/payment_links?{q}")
        items = resp.get("payment_links") or resp.get("items") or []
        if not items:
            return None
        return ProviderResult("ACCEPTED", items[0].get("id"), items[0].get("status"))

    def verify_webhook_signature(self, raw_body: bytes, signature) -> bool:
        return verify_signature(self.webhook_secret, raw_body, signature)
