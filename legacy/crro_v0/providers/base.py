"""
RecoveryExecutionProvider Interface
------------------------------------------------------------
        RecoveryExecutionProvider
                |
                +-- RazorpayTestAdapter   (crro/providers/razorpay_test.py)
                |
                +-- SimulatorAdapter      (crro/providers/simulator.py)

Any code executing a recovery action (crro/workflow.py, api.py) talks
to this interface only -- it never imports razorpay or the simulator
directly, so swapping between "real Test Mode demo" and "deterministic
local tests" is a one-line change (see crro/providers/factory.py).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutionResult:
    status: str                       # EXECUTED | PROVIDER_ERROR | EXECUTION_TIMEOUT | SKIPPED_DUPLICATE
    provider_reference: Optional[str] # e.g. Razorpay payment_link_id
    provider_status: Optional[str]    # raw status string from the provider, if any
    detail: Optional[str] = None


class RecoveryExecutionProvider(ABC):
    @abstractmethod
    def create_payment_link(self, case_id: str, amount_inr: float, customer_id: str,
                              idempotency_key: str, description: str = "") -> ExecutionResult:
        """Action: PAYMENT_LINK. Must be idempotent on idempotency_key."""

    @abstractmethod
    def get_payment_link_status(self, provider_reference: str) -> ExecutionResult:
        """Retrieve current status of a previously created payment link."""

    @abstractmethod
    def retry_payment(self, case_id: str, idempotency_key: str) -> ExecutionResult:
        """Action: RETRY_NOW / RETRY_LATER."""

    @abstractmethod
    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        """HMAC verification of an inbound webhook using the raw request body."""
