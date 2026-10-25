"""
SQLAlchemy ORM Models (Required Changes, Section 13)
------------------------------------------------------------
Replaces the in-memory `CustomerStateStore` / `AuditLog` with real
persistent tables. Schema is intentionally close to what
`crro/state.py` and `crro/executor.py` already model in memory, so the
DB-backed repository (`crro/state_db.py`) is a drop-in.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Float, Integer, Boolean, DateTime, ForeignKey, Text, JSON, Index
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def _uuid():
    return str(uuid.uuid4())


def _now():
    return datetime.now(timezone.utc)


class Merchant(Base):
    __tablename__ = "merchants"
    merchant_id = Column(String, primary_key=True)
    name = Column(String, nullable=True)
    category = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class Customer(Base):
    __tablename__ = "customers"
    customer_id = Column(String, primary_key=True)
    opted_out = Column(Boolean, default=False, nullable=False)
    opted_out_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class PaymentCase(Base):
    __tablename__ = "cases"
    case_id = Column(String, primary_key=True)
    merchant_id = Column(String, ForeignKey("merchants.merchant_id"), nullable=True)
    customer_id = Column(String, ForeignKey("customers.customer_id"), nullable=True)
    amount_inr = Column(Float, nullable=False)
    payment_method = Column(String, nullable=False)
    failure_reason = Column(String, nullable=False)
    attempt_count = Column(Integer, default=1)
    minutes_since_failure = Column(Integer, nullable=True)
    status = Column(String, default="OPEN")   # OPEN | RECOVERED | CLOSED_UNRECOVERED
    recovered_amount_inr = Column(Float, nullable=True)
    recovered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    decisions = relationship("RecoveryDecision", back_populates="case")


class RecoveryDecision(Base):
    """One row per (case, action) the optimizer/model evaluated or selected."""
    __tablename__ = "recovery_decisions"
    decision_id = Column(String, primary_key=True, default=_uuid)
    case_id = Column(String, ForeignKey("cases.case_id"), nullable=False)
    action = Column(String, nullable=False)
    recovery_probability = Column(Float, nullable=True)
    baseline_probability = Column(Float, nullable=True)
    estimated_uplift = Column(Float, nullable=True)
    expected_incremental_value_inr = Column(Float, nullable=True)
    model_version = Column(String, nullable=True)
    llm_diagnosis = Column(Text, nullable=True)
    llm_evidence = Column(JSON, nullable=True)
    llm_confidence = Column(Float, nullable=True)
    selected = Column(Boolean, default=False)          # chosen by the optimizer?
    approved = Column(Boolean, nullable=True)            # policy gate result
    requires_human_approval = Column(Boolean, default=False)
    reason_code = Column(String, nullable=True)
    execution_status = Column(String, nullable=True)      # EXECUTED | FAILED | PENDING | ...
    provider_reference = Column(String, nullable=True)    # e.g. Razorpay payment_link_id
    created_at = Column(DateTime(timezone=True), default=_now)

    case = relationship("PaymentCase", back_populates="decisions")


class CustomerActionHistory(Base):
    """Persistent version of crro/state.py's in-memory table."""
    __tablename__ = "customer_action_history"
    record_id = Column(String, primary_key=True, default=_uuid)
    customer_id = Column(String, ForeignKey("customers.customer_id"), nullable=False)
    case_id = Column(String, ForeignKey("cases.case_id"), nullable=False)
    action = Column(String, nullable=False)
    status = Column(String, nullable=False)   # PROPOSED | EXECUTED | BLOCKED | FAILED
    provider_reference = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        Index("ix_cah_customer_action", "customer_id", "action"),
        Index("ix_cah_case", "case_id"),
    )


class AuditEvent(Base):
    """Persistent version of crro/executor.py's in-memory AuditLog."""
    __tablename__ = "audit_events"
    event_id = Column(String, primary_key=True, default=_uuid)
    case_id = Column(String, ForeignKey("cases.case_id"), nullable=False)
    event_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=True)
    ts = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (Index("ix_audit_case", "case_id"),)


class Offer(Base):
    """Persistent version of crro/offers.py's OFFER_REGISTRY (config, not LLM output)."""
    __tablename__ = "offers"
    offer_id = Column(String, primary_key=True)
    offer_type = Column(String, nullable=False)
    discount_pct = Column(Float, nullable=False)
    max_discount_inr = Column(Float, nullable=False)
    approval_required = Column(Boolean, default=False)
    active = Column(Boolean, default=True)


class WebhookEvent(Base):
    """
    Idempotency ledger for inbound Razorpay webhooks (Required Checklist
    Section 4). `razorpay_event_id` is unique -- a second delivery of the
    same `x-razorpay-event-id` is detected here before any state changes,
    satisfying Razorpay's documented at-least-once delivery semantics.
    """
    __tablename__ = "webhook_events"
    id = Column(String, primary_key=True, default=_uuid)
    razorpay_event_id = Column(String, unique=True, nullable=False)
    event_type = Column(String, nullable=False)
    case_id = Column(String, nullable=True)
    payload = Column(JSON, nullable=True)
    signature_valid = Column(Boolean, nullable=False)
    processed = Column(Boolean, default=False)
    processing_note = Column(Text, nullable=True)
    received_at = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (Index("ix_webhook_event_type", "event_type"),)
