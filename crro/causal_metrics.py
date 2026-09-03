import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score
from .oracle import true_p_recovery

def _case_truth(test):
    rows=[]
    for _,r in test.iterrows():
        base=float(true_p_recovery(r,'NO_ACTION',shift=True)); best=0.0
        for a in r.get('eligible_actions',[]):
            if a!='NO_ACTION': best=max(best,float(true_p_recovery(r,a,shift=True))-base)
        rows.append((r.case_id,base,best))
    return pd.DataFrame(rows,columns=['case_id','true_base','true_best_uplift'])

def auuc(scored,test):
    if scored.empty:return 0.0
    est=scored.groupby('case_id').estimated_uplift.max().rename('estimated_uplift').reset_index(); truth=_case_truth(test); m=est.merge(truth,on='case_id').sort_values('estimated_uplift',ascending=False)
    if m.empty:return 0.0
    y=m.true_best_uplift.to_numpy(); curve=np.cumsum(y)/np.arange(1,len(y)+1); x=np.arange(1,len(y)+1)/len(y)
    return float(np.trapezoid(curve,x))

def uplift_at_k(scored,test,k=.2):
    if scored.empty:return 0.0
    est=scored.groupby('case_id').estimated_uplift.max().sort_values(ascending=False); ids=est.head(max(1,int(len(est)*k))).index; truth=_case_truth(test)
    return float(truth[truth.case_id.isin(ids)].true_best_uplift.mean())

def qini_curve(scored,test,points=(.1,.2,.3,.5,1.0)):
    return [{'fraction':p,'uplift':round(uplift_at_k(scored,test,p),6)} for p in points]

def factual_calibration(model,test):
    rows=[]
    for action in model.actions:
        m=test[test.assigned_action==action]
        if len(m)<20 or action not in model.models:continue
        p=model.predict_proba(m,action); rows.append({'action':action,'n':len(m),'brier':round(float(brier_score_loss(m.outcome_recovered,p)),6),'auc':round(float(roc_auc_score(m.outcome_recovered,p)),6) if m.outcome_recovered.nunique()>1 else None})
    return rows

def policy_regret(policy_df,test):
    truth=_case_truth(test); lookup=test.set_index('case_id'); rows=[]
    for _,r in policy_df.iterrows():
        t=lookup.loc[r.case_id]; chosen=float(true_p_recovery(t,r.action,shift=True))-float(truth.loc[truth.case_id==r.case_id,'true_base'].iloc[0]); best=float(truth.loc[truth.case_id==r.case_id,'true_best_uplift'].iloc[0]); rows.append((best-chosen)*float(t.amount_inr))
    arr=np.array(rows); return {'total_regret_inr':round(float(arr.sum()),2),'mean_regret_inr':round(float(arr.mean()),2),'zero_regret_rate':round(float((np.abs(arr)<1e-9).mean()),4)}

def evaluate_uplift(model,scored,test,policy=None):
    curve=qini_curve(scored,test); out={'auuc':round(auuc(scored,test),6),'uplift_at_10':curve[0]['uplift'],'uplift_at_20':curve[1]['uplift'],'uplift_at_50':curve[3]['uplift'],'qini_curve':curve,'calibration':factual_calibration(model,test)}
    if policy is not None:out['regret']=policy_regret(policy,test)
    return out
