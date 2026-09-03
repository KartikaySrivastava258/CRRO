from datetime import datetime, timezone, timedelta
from sqlalchemy import String, Integer, Float, Boolean, DateTime, select
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base
from .action_registry import ACTION_REGISTRY


class CustomerRecoveryState(Base):
    __tablename__ = 'customer_recovery_state'
    merchant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    contact_count: Mapped[int] = mapped_column(Integer, default=0)
    cumulative_incentive_inr: Mapped[float] = mapped_column(Float, default=0)
    last_action: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    recovered: Mapped[bool] = mapped_column(Boolean, default=False)


class CustomerStateService:
    def __init__(self, db):
        self.db = db

    def get(self, customer_id, merchant_id='demo'):
        stmt = select(CustomerRecoveryState).where(
            CustomerRecoveryState.customer_id == customer_id,
            CustomerRecoveryState.merchant_id == merchant_id,
        )
        s = self.db.scalars(stmt).first()
        if not s:
            s = CustomerRecoveryState(customer_id=customer_id, merchant_id=merchant_id)
            self.db.add(s)
            self.db.commit()
        return s

    def eligible(self, customer_id, action, merchant_id='demo', max_attempts=None, cooldown_minutes=None):
        s = self.get(customer_id, merchant_id)
        spec = ACTION_REGISTRY[action]
        now = datetime.now(timezone.utc)
        attempts_limit = spec.max_attempts if max_attempts is None else min(spec.max_attempts, int(max_attempts))
        if s.opted_out or s.recovered:
            return False, 'CUSTOMER_INACTIVE'
        if s.attempt_count >= attempts_limit:
            return False, 'MAX_ATTEMPTS'
        if s.cooldown_until and s.cooldown_until > now:
            return False, 'COOLDOWN'
        return True, None

    def record(self, customer_id, merchant_id, action, recovered=False, incentive_inr=0, cooldown_minutes=None):
        s = self.get(customer_id, merchant_id)
        now = datetime.now(timezone.utc)
        spec = ACTION_REGISTRY[action]
        s.attempt_count += 1
        s.last_action = action
        s.last_action_at = now
        s.contact_count += int(spec.requires_customer_contact)
        s.cumulative_incentive_inr += float(incentive_inr)
        s.recovered = s.recovered or recovered
        cooldown = spec.min_cooldown_minutes if cooldown_minutes is None else max(spec.min_cooldown_minutes, int(cooldown_minutes))
        s.cooldown_until = now + timedelta(minutes=cooldown)
        self.db.commit()
        return s

    def mark_recovered(self, customer_id, merchant_id='demo'):
        s = self.get(customer_id, merchant_id)
        s.recovered = True
        s.cooldown_until = None
        self.db.commit()
        return s
