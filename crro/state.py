"""
Customer Action History / State Model (Required Changes, Section 8)
------------------------------------------------------------------------
An in-memory table shaped exactly like the target `customer_action_history`
persistence table (customer_id, case_id, action, timestamp, status,
provider_reference). This is intentionally a plain, swappable data
structure: replacing this class's storage with a PostgreSQL-backed
repository (see `crro/db_schema.sql`) requires no change to callers.

Enforces, at DECISION time (before optimization, not just after):
  - max attempts per case
  - cooldown per (customer, action)
  - daily contact-frequency cap per customer
  - customer opt-out
"""

from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass
class ActionHistoryRecord:
    customer_id: str
    case_id: str
    action: str
    timestamp: float
    status: str                       # PROPOSED | EXECUTED | BLOCKED | FAILED
    provider_reference: Optional[str] = None


class CustomerStateStore:
    """In-memory stand-in for the `customer_action_history` table."""

    def __init__(self, opted_out_customers=None):
        self._records: list[ActionHistoryRecord] = []
        self._case_attempts: dict[str, int] = {}          # case_id -> count
        self._customer_contacts_today: dict[str, int] = {}  # customer_id -> count
        self.opted_out_customers = set(opted_out_customers or [])

    def record(self, customer_id, case_id, action, status, provider_reference=None):
        rec = ActionHistoryRecord(customer_id, case_id, action, time.time(),
                                   status, provider_reference)
        self._records.append(rec)
        if status in ("PROPOSED", "EXECUTED"):
            self._case_attempts[case_id] = self._case_attempts.get(case_id, 0) + 1

    def mark_contacted(self, customer_id):
        self._customer_contacts_today[customer_id] = \
            self._customer_contacts_today.get(customer_id, 0) + 1

    def attempts_used(self, case_id) -> int:
        return self._case_attempts.get(case_id, 0)

    def contacts_used_today(self, customer_id) -> int:
        return self._customer_contacts_today.get(customer_id, 0)

    def is_opted_out(self, customer_id) -> bool:
        return customer_id in self.opted_out_customers

    def last_action_time(self, customer_id, action) -> Optional[float]:
        matches = [r.timestamp for r in self._records
                   if r.customer_id == customer_id and r.action == action]
        return max(matches) if matches else None

    def in_cooldown(self, customer_id, action, min_cooldown_minutes, now=None) -> bool:
        last = self.last_action_time(customer_id, action)
        if last is None:
            return False
        now = now if now is not None else time.time()
        return (now - last) < min_cooldown_minutes * 60

    def to_records(self):
        return [r.__dict__ for r in self._records]
