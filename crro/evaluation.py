import numpy as np,pandas as pd
from .oracle import true_p_recovery
from .action_registry import ACTION_REGISTRY

def add_common_random_numbers(df,seed=777): out=df.copy(); out["_u"]=np.random.default_rng(seed).uniform(size=len(out)); return out

def realize_policy_outcomes(test_df,assignment,shift=True):
    m=test_df.merge(assignment[["case_id","action"]],on="case_id",how="left"); m.action=m.action.fillna("NO_ACTION"); p=true_p_recovery(m,m.action.to_numpy(),shift=shift); rec=(m._u.to_numpy()<p).astype(int)
    cost=m.action.map(lambda a:ACTION_REGISTRY[a].monetary_cost_inr).to_numpy(); inc=m.amount_inr.to_numpy()*m.action.map(lambda a:.02 if ACTION_REGISTRY[a].is_incentive else 0).to_numpy(); gross=rec*m.amount_inr.to_numpy(); net=gross-cost-inc
    out=m[["case_id","action","amount_inr","failure_reason","payment_method","merchant_category"]].copy(); out["recovered"]=rec; out["recovered_amount_inr"]=gross; out["action_cost_inr"]=cost; out["incentive_cost_inr"]=inc; out["net_value_inr"]=net; return out

def summarize_policy(df,name): return {"policy":name,"n_cases":len(df),"recovery_rate":round(float(df.recovered.mean()),4),"gross_recovered_inr":round(float(df.recovered_amount_inr.sum()),2),"total_action_cost_inr":round(float((df.action_cost_inr+df.incentive_cost_inr).sum()),2),"net_recovered_inr":round(float(df.net_value_inr.sum()),2),"actions_taken":int((df.action!="NO_ACTION").sum())}

def segment_performance(df,by="failure_reason"): return df.groupby(by).agg(n=("case_id","count"),recovery_rate=("recovered","mean"),net_value_inr=("net_value_inr","sum")).round(4).reset_index()
