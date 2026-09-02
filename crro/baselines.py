import pandas as pd
def control_policy(df): return pd.DataFrame({"case_id":df.case_id,"action":"NO_ACTION"})
def fixed_retry_policy(df): return pd.DataFrame({"case_id":df.case_id,"action":["RETRY_NOW" if a<200000 else "NO_ACTION" for a in df.amount_inr]})
def rule_based_policy(df):
    actions=[]
    for _,r in df.iterrows():
        if r.failure_reason=="network_error": a="RETRY_NOW"
        elif r.failure_reason=="insufficient_funds": a="PAYMENT_LINK"
        elif r.payment_method=="card": a="ALTERNATIVE_PAYMENT_METHOD"
        else: a="CUSTOMER_NUDGE"
        if r.attempt_count>=3 and a in ("RETRY_NOW","RETRY_LATER"): a="CUSTOMER_NUDGE"
        actions.append(a)
    return pd.DataFrame({"case_id":df.case_id,"action":actions})
def raw_probability_policy(scored):
    x=scored.sort_values("recovery_probability",ascending=False).drop_duplicates("case_id"); return x[["case_id","action"]]
def uplift_only_policy(scored):
    x=scored.sort_values("estimated_uplift",ascending=False).drop_duplicates("case_id"); return x[x.estimated_uplift>0][["case_id","action"]]
def per_case_optimal_policy(test,scored):
    x=scored.sort_values("expected_incremental_value_inr",ascending=False).drop_duplicates("case_id"); return x[x.expected_incremental_value_inr>0][["case_id","action"]]
