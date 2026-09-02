import hashlib,time
from dataclasses import dataclass
import pandas as pd
@dataclass
class ExecutionResult: status:str; provider_reference:str|None; recovered:int; message:str
class SimulatedExecutionAdapter:
    def __init__(self,seed=1): self.keys=set(); self.seed=seed
    def execute(self,case_id,action,idempotency_key):
        if idempotency_key in self.keys: return ExecutionResult("SKIPPED_DUPLICATE",None,0,"Idempotency key already processed")
        self.keys.add(idempotency_key); ref="sim_"+hashlib.sha256((case_id+action).encode()).hexdigest()[:16]; return ExecutionResult("EXECUTED",ref,0,"Accepted by simulated provider")
class AuditLog:
    def __init__(self): self.rows=[]
    def append(self,**kw): self.rows.append(kw)
    def to_df(self): return pd.DataFrame(self.rows)
def run_execution(allocation,test_df,adapter,audit,policy_decisions,model_version):
    rows=[]
    for _,r in allocation.iterrows():
        cid=r.case_id; action=r.action; dec=policy_decisions.get(cid); idem=hashlib.sha256(f"{cid}:{action}:v1".encode()).hexdigest(); started=time.time()
        if action=="NO_ACTION" or (dec and not dec.approved): status="NOT_EXECUTED"; ref=None; recovered=0; msg=dec.message if dec else "NO_ACTION"
        else:
            ex=adapter.execute(cid,action,idem); status,ref,recovered,msg=ex.status,ex.provider_reference,ex.recovered,ex.message
        audit.append(timestamp=time.time(),case_id=cid,action=action,status=status,idempotency_key=idem,provider_reference=ref,model_version=model_version,latency_ms=(time.time()-started)*1000,message=msg)
        rows.append({"case_id":cid,"action":action,"exec_status":status,"provider_reference":ref})
    return pd.DataFrame(rows),0
