"""
PostgreSQL-Backed Customer State Store
------------------------------------------------------------------------
Same interface as crro/state.py's in-memory CustomerStateStore, so
callers (crro/feasibility.py, api.py) don't need to know which backend
they're talking to. Unlike the in-memory version, state here genuinely
persists across process restarts -- this is what makes cooldown /
max-attempts / contact-frequency enforcement real rather than a
per-run simulation artifact.
"""

from datetime import datetime, timedelta, timezone
from sqlalchemy import func
from .db import get_session
from .db_models import Customer, CustomerActionHistory, PaymentCase


class PostgresCustomerStateStore:
    def __init__(self, session_factory=get_session):
        self._session_factory = session_factory

    def is_opted_out(self, customer_id) -> bool:
        s = self._session_factory()
        try:
            cust = s.get(Customer, customer_id)
            return bool(cust and cust.opted_out)
        finally:
            s.close()

    def set_opted_out(self, customer_id, opted_out=True):
        s = self._session_factory()
        try:
            cust = s.get(Customer, customer_id)
            if cust is None:
                cust = Customer(customer_id=customer_id)
                s.add(cust)
            cust.opted_out = opted_out
            cust.opted_out_at = datetime.now(timezone.utc) if opted_out else None
            s.commit()
        finally:
            s.close()

    def attempts_used(self, case_id) -> int:
        s = self._session_factory()
        try:
            n = (s.query(func.count(CustomerActionHistory.record_id))
                  .filter(CustomerActionHistory.case_id == case_id,
                          CustomerActionHistory.status.in_(("PROPOSED", "EXECUTED")))
                  .scalar())
            return int(n or 0)
        finally:
            s.close()

    def contacts_used_today(self, customer_id) -> int:
        s = self._session_factory()
        try:
            since = datetime.now(timezone.utc) - timedelta(days=1)
            n = (s.query(func.count(CustomerActionHistory.record_id))
                  .filter(CustomerActionHistory.customer_id == customer_id,
                          CustomerActionHistory.status == "EXECUTED",
                          CustomerActionHistory.timestamp >= since)
                  .scalar())
            return int(n or 0)
        finally:
            s.close()

    def in_cooldown(self, customer_id, action, min_cooldown_minutes) -> bool:
        s = self._session_factory()
        try:
            last = (s.query(func.max(CustomerActionHistory.timestamp))
                     .filter(CustomerActionHistory.customer_id == customer_id,
                             CustomerActionHistory.action == action)
                     .scalar())
            if last is None:
                return False
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - last) < timedelta(minutes=min_cooldown_minutes)
        finally:
            s.close()

    def record(self, customer_id, case_id, action, status, provider_reference=None):
        s = self._session_factory()
        try:
            if s.get(Customer, customer_id) is None:
                s.add(Customer(customer_id=customer_id))
                s.flush()
            rec = CustomerActionHistory(
                customer_id=customer_id, case_id=case_id, action=action,
                status=status, provider_reference=provider_reference,
            )
            s.add(rec)
            s.commit()
        finally:
            s.close()

    def history_for_customer(self, customer_id, limit=50):
        s = self._session_factory()
        try:
            rows = (s.query(CustomerActionHistory)
                     .filter(CustomerActionHistory.customer_id == customer_id)
                     .order_by(CustomerActionHistory.timestamp.desc())
                     .limit(limit).all())
            return [
                {"case_id": r.case_id, "action": r.action, "status": r.status,
                 "timestamp": r.timestamp.isoformat()} for r in rows
            ]
        finally:
            s.close()
