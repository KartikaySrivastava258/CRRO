from crro.data_generator import generate_dataset,eligible_actions_for
from crro.model import UpliftModel
from crro.optimizer import compute_expected_incremental_value,Budgets,greedy_allocate
from crro.evaluation import add_common_random_numbers,realize_policy_outcomes
from crro.baselines import control_policy

def test_control_is_real_baseline():
    d=generate_dataset(500,1); d['eligible_actions']=d.apply(eligible_actions_for,axis=1); d=add_common_random_numbers(d); out=realize_policy_outcomes(d,control_policy(d),True); assert out.recovered.sum()>=0

def test_model_and_optimizer():
    d=generate_dataset(2000,2); d['eligible_actions']=d.apply(eligible_actions_for,axis=1); m=UpliftModel().fit(d); s=compute_expected_incremental_value(m.score_all_actions(d),d[['case_id','amount_inr']]); a,u=greedy_allocate(s,Budgets(100,1000)); assert len(a)==len(d); assert u['contacts_used']<=100; assert u['incentive_used_inr']<=1000
