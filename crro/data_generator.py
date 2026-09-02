import numpy as np
import pandas as pd
from .oracle import true_p_recovery
FAILURE_REASONS=["insufficient_funds","bank_decline","network_error","risk_block","otp_timeout","card_expired"]
PAYMENT_METHODS=["upi","card","netbanking","wallet"]
MERCHANT_CATEGORIES=["ecommerce","subscription","travel","food_delivery","edtech"]
ACTIONS=["NO_ACTION","RETRY_NOW","RETRY_LATER","PAYMENT_LINK","ALTERNATIVE_PAYMENT_METHOD","CUSTOMER_NUDGE","SMALL_INCENTIVE"]

def _sigmoid(x): return 1/(1+np.exp(-np.clip(x,-30,30)))

def generate_dataset(n, seed, shift=False, n_merchants=8, n_customers=4000):
    rng=np.random.default_rng(seed); merchant_id=rng.integers(0,n_merchants,n); customer_id=rng.integers(0,n_customers,n); latent_table=rng.normal(0,1,n_customers); latent=latent_table[customer_id]
    reason=rng.choice(FAILURE_REASONS,n,p=[.30,.22,.14,.10,.14,.10]); method=rng.choice(PAYMENT_METHODS,n,p=[.45,.30,.15,.10]); category=rng.choice(MERCHANT_CATEGORIES,n)
    amount=np.round(np.exp(rng.normal(8.2,.9,n))).clip(100,200000); attempts=rng.integers(1,4,n); minutes=rng.integers(1,4320,n); tod=rng.integers(0,24,n)
    success=np.clip(_sigmoid(latent+rng.normal(0,.4,n)),.02,.98); prev=np.clip(_sigmoid(.7*latent+rng.normal(0,.5,n)),.02,.98); action=[]
    for a in amount: action.append(rng.choice(ACTIONS[:-1]+(["SMALL_INCENTIVE"] if a<20000 else [])))
    partial=pd.DataFrame({"failure_reason":reason,"payment_method":method,"amount_inr":amount,"attempt_count":attempts,"minutes_since_failure":minutes,"merchant_category":category,"_latent_trait":latent})
    p=true_p_recovery(partial,action,shift=shift); outcome=(rng.random(n)<p).astype(int)
    return pd.DataFrame({"case_id":[f"R-{i:06d}" for i in range(n)],"merchant_id":[f"merchant_{m:03d}" for m in merchant_id],"customer_id":[f"cust_{c:05d}" for c in customer_id],"amount_inr":amount.round(2),"payment_method":method,"failure_reason":reason,"attempt_count":attempts,"minutes_since_failure":minutes,"customer_success_rate":success.round(4),"customer_previous_recovery_rate":prev.round(4),"merchant_category":category,"time_of_day":tod,"assigned_action":action,"outcome_recovered":outcome,"_latent_trait":latent,"_true_p_recovery":p,"_shift":shift})

def eligible_actions_for(row):
    pool=ACTIONS.copy()
    if row["amount_inr"]>=20000: pool.remove("SMALL_INCENTIVE")
    if row["attempt_count"]>=3: pool=[a for a in pool if a not in ("RETRY_NOW","RETRY_LATER")]
    return pool
