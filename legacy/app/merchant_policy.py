from datetime import datetime,timezone
from sqlalchemy import String,Integer,Float,JSON,DateTime
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base
from .action_registry import CONTROLLABLE_ACTIONS
class MerchantPolicy(Base):
    __tablename__='merchant_policies'
    merchant_id:Mapped[str]=mapped_column(String(80),primary_key=True); max_attempts:Mapped[int]=mapped_column(Integer,default=3); contact_budget:Mapped[int]=mapped_column(Integer,default=4000); incentive_budget_inr:Mapped[float]=mapped_column(Float,default=100000); max_incentive_pct:Mapped[float]=mapped_column(Float,default=.05); approval_threshold_inr:Mapped[float]=mapped_column(Float,default=50000); cooldown_minutes:Mapped[int]=mapped_column(Integer,default=30); allowed_actions:Mapped[list]=mapped_column(JSON,default=lambda:CONTROLLABLE_ACTIONS); action_caps:Mapped[dict]=mapped_column(JSON,default=dict); updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class MerchantPolicyService:
    def __init__(self,db):self.db=db
    def get(self,merchant_id='demo'):
        p=self.db.get(MerchantPolicy,merchant_id)
        if not p:p=MerchantPolicy(merchant_id=merchant_id,allowed_actions=list(CONTROLLABLE_ACTIONS),action_caps={});self.db.add(p);self.db.commit()
        return p
    def update(self,merchant_id,patch):
        p=self.get(merchant_id)
        for k,v in patch.items():
            if hasattr(p,k) and k not in {'merchant_id'}:setattr(p,k,v)
        p.updated_at=datetime.now(timezone.utc);self.db.commit();return p
    def as_dict(self,p):return {k:getattr(p,k) for k in ('merchant_id','max_attempts','contact_budget','incentive_budget_inr','max_incentive_pct','approval_threshold_inr','cooldown_minutes','allowed_actions','action_caps')}
