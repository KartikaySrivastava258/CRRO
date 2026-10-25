"""
Basic test suite.
Run: pytest tests/ -v

This is NOT the full CI/integration/failure-injection suite the gap
analysis calls for (Section 21, Phase 7) -- it covers the highest-risk
correctness properties cheaply: oracle consistency, NO_ACTION realism,
optimizer feasibility/budget respect, and idempotency.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import pytest

from crro.data_generator import generate_dataset, eligible_actions_for
from crro.oracle import true_p_recovery
from crro.action_registry import ACTION_REGISTRY
from crro.optimizer import Budgets, compute_expected_incremental_value, greedy_allocate, exact_allocate
from crro.executor import SimulatedExecutionAdapter, AuditLog, run_execution
from crro.evaluate import realize_policy_outcomes, add_common_random_numbers, incremental_vs_control
from crro.baselines import control_policy


@pytest.fixture(scope="module")
def small_dataset():
    df = generate_dataset(500, seed=1, shift=False)
    df["eligible_actions"] = df.apply(eligible_actions_for, axis=1)
    return df


def test_no_action_has_nonzero_true_probability(small_dataset):
    """
    Regression test for the P0 bug: NO_ACTION
    must have a genuine, non-degenerate probability under the oracle --
    it must NOT be silently forced to 0.
    """
    p = true_p_recovery(small_dataset, np.full(len(small_dataset), "NO_ACTION", dtype=object))
    assert p.min() > 0.0
    assert p.max() < 1.0
    assert 0.05 < p.mean() < 0.6, f"unrealistic control recovery rate: {p.mean()}"


def test_actions_have_nonnegative_uplift_on_average(small_dataset):
    """Every real action should, on average, not perform worse than NO_ACTION."""
    p_control = true_p_recovery(small_dataset, np.full(len(small_dataset), "NO_ACTION", dtype=object))
    for a in ACTION_REGISTRY:
        if a == "NO_ACTION":
            continue
        p_a = true_p_recovery(small_dataset, np.full(len(small_dataset), a, dtype=object))
        assert p_a.mean() >= p_control.mean() - 0.05, f"{a} average uplift unexpectedly negative"


def test_control_policy_realizes_nonzero_recovery():
    """
    Regression test: realize_policy_outcomes must NOT force recovered=0
    for NO_ACTION (the exact P0 bug being fixed).
    """
    df = generate_dataset(2000, seed=5, shift=True)
    df = add_common_random_numbers(df, seed=99)
    alloc = control_policy(df)
    realized = realize_policy_outcomes(df, alloc, shift=True)
    assert realized["recovered"].sum() > 0, "CONTROL must recover SOME cases under the real oracle"
    assert realized["recovered_amount_inr"].sum() > 0


def test_incremental_vs_control_is_zero_for_control_itself():
    df = generate_dataset(1000, seed=6, shift=True)
    df = add_common_random_numbers(df, seed=100)
    alloc = control_policy(df)
    realized = realize_policy_outcomes(df, alloc, shift=True)
    inc = incremental_vs_control(realized, realized)
    assert inc["incremental_recovered_inr"] == 0
    assert inc["incremental_net_value_inr"] == 0


def test_optimizer_respects_contact_budget(small_dataset):
    """Neither greedy nor exact optimizer may allocate more contacts than the budget."""
    from crro.model import UpliftModel
    train = generate_dataset(3000, seed=10)
    model = UpliftModel().fit(train)
    scored = model.score_all_actions(small_dataset, "eligible_actions")
    scored = compute_expected_incremental_value(scored, small_dataset)

    budgets = Budgets(daily_contacts=10, daily_incentive_inr=5000, per_customer_daily_contact_cap=1)
    for allocator in (greedy_allocate, exact_allocate):
        alloc, usage = allocator(scored, budgets)
        contacted = alloc[alloc["action"] != "NO_ACTION"]
        contact_actions = contacted[contacted["action"].apply(
            lambda a: ACTION_REGISTRY[a].requires_customer_contact)]
        assert len(contact_actions) <= budgets.daily_contacts, \
            f"{allocator.__name__} exceeded contact budget: {len(contact_actions)} > {budgets.daily_contacts}"


def test_optimizer_never_allocates_negative_value_action(small_dataset):
    from crro.model import UpliftModel
    train = generate_dataset(3000, seed=11)
    model = UpliftModel().fit(train)
    scored = model.score_all_actions(small_dataset, "eligible_actions")
    scored = compute_expected_incremental_value(scored, small_dataset)
    budgets = Budgets(daily_contacts=1000, daily_incentive_inr=50000)
    alloc, _ = greedy_allocate(scored, budgets)
    active = alloc[alloc["action"] != "NO_ACTION"]
    assert (active["expected_incremental_value_inr"] > 0).all()


def test_idempotency_full_replay_is_always_a_duplicate():
    df = generate_dataset(200, seed=20, shift=True)
    alloc = pd.DataFrame({"case_id": df["case_id"], "action": "RETRY_NOW"})
    adapter = SimulatedExecutionAdapter(seed=1, timeout_rate=0, provider_error_rate=0)
    audit1, audit2 = AuditLog(), AuditLog()
    policy_decisions = {cid: None for cid in df["case_id"]}
    from crro.policy import PolicyDecision
    decisions = {cid: PolicyDecision(True, False, None) for cid in df["case_id"]}
    r1 = run_execution(alloc, df, adapter, audit1, decisions, "test_v1")
    r2 = run_execution(alloc, df, adapter, audit2, decisions, "test_v1")
    executed_first_time = r1[r1["exec_status"] == "EXECUTED"]["case_id"]
    dup_second_time = r2[r2["case_id"].isin(executed_first_time)]
    assert (dup_second_time["exec_status"] == "SKIPPED_DUPLICATE").all()


def test_llm_cannot_invent_action():
    """The action registry is a closed vocabulary; unknown actions must be rejected upstream."""
    assert "MAKE_UP_AN_OFFER" not in ACTION_REGISTRY
    assert all(a in ACTION_REGISTRY for a in
               ["NO_ACTION", "RETRY_NOW", "RETRY_LATER", "PAYMENT_LINK",
                "ALTERNATIVE_PAYMENT_METHOD", "CUSTOMER_NUDGE", "SMALL_INCENTIVE"])



from crro.db import db_is_reachable

_db_available = db_is_reachable()
requires_db = pytest.mark.skipif(not _db_available, reason="Postgres not reachable")


@requires_db
def test_db_schema_has_expected_tables():
    from crro.db import get_engine
    from sqlalchemy import inspect
    insp = inspect(get_engine())
    tables = set(insp.get_table_names())
    for t in ("merchants", "customers", "cases", "recovery_decisions",
              "customer_action_history", "audit_events", "offers"):
        assert t in tables, f"missing table {t} — did you run `alembic upgrade head`?"


@requires_db
def test_postgres_state_store_persists_opt_out_and_cooldown():
    from crro.state_db import PostgresCustomerStateStore
    from crro.db import get_session
    from crro.db_models import PaymentCase
    store = PostgresCustomerStateStore()
    cust_id = f"test_cust_{np.random.randint(1_000_000)}"
    case_id = f"test_case_{np.random.randint(1_000_000)}"

    assert store.is_opted_out(cust_id) is False
    store.set_opted_out(cust_id, True)
    assert store.is_opted_out(cust_id) is True

    s = get_session()
    s.add(PaymentCase(case_id=case_id, customer_id=cust_id, amount_inr=1000.0,
                       payment_method="upi", failure_reason="bank_decline", attempt_count=1))
    s.commit()
    s.close()

    assert store.in_cooldown(cust_id, "PAYMENT_LINK", min_cooldown_minutes=15) is False
    store.record(cust_id, case_id, "PAYMENT_LINK", status="PROPOSED")
    assert store.in_cooldown(cust_id, "PAYMENT_LINK", min_cooldown_minutes=15) is True
    assert store.attempts_used(case_id) == 1


def test_llm_mock_provider_is_explicit_and_registry_safe(monkeypatch):
    """Provider selection must support deterministic tests without network access."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from crro.llm_analyst import analyze_case
    case = {
        "case_id": "LLM-TEST-1", "amount_inr": 1000, "payment_method": "card",
        "failure_reason": "card_expired", "attempt_count": 1,
        "minutes_since_failure": 5, "customer_id": "cust-1",
    }
    result = analyze_case(case, ["ALTERNATIVE_PAYMENT_METHOD", "NO_ACTION"])
    assert result.source == "mock_no_api_key"
    assert all(a.action in ACTION_REGISTRY for a in result.recommended_actions)
