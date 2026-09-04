"""
Execution Adapter + Audit Trail (Spec Sections 12, 71-73, 76, 78-79)
------------------------------------------------------------------------
Simulates a Razorpay test-mode / synthetic execution adapter. Real
Razorpay test-mode credentials can be dropped into `RazorpayAdapter`
later without touching the rest of the pipeline (adapter pattern keeps
the LLM and optimizer fully decoupled from any financial API).

Guarantees demonstrated here:
  - idempotency (duplicate-action rate = 0, Section 76)
  - every executed action produces an AuditEvent
  - outcome is drawn from the SAME hidden ground-truth generator used to
    label the dataset, i.e. "actual outcome" is genuinely realized, not
    assumed equal to the predicted probability.
"""

import uuid
import time
import numpy as np
import pandas as pd


class SimulatedExecutionAdapter:
    """Stand-in for Razorpay test-mode API + customer-contact channel."""

    def __init__(self, seed=42, timeout_rate=0.01, provider_error_rate=0.015):
        self.rng = np.random.default_rng(seed)
        self.timeout_rate = timeout_rate
        self.provider_error_rate = provider_error_rate
        self._seen_idempotency_keys = set()

    def execute(self, case_id, action, idempotency_key):
        if idempotency_key in self._seen_idempotency_keys:
            return {"status": "SKIPPED_DUPLICATE", "idempotency_key": idempotency_key}
        self._seen_idempotency_keys.add(idempotency_key)

        r = self.rng.uniform()
        if r < self.timeout_rate:
            return {"status": "EXECUTION_TIMEOUT", "idempotency_key": idempotency_key}
        if r < self.timeout_rate + self.provider_error_rate:
            return {"status": "PROVIDER_ERROR", "idempotency_key": idempotency_key}
        return {"status": "EXECUTED", "idempotency_key": idempotency_key}


class AuditLog:
    def __init__(self):
        self.events = []

    def record(self, case_id, event_type, payload):
        self.events.append({
            "event_id": str(uuid.uuid4()),
            "ts": time.time(),
            "case_id": case_id,
            "event_type": event_type,
            "payload": payload,
        })

    def to_df(self):
        return pd.DataFrame(self.events)


def run_execution(allocation_df, cases_df, adapter: SimulatedExecutionAdapter,
                   audit: AuditLog, policy_decisions: dict, model_version):
    """
    For each allocated case, run the deterministic policy check result
    (already computed upstream), execute via the adapter, and realize a
    ground-truth outcome using the true hidden probability so we can
    measure ACTUAL recovered value (Section 8, 68-69).
    """
    cases_idx = cases_df.set_index("case_id")
    results = []
    for _, row in allocation_df.iterrows():
        cid = row["case_id"]
        action = row["action"]
        decision = policy_decisions.get(cid)

        audit.record(cid, "DECISION_RECORDED", {
            "action": action,
            "expected_incremental_value_inr": row.get("expected_incremental_value_inr"),
            "model_version": model_version,
        })

        if action == "NO_ACTION":
            audit.record(cid, "NO_ACTION_TAKEN", {"reason": row.get("rejected_reason")})
            results.append({"case_id": cid, "action": action, "exec_status": "NOT_EXECUTED",
                             "actual_recovered": None, "recovered_amount_inr": 0.0})
            continue

        if decision is not None and not decision.approved:
            audit.record(cid, "ACTION_BLOCKED", {"reason": decision.reason_code})
            results.append({"case_id": cid, "action": action, "exec_status": decision.reason_code,
                             "actual_recovered": None, "recovered_amount_inr": 0.0})
            continue

        if decision is not None and decision.requires_human_approval:
            audit.record(cid, "PENDING_HUMAN_APPROVAL", {"reason": decision.reason_code})
            results.append({"case_id": cid, "action": action, "exec_status": "APPROVAL_PENDING",
                             "actual_recovered": None, "recovered_amount_inr": 0.0})
            continue

        idem_key = f"{cid}:{action}:v1"
        exec_result = adapter.execute(cid, action, idem_key)
        audit.record(cid, "ACTION_EXECUTED", exec_result)

        if exec_result["status"] != "EXECUTED":
            results.append({"case_id": cid, "action": action, "exec_status": exec_result["status"],
                             "actual_recovered": None, "recovered_amount_inr": 0.0})
            continue

        true_p = cases_idx.loc[cid, "_true_p_recovery"]
        recovered = int(adapter.rng.uniform() < true_p)
        amt = cases_idx.loc[cid, "amount_inr"] if recovered else 0.0
        audit.record(cid, "OUTCOME_OBSERVED", {"recovered": bool(recovered), "amount_inr": amt})
        results.append({"case_id": cid, "action": action, "exec_status": "EXECUTED",
                         "actual_recovered": bool(recovered), "recovered_amount_inr": amt})

    return pd.DataFrame(results)
