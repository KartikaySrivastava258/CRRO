from dataclasses import dataclass, field
from .action_registry import ACTION_REGISTRY
@dataclass
class Budgets:
    daily_contacts:int=4000; daily_incentive_inr:float=100000; per_action_daily_cap:dict=field(default_factory=lambda:{"ALTERNATIVE_PAYMENT_METHOD":1000})

def compute_expected_incremental_value(scored_df,case_amounts,incentive_pct=.02):
    df=scored_df.merge(case_amounts,on="case_id",how="left"); df["action_cost_inr"]=df.action.map(lambda a:ACTION_REGISTRY[a].monetary_cost_inr); df["recoverable_amount_inr"]=df.amount_inr
    df["incentive_pct"]=df.action.map(lambda a:min(incentive_pct,ACTION_REGISTRY[a].max_incentive_pct) if ACTION_REGISTRY[a].is_incentive else 0); df["incentive_cost_inr"]=df.amount_inr*df.incentive_pct
    df["contact_cost_inr"]=df.action.map(lambda a:1.0 if ACTION_REGISTRY[a].requires_customer_contact else 0.0); df["expected_incremental_value_inr"]=df.estimated_uplift*df.amount_inr-df.action_cost_inr-df.incentive_cost_inr
    return df

def greedy_allocate(scored_df,budgets):
    df=scored_df.copy(); df["requires_customer_contact"]=df.action.map(lambda a:ACTION_REGISTRY[a].requires_customer_contact); selected={}; contacts=0; incentive=0.; caps={a:0 for a in budgets.per_action_daily_cap}
    for _,r in df[df.expected_incremental_value_inr>0].sort_values("expected_incremental_value_inr",ascending=False).iterrows():
        if r.case_id in selected or (r.requires_customer_contact and contacts>=budgets.daily_contacts) or incentive+r.incentive_cost_inr>budgets.daily_incentive_inr: continue
        cap=budgets.per_action_daily_cap.get(r.action)
        if cap is not None and caps[r.action]>=cap: continue
        selected[r.case_id]=r.to_dict(); contacts+=int(r.requires_customer_contact); incentive+=float(r.incentive_cost_inr); caps[r.action]=caps.get(r.action,0)+1
    rows=[]
    for cid,g in df.groupby("case_id"):
        if cid in selected: r=selected[cid]; r["rejected_reason"]=None; rows.append(r)
        else: rows.append({"case_id":cid,"action":"NO_ACTION","estimated_uplift":0.,"expected_incremental_value_inr":0.,"action_cost_inr":0.,"incentive_cost_inr":0.,"requires_customer_contact":False,"rejected_reason":"NO_POSITIVE_UPLIFT_OR_BUDGET"})
    return __import__("pandas").DataFrame(rows),{"contacts_used":contacts,"contacts_budget":budgets.daily_contacts,"incentive_used_inr":round(incentive,2),"incentive_budget_inr":budgets.daily_incentive_inr,"action_counts":caps}
