from .config import get_settings
from .repository import Repository

class ApprovalService:
    def __init__(self, repo: Repository): self.repo=repo
    def gate(self, case_id, action, amount, require_approval=False):
        s=get_settings()
        if require_approval or amount >= s.approval_amount_inr:
            a=self.repo.approval(case_id,action) or self.repo.create_approval(case_id,action,f'Approval required for {action} on ₹{amount:,.0f}')
            return a.status == 'APPROVED', {'approval_id':a.id,'status':a.status}
        return True, {'approval_id':None,'status':'AUTO_APPROVED'}
