from dataclasses import dataclass, field
import pandas as pd
from .action_registry import ACTION_REGISTRY

@dataclass
class Budgets:
    daily_contacts:int=4000
    daily_incentive_inr:float=100000
    per_action_daily_cap:dict=field(default_factory=lambda:{"ALTERNATIVE_PAYMENT_METHOD":1000})
    per_customer_daily_cap:int=1

def compute_expected_incremental_value(scored_df,case_amounts,incentive_pct=.02):
    df=scored_df.merge(case_amounts,on='case_id',how='left')
    df['action_cost_inr']=df.action.map(lambda a:ACTION_REGISTRY[a].monetary_cost_inr)
    df['recoverable_amount_inr']=df.amount_inr
    df['incentive_pct']=df.action.map(lambda a:min(incentive_pct,ACTION_REGISTRY[a].max_incentive_pct) if ACTION_REGISTRY[a].is_incentive else 0)
    df['incentive_cost_inr']=df.amount_inr*df.incentive_pct
    df['contact_cost_inr']=df.action.map(lambda a:1.0 if ACTION_REGISTRY[a].requires_customer_contact else 0.0)
    df['expected_incremental_value_inr']=df.estimated_uplift*df.amount_inr-df.action_cost_inr-df.incentive_cost_inr
    return df

def _fallback_greedy(df,budgets):
    selected={}; contacts=0; incentive=0.; caps={a:0 for a in budgets.per_action_daily_cap}; customers={}
    for _,r in df[df.expected_incremental_value_inr>0].sort_values('expected_incremental_value_inr',ascending=False).iterrows():
        if r.case_id in selected: continue
        customer=str(r.get('customer_id',r.case_id))
        if customers.get(customer,0)>=budgets.per_customer_daily_cap: continue
        if r.requires_customer_contact and contacts>=budgets.daily_contacts: continue
        if incentive+float(r.incentive_cost_inr)>budgets.daily_incentive_inr: continue
        cap=budgets.per_action_daily_cap.get(r.action)
        if cap is not None and caps.get(r.action,0)>=cap: continue
        selected[r.case_id]=r.to_dict(); customers[customer]=customers.get(customer,0)+1
        contacts+=int(r.requires_customer_contact); incentive+=float(r.incentive_cost_inr); caps[r.action]=caps.get(r.action,0)+1
    return selected,contacts,incentive,caps,'greedy_fallback'

def exact_allocate(scored_df,budgets,time_limit_seconds=10):
    """Exact 0/1 portfolio allocation with CP-SAT. One action per case; NO_ACTION is implicit."""
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return greedy_allocate(scored_df,budgets)
    df=scored_df.copy().reset_index(drop=True)
    candidates=df[df.expected_incremental_value_inr>0].copy()
    if candidates.empty:return greedy_allocate(df,budgets)
    model=cp_model.CpModel(); vars=[]
    SCALE=100
    for i in candidates.index: vars.append(model.NewBoolVar(f'x_{i}'))
    model.Add(sum(vars)<=int(len(candidates)))
    contacts=[]; incentive=[]
    for i,v in zip(candidates.index,vars):
        r=df.loc[i]; contacts.append(int(round(float(r.requires_customer_contact)))*v); incentive.append(int(round(float(r.incentive_cost_inr)*SCALE))*v)
    model.Add(sum(contacts)<=int(budgets.daily_contacts)); model.Add(sum(incentive)<=int(round(budgets.daily_incentive_inr*SCALE)))
    for action,cap in budgets.per_action_daily_cap.items():
        av=[v for i,v in zip(candidates.index,vars) if df.loc[i,'action']==action]
        if av:model.Add(sum(av)<=int(cap))
    for customer,g in candidates.groupby(candidates.get('customer_id',pd.Series(index=candidates.index,data=candidates.index))):
        gv=[vars[list(candidates.index).index(i)] for i in g.index]
        model.Add(sum(gv)<=int(budgets.per_customer_daily_cap))
    coeff=[int(round(float(df.loc[i,'expected_incremental_value_inr'])*SCALE)) for i in candidates.index]
    model.Maximize(sum(c*v for c,v in zip(coeff,vars)))
    solver=cp_model.CpSolver(); solver.parameters.max_time_in_seconds=time_limit_seconds; solver.parameters.num_search_workers=8
    status=solver.Solve(model)
    if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE): return greedy_allocate(df,budgets)
    chosen={}; contacts_used=0; incentive_used=0.; caps={a:0 for a in budgets.per_action_daily_cap}
    for i,v in zip(candidates.index,vars):
        if solver.Value(v):
            r=df.loc[i].to_dict(); chosen[r['case_id']]=r; contacts_used+=int(r['requires_customer_contact']); incentive_used+=float(r['incentive_cost_inr']); caps[r['action']]=caps.get(r['action'],0)+1
    rows=[]
    for cid,g in df.groupby('case_id'):
        if cid in chosen:r=chosen[cid]; r['rejected_reason']=None; rows.append(r)
        else:rows.append({'case_id':cid,'action':'NO_ACTION','estimated_uplift':0.,'expected_incremental_value_inr':0.,'action_cost_inr':0.,'incentive_cost_inr':0.,'requires_customer_contact':False,'rejected_reason':'NO_POSITIVE_UPLIFT_OR_BUDGET'})
    return pd.DataFrame(rows),{'contacts_used':contacts_used,'contacts_budget':budgets.daily_contacts,'incentive_used_inr':round(incentive_used,2),'incentive_budget_inr':budgets.daily_incentive_inr,'action_counts':caps,'solver':'cp-sat','solver_status':solver.StatusName(status),'objective_inr':round(solver.ObjectiveValue()/SCALE,2)}

def greedy_allocate(scored_df,budgets):
    selected,contacts,incentive,caps,solver=_fallback_greedy(scored_df,budgets)
    rows=[]
    for cid,g in scored_df.groupby('case_id'):
        if cid in selected:r=selected[cid]; r['rejected_reason']=None; rows.append(r)
        else:rows.append({'case_id':cid,'action':'NO_ACTION','estimated_uplift':0.,'expected_incremental_value_inr':0.,'action_cost_inr':0.,'incentive_cost_inr':0.,'requires_customer_contact':False,'rejected_reason':'NO_POSITIVE_UPLIFT_OR_BUDGET'})
    return pd.DataFrame(rows),{'contacts_used':contacts,'contacts_budget':budgets.daily_contacts,'incentive_used_inr':round(incentive,2),'incentive_budget_inr':budgets.daily_incentive_inr,'action_counts':caps,'solver':solver}
