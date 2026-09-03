import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from crro.action_registry import ACTION_REGISTRY
from crro.data_generator import generate_dataset, eligible_actions_for
from crro.model import UpliftModel
from crro.optimizer import compute_expected_incremental_value, Budgets, greedy_allocate, exact_allocate
from crro.evaluation import add_common_random_numbers, realize_policy_outcomes
from crro.baselines import control_policy
from crro.executor import SimulatedExecutionAdapter
from crro.oracle import true_p_recovery
from app.db import Base
from app.customer_state import CustomerRecoveryState, CustomerStateService


def test_control_is_real_baseline():
    d = generate_dataset(500, 1)
    d["eligible_actions"] = d.apply(eligible_actions_for, axis=1)
    d = add_common_random_numbers(d)
    out = realize_policy_outcomes(d, control_policy(d), True)
    assert 0 <= out.recovered.mean() <= 1


def test_model_and_optimizer_constraints():
    d = generate_dataset(2000, 2)
    d["eligible_actions"] = d.apply(eligible_actions_for, axis=1)
    m = UpliftModel().fit(d)
    s = compute_expected_incremental_value(
        m.score_all_actions(d), d[["case_id", "amount_inr"]]
    )
    a, u = greedy_allocate(s, Budgets(100, 1000))
    assert len(a) == len(d)
    assert u["contacts_used"] <= 100
    assert u["incentive_used_inr"] <= 1000


def test_provider_idempotency():
    p = SimulatedExecutionAdapter()
    first = p.execute("R-1", "RETRY_NOW", "same-key")
    second = p.execute("R-1", "RETRY_NOW", "same-key")
    assert first.status == "EXECUTED"
    assert second.status == "SKIPPED_DUPLICATE"


def test_exact_allocator_uses_action_registry_constraints():
    rows = []
    for i, action in enumerate(["PAYMENT_LINK", "CUSTOMER_NUDGE", "RETRY_NOW"]):
        rows.append(
            {
                "case_id": f"R-{i}",
                "action": action,
                "estimated_uplift": 0.20,
                "amount_inr": 10000.0,
                "incentive_cost_inr": 0.0,
                "expected_incremental_value_inr": 1000.0 - i,
                "customer_id": "same-customer",
            }
        )
    scored = pd.DataFrame(rows)
    allocation, usage = exact_allocate(scored, Budgets(daily_contacts=1, daily_incentive_inr=1000))
    selected = allocation[allocation.action != "NO_ACTION"]
    assert len(selected) == 1
    chosen = selected.iloc[0].action
    assert chosen in ACTION_REGISTRY
    assert usage["contacts_used"] <= 1


def test_shifted_oracle_is_writable_and_returns_probability():
    d = generate_dataset(20, 3)
    p = true_p_recovery(d, ["NO_ACTION"] * len(d), shift=True)
    assert len(p) == len(d)
    assert ((p >= 0) & (p <= 1)).all()


def test_customer_state_is_merchant_scoped_and_persists_limits():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[CustomerRecoveryState.__table__])
    with Session(engine) as db:
        service = CustomerStateService(db)
        service.record("cust-1", "merchant-a", "CUSTOMER_NUDGE", cooldown_minutes=0)
        a = service.get("cust-1", "merchant-a")
        b = service.get("cust-1", "merchant-b")
        assert a.attempt_count == 1
        assert b.attempt_count == 0
        allowed, reason = service.eligible("cust-1", "CUSTOMER_NUDGE", "merchant-a", max_attempts=1, cooldown_minutes=0)
        assert not allowed
        assert reason == "MAX_ATTEMPTS"
