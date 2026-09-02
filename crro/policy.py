from dataclasses import dataclass
from .action_registry import ACTION_REGISTRY
@dataclass
class PolicyDecision:
    approved:bool; requires_human_approval:bool; reason_code:str|None; message:str

def evaluate_policy(row,action,expected_incremental_value=0,customer_state=None,opted_out_customers=None):
    customer_state=customer_state or {}; opted_out_customers=opted_out_customers or set(); spec=ACTION_REGISTRY[action]
    if action=="NO_ACTION": return PolicyDecision(True,False,None,"No intervention selected")
    if row.customer_id in opted_out_customers: return PolicyDecision(False,False,"CUSTOMER_OPTED_OUT","Customer has opted out")
    if int(customer_state.get("attempts_used",0))>=spec.max_attempts: return PolicyDecision(False,False,"MAX_ATTEMPTS","Maximum attempts reached")
    if expected_incremental_value is not None and expected_incremental_value<=0: return PolicyDecision(False,False,"NO_POSITIVE_UPLIFT","No positive incremental value")
    threshold=spec.requires_approval_above_amount
    if threshold is not None and float(row.amount_inr)>threshold: return PolicyDecision(False,True,"APPROVAL_PENDING",f"Merchant approval required above ₹{threshold:,.0f}")
    return PolicyDecision(True,False,None,"Approved by deterministic policy")
