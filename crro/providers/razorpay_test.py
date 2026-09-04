"""
RazorpayTestAdapter
---------------------------------------------
Real Razorpay Test Mode integration via the official `razorpay` Python
SDK, behind the same RecoveryExecutionProvider interface as the
simulator. Uses Razorpay's Payment Links API
(https://razorpay.com/docs/api/payments/payment-links/) to create a
real, test-mode payment link a customer can pay.

Scope note (honest, not fabricated): this class was written against
the documented Razorpay Payment Links API and the official SDK, but
was NOT exercised against a live Razorpay Test Mode account in this
session -- no RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET were available in
this sandbox. Wire in real Test Mode credentials (never Live Mode) via
environment variables to use it; SimulatorAdapter remains the default
so the rest of the system runs without them.
"""

import hashlib
import hmac
import os
from typing import Optional

from .base import RecoveryExecutionProvider, ExecutionResult


class RazorpayCredentialsMissing(RuntimeError):
    pass


class RazorpayTestAdapter(RecoveryExecutionProvider):
    def __init__(self, key_id: Optional[str] = None, key_secret: Optional[str] = None,
                 webhook_secret: Optional[str] = None, timeout_seconds: float = 10.0):
        self.key_id = key_id or os.environ.get("RAZORPAY_KEY_ID")
        self.key_secret = key_secret or os.environ.get("RAZORPAY_KEY_SECRET")
        self.webhook_secret = webhook_secret or os.environ.get("RAZORPAY_WEBHOOK_SECRET")
        if not self.key_id or not self.key_secret:
            raise RazorpayCredentialsMissing(
                "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set. Use Test Mode keys only "
                "(they start with 'rzp_test_'). Falling back to SimulatorAdapter is usually "
                "what you want in this sandbox -- see crro/providers/factory.py."
            )
        if not self.key_id.startswith("rzp_test_"):
            raise RuntimeError(
                "Refusing to initialize: key_id does not look like a Test Mode key "
                "('rzp_test_...'). This adapter must never run against Live Mode."
            )
        import razorpay
        self.client = razorpay.Client(auth=(self.key_id, self.key_secret))
        self.timeout_seconds = timeout_seconds

    def create_payment_link(self, case_id, amount_inr, customer_id, idempotency_key, description=""):
        try:
            payload = {
                "amount": int(round(amount_inr * 100)),   # paise
                "currency": "INR",
                "description": description or f"Recovery for case {case_id}",
                "reference_id": idempotency_key,   # Razorpay dedupes on reference_id
                "notes": {"case_id": case_id, "customer_id": customer_id},
            }
            resp = self.client.payment_link.create(payload)
            return ExecutionResult(status="EXECUTED", provider_reference=resp.get("id"),
                                     provider_status=resp.get("status"))
        except Exception as e:
            cls = type(e).__name__
            if "Timeout" in cls or "timeout" in str(e).lower():
                return ExecutionResult(status="EXECUTION_TIMEOUT", provider_reference=None,
                                         provider_status=None, detail=str(e))
            return ExecutionResult(status="PROVIDER_ERROR", provider_reference=None,
                                     provider_status=None, detail=str(e))

    def get_payment_link_status(self, provider_reference):
        try:
            resp = self.client.payment_link.fetch(provider_reference)
            return ExecutionResult(status="EXECUTED", provider_reference=provider_reference,
                                     provider_status=resp.get("status"))
        except Exception as e:
            return ExecutionResult(status="PROVIDER_ERROR", provider_reference=provider_reference,
                                     provider_status=None, detail=str(e))

    def retry_payment(self, case_id, idempotency_key):
        return self.create_payment_link(case_id, amount_inr=0, customer_id="",
                                          idempotency_key=idempotency_key,
                                          description=f"Retry for case {case_id}")

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        """
        Verify using the RAW request body -- never
        the re-serialized/parsed JSON, which can differ byte-for-byte.
        """
        if not self.webhook_secret:
            raise RazorpayCredentialsMissing("RAZORPAY_WEBHOOK_SECRET not set.")
        expected = hmac.new(self.webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
