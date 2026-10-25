from .repository import Repository
class ApprovalService:
    def __init__(self,repo,approval_threshold_inr=50000): self.repo=repo; self.approval_threshold_inr=approval_threshold_inr
    def gate(self,case_id,action,amount,require_approval=False):
        if require_approval or amount>=self.approval_threshold_inr:
            a=self.repo.approval(case_id,action) or self.repo.create_approval(case_id,action,f'Approval required for {action} on ₹{amount:,.0f}')
            return a.status=='APPROVED',{'approval_id':a.id,'status':a.status}
        return True,{'approval_id':None,'status':'AUTO_APPROVED'}
