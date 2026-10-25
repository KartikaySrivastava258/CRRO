"""
Evidence / Retrieval Layer
------------------------------------------------------
Builds the grounded context the AI Recovery Analyst reasons over, so it
is never operating on a raw database row alone. Kept intentionally
lightweight (direct SQL lookups against the same Postgres tables used
elsewhere) rather than a vector-store/ANN pipeline -- swappable later,
same as the doc allows ("can remain lightweight and replaceable").

Every evidence bullet returned here is grounded in an actual persisted
fact (a real query result). The LLM prompt builder (llm_analyst.py)
must not add anything the evidence layer didn't supply.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from .db import get_session, db_is_reachable
from .db_models import CustomerActionHistory, PaymentCase
from .action_registry import ACTION_REGISTRY


@dataclass
class Evidence:
    bullets: List[str] = field(default_factory=list)
    prior_cases_for_customer: int = 0
    prior_recoveries_for_customer: int = 0
    prior_actions_tried: List[str] = field(default_factory=list)
    merchant_allowed_actions: List[str] = field(default_factory=list)
    source: str = "postgres"   # or "none_db_unavailable"


def build_evidence(case_row, eligible_actions: List[str]) -> Evidence:
    """
    case_row: a dict/Series with at least customer_id, failure_reason,
    payment_method, amount_inr.
    """
    if not db_is_reachable():
        return Evidence(
            bullets=["No historical customer data available (database not connected)."],
            merchant_allowed_actions=eligible_actions,
            source="none_db_unavailable",
        )

    s = get_session()
    try:
        cust_id = case_row["customer_id"]
        history = (
            s.query(CustomerActionHistory)
            .filter(CustomerActionHistory.customer_id == cust_id)
            .order_by(CustomerActionHistory.timestamp.desc())
            .limit(20)
            .all()
        )
        prior_cases = (
            s.query(PaymentCase)
            .filter(PaymentCase.customer_id == cust_id)
            .all()
        )
        prior_recoveries = sum(1 for c in prior_cases if c.status == "RECOVERED")
        prior_actions_tried = sorted({h.action for h in history if h.action})

        bullets = []
        if prior_cases:
            bullets.append(
                f"Customer has {len(prior_cases)} prior recovery case(s) on record, "
                f"{prior_recoveries} recovered."
            )
        else:
            bullets.append("No prior recovery cases on record for this customer.")

        if prior_actions_tried:
            bullets.append(
                f"Previously attempted action(s) for this customer: {', '.join(prior_actions_tried)}."
            )

        same_reason_recovered = [
            c for c in prior_cases
            if c.failure_reason == case_row["failure_reason"] and c.status == "RECOVERED"
        ]
        if same_reason_recovered:
            bullets.append(
                f"Customer has previously recovered from a '{case_row['failure_reason']}' "
                f"failure before."
            )

        bullets.append(
            f"Merchant policy currently allows these actions for this case: "
            f"{', '.join(a for a in eligible_actions if a != 'NO_ACTION')}."
        )

        return Evidence(
            bullets=bullets,
            prior_cases_for_customer=len(prior_cases),
            prior_recoveries_for_customer=prior_recoveries,
            prior_actions_tried=prior_actions_tried,
            merchant_allowed_actions=eligible_actions,
            source="postgres",
        )
    finally:
        s.close()
