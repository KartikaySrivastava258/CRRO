"""
Deterministic Policy / Guardrail Engine (Spec Sections 12, 24, 78-79)
------------------------------------------------------------------------
This is a pure, deterministic, non-LLM gate. It runs AFTER the optimizer
selects an action and BEFORE execution. It can downgrade a selection to
NO_ACTION with a reason code, or route to human approval.

"AI reasons; deterministic systems control money." (Section 84)
"""

from dataclasses import dataclass
from typing import Optional
from .action_registry import ACTION_REGISTRY, REASON_CODES


@dataclass
class PolicyDecision:
    approved: bool
    requires_human_approval: bool
    reason_code: Optional[str]  # None if approved outright


def evaluate_policy(case_row, action_name, expected_incremental_value,
                     customer_state, opted_out_customers):
    """
    customer_state: dict with keys 'attempts_used', 'contacts_used'
    opted_out_customers: set of customer_ids
    """
    spec = ACTION_REGISTRY[action_name]

    if action_name == "NO_ACTION":
        return PolicyDecision(True, False, None)

    if case_row["customer_id"] in opted_out_customers and spec.requires_customer_contact:
        return PolicyDecision(False, False, "CUSTOMER_OPTED_OUT")

    if customer_state["attempts_used"] >= spec.max_attempts:
        return PolicyDecision(False, False, "MAX_ATTEMPTS")

    if expected_incremental_value is not None and expected_incremental_value <= 0:
        return PolicyDecision(False, False, "NO_POSITIVE_UPLIFT")

    if any(k not in case_row or case_row[k] is None for k in
           ("amount_inr", "payment_method", "failure_reason")):
        return PolicyDecision(False, False, "MISSING_DATA")

    if spec.requires_approval_above_amount is not None and \
            case_row["amount_inr"] > spec.requires_approval_above_amount:
        return PolicyDecision(True, True, "APPROVAL_PENDING")

    return PolicyDecision(True, False, None)
