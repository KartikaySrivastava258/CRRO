from datetime import datetime, timezone, timedelta
from sqlalchemy import String, Integer, Float, Boolean, DateTime, select
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base
from crro.action_registry import ACTION_REGISTRY


class CustomerRecoveryState(Base):
    __tablename__ = "customer_recovery_state"

    merchant_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    recovered: Mapped[bool] = mapped_column(Boolean, default=False)
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    last_action: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovered_amount_inr: Mapped[float] = mapped_column(Float, default=0.0)


class CustomerStateService:
    def __init__(self, db):
        self.db = db

    def get(self, customer_id: str, merchant_id: str = "demo") -> CustomerRecoveryState:
        stmt = select(CustomerRecoveryState).where(
            CustomerRecoveryState.customer_id == customer_id,
            CustomerRecoveryState.merchant_id == merchant_id,
        )
        state = self.db.scalars(stmt).first()
        if state is None:
            state = CustomerRecoveryState(customer_id=customer_id, merchant_id=merchant_id)
            self.db.add(state)
            self.db.commit()
            self.db.refresh(state)
        return state

    def eligible(
        self,
        customer_id: str,
        merchant_id: str,
        action: str,
        max_attempts: int | None = None,
        cooldown_minutes: int | None = None,
    ) -> tuple[bool, str]:
        spec = ACTION_REGISTRY[action]
        state = self.get(customer_id, merchant_id)
        attempts_limit = spec.max_attempts if max_attempts is None else min(spec.max_attempts, int(max_attempts))
        if state.opted_out or state.recovered:
            return False, "CUSTOMER_INACTIVE"
        if state.attempt_count >= attempts_limit:
            return False, "MAX_ATTEMPTS"
        now = datetime.now(timezone.utc)
        if state.cooldown_until and state.cooldown_until > now:
            return False, "COOLDOWN"
        return True, "ELIGIBLE"

    def record(
        self,
        customer_id: str,
        merchant_id: str,
        action: str,
        recovered: bool = False,
        incentive_inr: float = 0.0,
        cooldown_minutes: int | None = None,
        recovered_amount_inr: float = 0.0,
    ) -> CustomerRecoveryState:
        state = self.get(customer_id, merchant_id)
        spec = ACTION_REGISTRY[action]
        now = datetime.now(timezone.utc)
        state.attempt_count += 1
        state.last_action = action
        state.last_action_at = now
        cooldown = spec.min_cooldown_minutes if cooldown_minutes is None else max(spec.min_cooldown_minutes, int(cooldown_minutes))
        state.cooldown_until = now + timedelta(minutes=cooldown) if cooldown > 0 else None
        if recovered:
            state.recovered = True
            state.recovered_amount_inr += float(recovered_amount_inr)
            state.cooldown_until = None
        self.db.commit()
        self.db.refresh(state)
        return state

    def mark_recovered(self, customer_id: str, merchant_id: str, amount_inr: float = 0.0) -> CustomerRecoveryState:
        state = self.get(customer_id, merchant_id)
        state.recovered = True
        state.recovered_amount_inr += float(amount_inr)
        state.cooldown_until = None
        self.db.commit()
        self.db.refresh(state)
        return state
