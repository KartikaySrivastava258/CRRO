import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score

def uplift_at_k(scored, test, k=0.2):
    """Qini-style policy uplift using randomized factual outcomes."""
    if scored.empty:return 0.0
    rank=scored.groupby('case_id').estimated_uplift.max().sort_values(ascending=False)
    ids=rank.head(max(1,int(len(rank)*k))).index
    m=test[test.case_id.isin(ids)].copy(); m['treated']=m.assigned_action!='NO_ACTION'
    if m.empty:return 0.0
    treated=m[m.treated].outcome_recovered.mean() if (m.treated).any() else 0
    control=m[~m.treated].outcome_recovered.mean() if (~m.treated).any() else 0
    return float(treated-control)

def policy_value(df): return float(df.net_value_inr.sum())

def qini_curve(scored,test,points=(.1,.2,.3,.5,1.0)):
    return [{'fraction':p,'uplift':round(uplift_at_k(scored,test,p),6)} for p in points]

def factual_calibration(model,test):
    rows=[]
    for action in model.actions:
        m=test[test.assigned_action==action]
        if len(m)<20 or action not in model.models: continue
        p=model.predict_proba(m,action)
        rows.append({'action':action,'n':len(m),'brier':round(float(brier_score_loss(m.outcome_recovered,p)),6),'auc':round(float(roc_auc_score(m.outcome_recovered,p)),6) if m.outcome_recovered.nunique()>1 else None})
    return rows

def evaluate_uplift(model,scored,test):
    curve=qini_curve(scored,test); return {'uplift_at_10':curve[0]['uplift'],'uplift_at_20':curve[1]['uplift'],'uplift_at_50':curve[3]['uplift'],'qini_curve':curve,'calibration':factual_calibration(model,test)}
