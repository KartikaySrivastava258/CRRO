import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from .action_registry import ACTION_REGISTRY
FEATURES=["amount_inr","attempt_count","minutes_since_failure","customer_success_rate","customer_previous_recovery_rate","time_of_day","failure_reason","payment_method","merchant_category"]
MODEL_VERSION="uplift_tlearner_v2"
class UpliftModel:
    def __init__(self): self.models={}; self.actions=list(ACTION_REGISTRY)
    def _pipe(self):
        cat=["failure_reason","payment_method","merchant_category"]; num=[x for x in FEATURES if x not in cat]
        prep=ColumnTransformer([("num",Pipeline([("impute",SimpleImputer(strategy="median")),("scale",StandardScaler())]),num),("cat",Pipeline([("impute",SimpleImputer(strategy="most_frequent")),("onehot",OneHotEncoder(handle_unknown="ignore"))]),cat)])
        return Pipeline([("prep",prep),("model",LogisticRegression(max_iter=500,class_weight="balanced"))])
    def fit(self,df):
        for action in self.actions:
            sub=df[df.assigned_action==action]
            if len(sub)>=20:
                pipe=self._pipe(); pipe.fit(sub[FEATURES],sub.outcome_recovered); self.models[action]=pipe
        return self
    def predict_proba(self,df,action): return self.models[action].predict_proba(df[FEATURES])[:,1] if action in self.models else np.zeros(len(df))
    def score_all_actions(self,df,eligible_col="eligible_actions"):
        base=self.predict_proba(df,"NO_ACTION"); rows=[]
        for action in self.actions:
            if action=="NO_ACTION" or action not in self.models: continue
            p=self.predict_proba(df,action); mask=df[eligible_col].apply(lambda x: action in x).to_numpy()
            for i in np.where(mask)[0]: rows.append({"case_id":df.iloc[i].case_id,"action":action,"recovery_probability":float(p[i]),"baseline_probability":float(base[i]),"estimated_uplift":float(p[i]-base[i]),"model_version":MODEL_VERSION})
        return pd.DataFrame(rows)
