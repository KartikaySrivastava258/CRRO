from datetime import datetime,timezone,timedelta
from sqlalchemy import String,Integer,Float,Boolean,DateTime,select
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base
from .action_registry import ACTION_REGISTRY
class CustomerRecoveryState(Base):
    __tablename__='customer_recovery_state'
    customer_id:Mapped[str]=mapped_column(String(80),primary_key=True); merchant_id:Mapped[str]=mapped_column(String(80),index=True)
    attempt_count:Mapped[int]=mapped_column(Integer,default=0); contact_count:Mapped[int]=mapped_column(Integer,default=0); cumulative_incentive_inr:Mapped[float]=mapped_column(Float,default=0)
    last_action:Mapped[str|None]=mapped_column(String(80),nullable=True); last_action_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True); cooldown_until:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    opted_out:Mapped[bool]=mapped_column(Boolean,default=False); recovered:Mapped[bool]=mapped_column(Boolean,default=False)
class CustomerStateService:
    def __init__(self,db):self.db=db
    def get(self,customer_id,merchant_id='demo'):
        s=self.db.get(CustomerRecoveryState,customer_id)
        if not s:s=CustomerRecoveryState(customer_id=customer_id,merchant_id=merchant_id);self.db.add(s);self.db.commit()
        return s
    def eligible(self,customer_id,action,merchant_id='demo'):
        s=self.get(customer_id,merchant_id); spec=ACTION_REGISTRY[action]; now=datetime.now(timezone.utc)
        if s.opted_out or s.recovered:return False,'CUSTOMER_INACTIVE'
        if s.attempt_count>=spec.max_attempts:return False,'MAX_ATTEMPTS'
        if s.cooldown_until and s.cooldown_until>now:return False,'COOLDOWN'
        return True,None
    def record(self,customer_id,merchant_id,action,recovered=False,incentive_inr=0):
        s=self.get(customer_id,merchant_id); now=datetime.now(timezone.utc); spec=ACTION_REGISTRY[action]; s.attempt_count+=1; s.last_action=action;s.last_action_at=now;s.contact_count+=int(spec.requires_customer_contact);s.cumulative_incentive_inr+=float(incentive_inr);s.recovered=s.recovered or recovered;s.cooldown_until=now+timedelta(minutes=spec.min_cooldown_minutes);self.db.commit();return s
