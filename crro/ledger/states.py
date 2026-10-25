"""
Case lifecycle state machine (docs/ARCHITECTURE.md#lifecycle).

  OPEN ─┬─> NO_ACTION                         (decision: do nothing / blocked)
        ├─> PENDING_APPROVAL ─┬─> ACTION_REJECTED   (rejected or approval expired)
        │                     └─> ACTION_QUEUED
        └─> ACTION_QUEUED ─┬─> PROVIDER_PENDING     (provider accepted)
                           ├─> OUTCOME_UNKNOWN      (timeout: provider state unknown)
                           └─> ACTION_FAILED        (provider error / unsupported)
  OUTCOME_UNKNOWN ──> PROVIDER_PENDING | ACTION_FAILED | (any reconciliation outcome)
  PROVIDER_PENDING ──> RECOVERED | EXPIRED | STILL_FAILED | NEEDS_REVIEW
  EXPIRED ──> RECOVERED (late verified payment: funds moved) | NEEDS_REVIEW
  NEEDS_REVIEW ──> RECOVERED | EXPIRED | STILL_FAILED   (operator resolution)
  RECOVERED, NO_ACTION, ACTION_REJECTED, ACTION_FAILED, STILL_FAILED: terminal.

RECOVERED is terminal and can never be downgraded by a stale/out-of-order event.
"""
from __future__ import annotations

_RECON_OUTCOMES = {"RECOVERED", "EXPIRED", "STILL_FAILED", "NEEDS_REVIEW"}

ALLOWED = {
    "OPEN": {"NO_ACTION", "PENDING_APPROVAL", "ACTION_QUEUED"},
    "PENDING_APPROVAL": {"ACTION_QUEUED", "ACTION_REJECTED"},
    # A verified webhook can legitimately arrive before our post-dispatch write
    # commits (provider faster than our second transaction), hence recon outcomes.
    "ACTION_QUEUED": {"PROVIDER_PENDING", "OUTCOME_UNKNOWN", "ACTION_FAILED"} | _RECON_OUTCOMES,
    "OUTCOME_UNKNOWN": {"PROVIDER_PENDING", "ACTION_FAILED"} | _RECON_OUTCOMES,
    "PROVIDER_PENDING": set(_RECON_OUTCOMES),
    "EXPIRED": {"RECOVERED", "NEEDS_REVIEW"},
    "NEEDS_REVIEW": {"RECOVERED", "EXPIRED", "STILL_FAILED"},
    "RECOVERED": set(),
    "NO_ACTION": set(),
    "ACTION_REJECTED": set(),
    "ACTION_FAILED": set(),
    "STILL_FAILED": set(),
}

TERMINAL = {s for s, nxt in ALLOWED.items() if not nxt}


class InvalidTransition(Exception):
    def __init__(self, current: str, target: str):
        super().__init__(f"invalid case transition {current} -> {target}")
        self.current, self.target = current, target


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED.get(current, set())


def assert_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
