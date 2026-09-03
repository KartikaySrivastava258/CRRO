import numpy as np
from .data_generator import generate_dataset,eligible_actions_for
from .model import UpliftModel
from .optimizer import Budgets,compute_expected_incremental_value,exact_allocate
from .evaluation import realize_policy_outcomes,summarize_policy,add_common_random_numbers
from .baselines import control_policy,fixed_retry_policy,rule_based_policy,raw_probability_policy,uplift_only_policy
from .causal_metrics import evaluate_uplift

def benchmark_seed(seed,n=1500,contacts=400,incentive_budget=20000):
    train=generate_dataset(max(10000,n),seed+100); test=generate_dataset(n,seed+200,shift=True)
    for d in (train,test):d['eligible_actions']=d.apply(eligible_actions_for,axis=1)
    model=UpliftModel().fit(train); scored=compute_expected_incremental_value(model.score_all_actions(test),test[['case_id','amount_inr','customer_id']]); scored['customer_id']=scored.case_id.map(test.set_index('case_id').customer_id)
    allocation,usage=exact_allocate(scored,Budgets(contacts,incentive_budget,{'ALTERNATIVE_PAYMENT_METHOD':contacts//2}))
    test=add_common_random_numbers(test,seed+500)
    policies={'CONTROL':control_policy(test),'FIXED_RETRY':fixed_retry_policy(test),'RULE_BASED':rule_based_policy(test),'RAW_PROBABILITY':raw_probability_policy(scored),'UPLIFT_ONLY':uplift_only_policy(scored),'CRRO':allocation}
    metrics={name:summarize_policy(realize_policy_outcomes(test,p,True),name) for name,p in policies.items()}
    uplift=evaluate_uplift(model,scored,test,allocation)
    return {'seed':seed,'metrics':metrics,'uplift':uplift,'budget':usage}

def multi_seed_benchmark(seeds=range(10),n=1500):
    runs=[benchmark_seed(int(s),n=n) for s in seeds]; policies=sorted(runs[0]['metrics']); out={}
    for p in policies:
        vals=np.array([r['metrics'][p]['net_recovered_inr'] for r in runs]); mean=float(vals.mean()); se=float(vals.std(ddof=1)/np.sqrt(len(vals))); out[p]={'mean_net_recovered_inr':round(mean,2),'std_inr':round(float(vals.std(ddof=1)),2),'ci95_low':round(mean-1.96*se,2),'ci95_high':round(mean+1.96*se),'n_seeds':len(vals)}
    return {'policies':out,'runs':runs}

def ablation(seed=2026,n=2000):
    full=benchmark_seed(seed,n=n); m=full['metrics']; cr=m['CRRO']['net_recovered_inr']; return {'baseline':m,'incremental_lift_vs_control':round(cr-m['CONTROL']['net_recovered_inr'],2),'incremental_lift_vs_raw_probability':round(cr-m['RAW_PROBABILITY']['net_recovered_inr'],2),'incremental_lift_vs_uplift_only':round(cr-m['UPLIFT_ONLY']['net_recovered_inr'],2)}
