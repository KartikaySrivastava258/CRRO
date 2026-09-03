from sqlalchemy import select
from .models import Execution

class ReconciliationService:
    def __init__(self,repo): self.repo=repo
    def handle_payment_link_event(self,payload):
        entity=((payload.get('payload') or {}).get('payment_link') or {}).get('entity') or payload.get('payment_link') or {}
        case_id=(entity.get('notes') or {}).get('case_id') or entity.get('reference_id')
        if not case_id:return {'status':'ignored','reason':'no_case_id'}
        status=entity.get('status','unknown')
        self.repo.execution(case_id=case_id,action='PAYMENT_LINK',provider='razorpay',provider_id=entity.get('id'),status=status,amount_inr=float(entity.get('amount',0))/100,raw_response=payload)
        return {'status':'reconciled','case_id':case_id,'provider_status':status}
