"""
SimulatorAdapter
-----------------------------------------
Deterministic, no-network implementation of RecoveryExecutionProvider,
used for local tests and offline benchmark runs (run_demo.py). Same
interface as RazorpayTestAdapter, so switching between them is a
one-line change in crro/providers/factory.py.
"""

import hashlib
import hmac
import uuid

import numpy as np

from .base import RecoveryExecutionProvider, ExecutionResult

SIMULATOR_WEBHOOK_SECRET = "simulator_dev_secret"   # not a real secret; local tests only


class SimulatorAdapter(RecoveryExecutionProvider):
    def __init__(self, seed=42, timeout_rate=0.01, provider_error_rate=0.015):
        self.rng = np.random.default_rng(seed)
        self.timeout_rate = timeout_rate
        self.provider_error_rate = provider_error_rate
        self._seen_idempotency_keys = {}   # key -> ExecutionResult

    def _simulate(self, idempotency_key, ref_prefix):
        if idempotency_key in self._seen_idempotency_keys:
            prior = self._seen_idempotency_keys[idempotency_key]
            return ExecutionResult(status="SKIPPED_DUPLICATE",
                                    provider_reference=prior.provider_reference,
                                    provider_status=prior.provider_status,
                                    detail="idempotency key already seen")
        r = self.rng.uniform()
        if r < self.timeout_rate:
            result = ExecutionResult(status="EXECUTION_TIMEOUT", provider_reference=None,
                                       provider_status=None)
        elif r < self.timeout_rate + self.provider_error_rate:
            result = ExecutionResult(status="PROVIDER_ERROR", provider_reference=None,
                                       provider_status=None)
        else:
            ref = f"{ref_prefix}_{uuid.uuid4().hex[:12]}"
            result = ExecutionResult(status="EXECUTED", provider_reference=ref,
                                       provider_status="created")
        self._seen_idempotency_keys[idempotency_key] = result
        return result

    def create_payment_link(self, case_id, amount_inr, customer_id, idempotency_key, description=""):
        return self._simulate(idempotency_key, "plink")

    def get_payment_link_status(self, provider_reference):
        return ExecutionResult(status="EXECUTED", provider_reference=provider_reference,
                                 provider_status="paid")

    def retry_payment(self, case_id, idempotency_key):
        return self._simulate(idempotency_key, "retry")

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        expected = hmac.new(SIMULATOR_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def sign_payload(self, raw_body: bytes) -> str:
        """Test helper: produce a valid signature for a given payload (used by tests/demo)."""
        return hmac.new(SIMULATOR_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
