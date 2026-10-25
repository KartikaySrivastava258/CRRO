"""
Provider Factory
------------------------------------------
Single place that decides which RecoveryExecutionProvider implementation
is live. Controlled by CRRO_PROVIDER=simulator|razorpay_test (defaults
to simulator so nothing here requires real credentials to run).

Returns a per-process SINGLETON: the simulator's idempotency ledger
(_seen_idempotency_keys) must persist across requests within one
running process, or "duplicate execution request" protection (Checklist
Section 13) would silently reset on every call. RazorpayTestAdapter is
idempotent server-side (via Razorpay's own reference_id dedup), so
singleton-vs-fresh doesn't matter for it, but we keep one instance
either way for consistency.
"""

import os

from .simulator import SimulatorAdapter

_provider_singleton = None


def get_provider():
    global _provider_singleton
    if _provider_singleton is not None:
        return _provider_singleton

    mode = os.environ.get("CRRO_PROVIDER", "simulator").lower()
    if mode == "razorpay_test":
        from .razorpay_test import RazorpayTestAdapter
        _provider_singleton = RazorpayTestAdapter()
    else:
        _provider_singleton = SimulatorAdapter(seed=int(os.environ.get("CRRO_SIMULATOR_SEED", "42")))
    return _provider_singleton


def reset_provider():
    """Test helper: force a fresh provider instance (e.g. between test modules)."""
    global _provider_singleton
    _provider_singleton = None
