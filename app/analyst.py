from pydantic import BaseModel, Field
from .config import get_settings
from .action_registry import ALLOWED_ACTIONS if False else None

ALLOWED={'NO_ACTION','RETRY_NOW','RETRY_LATER','PAYMENT_LINK','ALTERNATIVE_PAYMENT_METHOD','CUSTOMER_NUDGE','SMALL_INCENTIVE'}
class AnalystOutput(BaseModel):
    diagnosis:str
    recommended_action:str
    rationale:list[str]=Field(default_factory=list)
    evidence_ids:list[int]=Field(default_factory=list)
    confidence:float=0.0

SYSTEM='''You are CRRO Recovery Analyst. Diagnose payment-recovery context and choose ONLY an action from the supplied allowlist. Never invent transaction facts, amounts, offers, discounts, credentials, or outcomes. The optimizer/policy engine owns money decisions.'''

class RecoveryAnalyst:
    def __init__(self): self.settings=get_settings()
    def analyze(self, case, evidence, allowed):
        allowed=[a for a in allowed if a in ALLOWED]
        if not allowed: allowed=['NO_ACTION']
        prompt=f"Case={case}\nAllowed={allowed}\nEvidence={evidence}\nReturn JSON diagnosis,recommended_action,rationale,evidence_ids,confidence."
        if self.settings.openai_api_key:
            try:
                from openai import OpenAI
                client=OpenAI(api_key=self.settings.openai_api_key)
                r=client.chat.completions.create(model=self.settings.openai_model,temperature=0,response_format={'type':'json_object'},messages=[{'role':'system','content':SYSTEM},{'role':'user','content':prompt}])
                out=AnalystOutput.model_validate_json(r.choices[0].message.content)
                if out.recommended_action not in allowed: out.recommended_action='NO_ACTION'
                return out
            except Exception: pass
        reason=case.get('failure_reason','')
        if reason=='insufficient_funds' and 'PAYMENT_LINK' in allowed: action='PAYMENT_LINK'
        elif reason in {'network_error','otp_timeout'} and 'RETRY_LATER' in allowed: action='RETRY_LATER'
        elif 'RETRY_NOW' in allowed and case.get('attempt_count',1)<2: action='RETRY_NOW'
        else: action=allowed[0]
        return AnalystOutput(diagnosis=f"Failure reason: {reason or 'unknown'}",recommended_action=action,rationale=['Deterministic fallback used; no LLM claim was required.'],evidence_ids=[e.id for e in evidence[:5]],confidence=0.55)
