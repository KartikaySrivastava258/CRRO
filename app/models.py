from datetime import datetime, timezone
from sqlalchemy import String, Integer, Float, DateTime, Text, Boolean, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

def now(): return datetime.now(timezone.utc)

class RecoveryCase(Base):
    __tablename__='recovery_cases'
    id: Mapped[str]=mapped_column(String(80), primary_key=True)
    merchant_id: Mapped[str]=mapped_column(String(80), index=True)
    customer_id: Mapped[str]=mapped_column(String(80), index=True)
    amount_inr: Mapped[float]=mapped_column(Float)
    failure_reason: Mapped[str]=mapped_column(String(80))
    payment_method: Mapped[str]=mapped_column(String(40))
    status: Mapped[str]=mapped_column(String(30), default='AT_RISK', index=True)
    payload: Mapped[dict]=mapped_column(JSON, default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)

class Approval(Base):
    __tablename__='approvals'
    id: Mapped[int]=mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str]=mapped_column(String(80), index=True)
    action: Mapped[str]=mapped_column(String(80))
    status: Mapped[str]=mapped_column(String(20), default='PENDING', index=True)
    reason: Mapped[str]=mapped_column(Text, default='')
    decided_by: Mapped[str]=mapped_column(String(120), default='system')
    decided_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)

class IdempotencyRecord(Base):
    __tablename__='idempotency_records'
    id: Mapped[int]=mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str]=mapped_column(String(120), unique=True, index=True)
    action: Mapped[str]=mapped_column(String(80))
    response: Mapped[dict]=mapped_column(JSON)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)

class Execution(Base):
    __tablename__='executions'
    id: Mapped[int]=mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str]=mapped_column(String(80), index=True)
    action: Mapped[str]=mapped_column(String(80))
    provider: Mapped[str]=mapped_column(String(40))
    provider_id: Mapped[str|None]=mapped_column(String(120), nullable=True)
    status: Mapped[str]=mapped_column(String(30), index=True)
    amount_inr: Mapped[float]=mapped_column(Float, default=0)
    raw_response: Mapped[dict]=mapped_column(JSON, default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)

class WebhookEvent(Base):
    __tablename__='webhook_events'
    __table_args__=(UniqueConstraint('event_id', name='uq_webhook_event'),)
    id: Mapped[int]=mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str]=mapped_column(String(160), index=True)
    event_type: Mapped[str]=mapped_column(String(100))
    payload: Mapped[dict]=mapped_column(JSON)
    processed: Mapped[bool]=mapped_column(Boolean, default=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)

class Evidence(Base):
    __tablename__='evidence'
    id: Mapped[int]=mapped_column(Integer, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str]=mapped_column(String(80), index=True)
    title: Mapped[str]=mapped_column(String(200))
    text: Mapped[str]=mapped_column(Text)
    metadata_json: Mapped[dict]=mapped_column(JSON, default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
