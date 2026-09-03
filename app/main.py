import time,json
import numpy as np
import pandas as pd
from fastapi import FastAPI,Depends,HTTPException,Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel,Field
from sqlalchemy.orm import Session
from crro.data_generator import generate_dataset,eligible_actions_for
from crro.model import UpliftModel
from crro.optimizer import Budgets,compute_expected_incremental_value,exact_allocate
from crro.policy import evaluate_policy
from crro.evaluation import add_common_random_numbers,realize_policy_outcomes,summarize_policy,segment_performance
from crro.baselines import control_policy,fixed_retry_policy,rule_based_policy,raw_probability_policy,uplift_only_policy,per_case_optimal_policy
from crro.executor import SimulatedExecutionAdapter,AuditLog,run_execution
from crro.causal_metrics import evaluate_uplift
from crro.experiments import multi_seed_benchmark,ablation
from .db import get_db,init_db
from .repository import Repository
from .approvals import ApprovalService
from .analyst import RecoveryAnalyst
from .retrieval import EvidenceRetriever
from .razorpay import RazorpayTestAdapter,verify_webhook
from .reconciliation import ReconciliationService
from .customer_state import CustomerStateService
from .merchant_policy import MerchantPolicyService
app=FastAPI(title='CRRO',version='3.0.0',description='Counterfactual Revenue Recovery Optimizer');STATE={}
class OptimizeRequest(BaseModel):cases:int=Field(2000,ge=100,le=20000);seed:int=2026;contacts:int|None=None;incentive_budget_inr:float|None=None;merchant_id:str='demo'
class ApprovalRequest(BaseModel):status:str=Field(pattern='^(APPROVED|REJECTED)$');actor:str='merchant'
class AnalystRequest(BaseModel):case:dict;allowed_actions:list[str];merchant_id:str='demo'
class PolicyRequest(BaseModel):max_attempts:int|None=None;contact_budget:int|None=None;incentive_budget_inr:float|None=None;max_incentive_pct:float|None=None;approval_threshold_inr:float|None=None;cooldown_minutes:int|None=None;allowed_actions:list[str]|None=None;action_caps:dict|None=None
@app.on_event('startup')
def startup():init_db()
def clean(df):return df.replace({np.nan:None}).to_dict('records')
def run_engine(req,db):
    policy=MerchantPolicyService(db).get(req.merchant_id);train=generate_dataset(max(20000,req.cases),req.seed+1);test=generate_dataset(req.cases,req.seed+2,shift=True)
    for d in (train,test):d['eligible_actions']=d.apply(eligible_actions_for,axis=1)
    model=UpliftModel().fit(train);scored=compute_expected_incremental_value(model.score_all_actions(test),test[['case_id','amount_inr']],incentive_pct=policy.max_incentive_pct);scored['customer_id']=scored.case_id.map(test.set_index('case_id').customer_id);allowed=set(policy.allowed_actions);scored=scored[scored.action.isin(allowed)]
    state=CustomerStateService(db);eligible_keys=set()
    for _,r in scored.iterrows():
        ok,_=state.eligible(r.customer_id,r.action,req.merchant_id)
        if ok:eligible_keys.add(f'{r.case_id}|{r.action}')
    scored=scored[scored.apply(lambda r:f'{r.case_id}|{r.action}' in eligible_keys,axis=1)]
    budgets=Budgets(req.contacts if req.contacts is not None else policy.contact_budget,req.incentive_budget_inr if req.incentive_budget_inr is not None else policy.incentive_budget_inr,policy.action_caps);allocation,usage=exact_allocate(scored,budgets);opted=set(test.customer_id.iloc[::33]);decisions={};final=[];indexed=test.set_index('case_id');repo=Repository(db);approvals=ApprovalService(repo,policy.approval_threshold_inr)
    for _,r in allocation.iterrows():
        row=indexed.loc[r.case_id];repo.upsert_case(row.to_dict());d=evaluate_policy(row,r.action,r.expected_incremental_value_inr,{'attempts_used':int(row.get('attempt_count',0))},opted);approved=d.approved
        if approved:approved,_=approvals.gate(r.case_id,r.action,float(row.amount_inr))
        decisions[r.case_id]=d;final.append({'case_id':r.case_id,'action':r.action if approved else 'NO_ACTION'})
    final=pd.DataFrame(final);test=add_common_random_numbers(test,req.seed+700);realized=realize_policy_outcomes(test,final,shift=True);policies={'CONTROL':control_policy(test),'FIXED_RETRY':fixed_retry_policy(test),'RULE_BASED':rule_based_policy(test),'RAW_PROBABILITY':raw_probability_policy(scored),'UPLIFT_ONLY':uplift_only_policy(scored),'CRRO':final,'PER_CASE_ORACLE':per_case_optimal_policy(test,scored)};metrics=[summarize_policy(realize_policy_outcomes(test,a,True),n) for n,a in policies.items()];causal=evaluate_uplift(model,scored,test,final);exec_input=allocation.merge(final.rename(columns={'action':'final_action'}),on='case_id',how='left').assign(action=lambda x:x.final_action);adapter=SimulatedExecutionAdapter(req.seed+3);audit=AuditLog();exec_df,_=run_execution(exec_input,test,adapter,audit,decisions,'uplift_tlearner_v2');report={'generated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'dataset':{'train_cases':len(train),'test_cases':len(test),'distribution_shift':True},'budget_usage':usage,'policies':metrics,'causal_metrics':causal,'guardrails':{'opted_out':len(opted),'blocked':int((final.action=='NO_ACTION').sum())},'segments':segment_performance(realized).to_dict('records'),'execution':exec_df.exec_status.value_counts().to_dict(),'allocation':clean(allocation),'scored':clean(scored),'audit':clean(audit.to_df())};STATE.update(report=report,model=model,test=test,scored=scored,allocation=allocation);return report
@app.get('/api/health')
def health(db:Session=Depends(get_db)):return {'status':'ok','service':'crro','version':app.version,'database':db.bind.dialect.name}
@app.post('/api/optimize')
def optimize(req:OptimizeRequest,db:Session=Depends(get_db)):return run_engine(req,db)
@app.get('/api/report')
def report(db:Session=Depends(get_db)):return STATE.get('report') or run_engine(OptimizeRequest(),db)
@app.get('/api/cases')
def cases(limit:int=50,db:Session=Depends(get_db)):r=STATE.get('report') or run_engine(OptimizeRequest(),db);return {'cases':r['allocation'][:limit]}
@app.post('/api/benchmark/multi-seed')
def benchmark(seeds:int=10,cases:int=1500):return multi_seed_benchmark(range(seeds),cases)
@app.get('/api/benchmark/ablation')
def benchmark_ablation(seed:int=2026,cases:int=2000):return ablation(seed,cases)
@app.get('/api/merchant-policy')
def get_policy(merchant_id:str='demo',db:Session=Depends(get_db)):return MerchantPolicyService(db).as_dict(MerchantPolicyService(db).get(merchant_id))
@app.put('/api/merchant-policy/{merchant_id}')
def update_policy(merchant_id:str,req:PolicyRequest,db:Session=Depends(get_db)):
    p=MerchantPolicyService(db).update(merchant_id,req.model_dump(exclude_none=True));return MerchantPolicyService(db).as_dict(p)
@app.get('/api/customer-state/{customer_id}')
def customer_state(customer_id:str,merchant_id:str='demo',db:Session=Depends(get_db)):
    s=CustomerStateService(db).get(customer_id,merchant_id);return {k:getattr(s,k) for k in ('customer_id','merchant_id','attempt_count','contact_count','cumulative_incentive_inr','last_action','last_action_at','cooldown_until','opted_out','recovered')}
@app.post('/api/analyst/analyze')
def analyst(req:AnalystRequest,db:Session=Depends(get_db)):
    evidence=[{'id':e.id,'title':e.title,'text':e.text} for e in Repository(db).evidence(req.merchant_id)];return RecoveryAnalyst().analyze(req.case,evidence,req.allowed_actions).model_dump()
@app.post('/api/approvals/{approval_id}')
def decide_approval(approval_id:int,req:ApprovalRequest,db:Session=Depends(get_db)):
    a=Repository(db).decide_approval(approval_id,req.status,req.actor);return {'id':a.id,'status':a.status,'case_id':a.case_id,'action':a.action}
@app.get('/api/approvals')
def approvals(status:str='PENDING',db:Session=Depends(get_db)):
    from sqlalchemy import select
    from .models import Approval
    rows=db.scalars(select(Approval).where(Approval.status==status).order_by(Approval.id.desc())).all();return {'approvals':[{'id':a.id,'case_id':a.case_id,'action':a.action,'status':a.status,'reason':a.reason} for a in rows]}
@app.post('/api/evidence')
def add_evidence(payload:dict,db:Session=Depends(get_db)):
    e=Repository(db).add_evidence(payload.get('merchant_id','demo'),payload['title'],payload['text'],payload.get('metadata'));return {'id':e.id,'merchant_id':e.merchant_id,'title':e.title}
@app.get('/api/evidence/search')
def search_evidence(merchant_id:str,query:str,k:int=5,db:Session=Depends(get_db)):return {'hits':[h.__dict__ for h in EvidenceRetriever(Repository(db)).search(merchant_id,query,k)]}
@app.post('/api/razorpay/payment-link')
def create_link(payload:dict,db:Session=Depends(get_db)):
    repo=Repository(db);key=payload.get('idempotency_key') or f"crro-{payload['case_id']}-PAYMENT_LINK";old=repo.idem(key)
    if old:return old.response
    data=RazorpayTestAdapter().create_payment_link(payload,key,payload.get('notify',False));response={'provider':'razorpay','mode':'test','case_id':payload['case_id'],'payment_link':data};repo.save_idem(key,'PAYMENT_LINK',response);repo.execution(case_id=payload['case_id'],action='PAYMENT_LINK',provider='razorpay',provider_id=data.get('id'),status=data.get('status','created'),amount_inr=float(payload.get('amount_inr',0)),raw_response=data);return response
@app.post('/api/webhooks/razorpay')
async def razorpay_webhook(request:Request,db:Session=Depends(get_db)):
    body=await request.body();sig=request.headers.get('X-Razorpay-Signature','');from .config import get_settings
    if not verify_webhook(body,sig,get_settings().razorpay_webhook_secret):raise HTTPException(400,'Invalid webhook signature')
    payload=json.loads(body);event_id=request.headers.get('x-razorpay-event-id') or payload.get('event','')+'-'+str(hash(body));event=payload.get('event','unknown');repo=Repository(db);_,created=repo.webhook_once(event_id,event,payload)
    if not created:return {'status':'duplicate_ignored'}
    return ReconciliationService(repo).handle_payment_link_event(payload) if event.startswith('payment_link.') else {'status':'stored','event':event}
@app.get('/',response_class=HTMLResponse)
def home():return HTMLResponse(open('app/dashboard.html').read())
