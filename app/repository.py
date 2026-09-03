from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import RecoveryCase, Approval, IdempotencyRecord, Execution, WebhookEvent, Evidence, now

class Repository:
    def __init__(self, db: Session): self.db=db
    def upsert_case(self, data):
        case=self.db.get(RecoveryCase,data['case_id'])
        if not case:
            case=RecoveryCase(id=data['case_id'],merchant_id=data.get('merchant_id','demo'),customer_id=data.get('customer_id',''),amount_inr=float(data.get('amount_inr',0)),failure_reason=data.get('failure_reason','unknown'),payment_method=data.get('payment_method','unknown'),payload=dict(data)); self.db.add(case)
        else: case.payload=dict(data)
        self.db.commit(); return case
    def approval(self, case_id, action):
        return self.db.scalar(select(Approval).where(Approval.case_id==case_id,Approval.action==action,Approval.status=='PENDING').order_by(Approval.id.desc()))
    def create_approval(self, case_id, action, reason):
        a=Approval(case_id=case_id,action=action,reason=reason); self.db.add(a); self.db.commit(); return a
    def decide_approval(self, approval_id, status, actor):
        a=self.db.get(Approval,approval_id); a.status=status; a.decided_by=actor; a.decided_at=now(); self.db.commit(); return a
    def idem(self,key): return self.db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.key==key))
    def save_idem(self,key,action,response):
        r=IdempotencyRecord(key=key,action=action,response=response); self.db.add(r); self.db.commit(); return r
    def execution(self, **kw):
        e=Execution(**kw); self.db.add(e); self.db.commit(); return e
    def webhook_once(self,event_id,event_type,payload):
        old=self.db.scalar(select(WebhookEvent).where(WebhookEvent.event_id==event_id))
        if old: return old, False
        e=WebhookEvent(event_id=event_id,event_type=event_type,payload=payload); self.db.add(e); self.db.commit(); return e, True
    def add_evidence(self,merchant_id,title,text,metadata=None):
        e=Evidence(merchant_id=merchant_id,title=title,text=text,metadata_json=metadata or {}); self.db.add(e); self.db.commit(); return e
    def evidence(self,merchant_id): return self.db.scalars(select(Evidence).where(Evidence.merchant_id==merchant_id).order_by(Evidence.id.desc())).all()
