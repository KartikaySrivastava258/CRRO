import time
import pandas as pd
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel,Field
from crro.data_generator import generate_dataset,eligible_actions_for
from crro.model import UpliftModel
from crro.optimizer import Budgets,compute_expected_incremental_value,greedy_allocate
from crro.policy import evaluate_policy
from crro.evaluation import add_common_random_numbers,realize_policy_outcomes,summarize_policy,segment_performance
from crro.baselines import control_policy,fixed_retry_policy,rule_based_policy,raw_probability_policy,uplift_only_policy,per_case_optimal_policy
from crro.executor import SimulatedExecutionAdapter,AuditLog,run_execution
app=FastAPI(title="CRRO",version="1.0.0",description="Counterfactual Revenue Recovery Optimizer")
STATE={}
class OptimizeRequest(BaseModel):
    cases:int=Field(5000,ge=100,le=50000); seed:int=2026; contacts:int=1000; incentive_budget_inr:float=25000

def run_engine(req):
    train=generate_dataset(max(20000,req.cases),req.seed+1); test=generate_dataset(req.cases,req.seed+2,shift=True)
    for d in (train,test): d["eligible_actions"]=d.apply(eligible_actions_for,axis=1)
    model=UpliftModel().fit(train); scored=compute_expected_incremental_value(model.score_all_actions(test),test[["case_id","amount_inr"]])
    budgets=Budgets(req.contacts,req.incentive_budget_inr,{"ALTERNATIVE_PAYMENT_METHOD":max(1,req.contacts//2)}); allocation,usage=greedy_allocate(scored,budgets)
    opted=set(test.customer_id.iloc[::33]); decisions={}; final=[]; indexed=test.set_index("case_id")
    for _,r in allocation.iterrows():
        d=evaluate_policy(indexed.loc[r.case_id],r.action,r.expected_incremental_value_inr,{"attempts_used":0},opted); decisions[r.case_id]=d; final.append({"case_id":r.case_id,"action":r.action if d.approved else "NO_ACTION"})
    final=pd.DataFrame(final); test=add_common_random_numbers(test); realized=realize_policy_outcomes(test,final,shift=True)
    policies={"CONTROL":control_policy(test),"FIXED_RETRY":fixed_retry_policy(test),"RULE_BASED":rule_based_policy(test),"RAW_PROBABILITY":raw_probability_policy(scored),"UPLIFT_ONLY":uplift_only_policy(scored),"CRRO":final,"PER_CASE_ORACLE":per_case_optimal_policy(test,scored)}
    metrics=[summarize_policy(realize_policy_outcomes(test,a,True),n) for n,a in policies.items()]
    exec_input=allocation.merge(final.rename(columns={"action":"final_action"}),on="case_id",how="left").assign(action=lambda x:x.final_action)
    adapter=SimulatedExecutionAdapter(req.seed+3); audit=AuditLog(); exec_df,_=run_execution(exec_input,test,adapter,audit,decisions,"uplift_tlearner_v2")
    report={"generated_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"dataset":{"train_cases":len(train),"test_cases":len(test),"distribution_shift":True},"budget_usage":usage,"policies":metrics,"guardrails":{"opted_out":len(opted),"blocked":int((final.action=="NO_ACTION").sum())},"segments":segment_performance(realized).to_dict("records"),"execution":exec_df.exec_status.value_counts().to_dict(),"allocation":allocation.to_dict("records"),"scored":scored.to_dict("records"),"audit":audit.to_df().to_dict("records")}
    STATE.update(report=report,model=model,test=test,scored=scored,allocation=allocation); return report
@app.get("/api/health")
def health(): return {"status":"ok","service":"crro","version":app.version}
@app.post("/api/optimize")
def optimize(req:OptimizeRequest): return run_engine(req)
@app.get("/api/report")
def report(): return STATE.get("report") or run_engine(OptimizeRequest())
@app.get("/api/cases")
def cases(limit:int=50):
    r=STATE.get("report") or run_engine(OptimizeRequest()); return {"cases":r["allocation"][:limit]}
@app.get("/",response_class=HTMLResponse)
def home(): return HTMLResponse(open("app/dashboard.html").read())
