"""Ports of every assertion in the original tests/test_crro.py to the current
APIs (originals preserved in legacy/tests/). DB-specific originals are covered
by tests/integration/test_ledger.py; the LLM-mock test moved with the analyst."""
import unittest

import numpy as np
import pandas as pd

from crro.action_registry import ACTION_REGISTRY
from crro.baselines import control_policy
from crro.data_generator import eligible_actions_for, generate_dataset
from crro.evaluate import add_common_random_numbers, incremental_vs_control, realize_policy_outcomes
from crro.model import PooledUpliftModel, UpliftModel
from crro.optimizer import Budgets, compute_expected_incremental_value, exact_allocate, greedy_allocate
from crro.oracle import true_p_recovery


class TestOriginalSuitePorted(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = generate_dataset(500, seed=1)
        cls.df["eligible_actions"] = cls.df.apply(eligible_actions_for, axis=1)

    def test_no_action_has_nonzero_true_probability(self):
        p = true_p_recovery(self.df, np.full(len(self.df), "NO_ACTION", dtype=object))
        self.assertTrue(p.min() > 0 and p.max() < 1 and 0.05 < p.mean() < 0.6)

    def test_actions_have_nonnegative_uplift_on_average(self):
        p0 = true_p_recovery(self.df, np.full(len(self.df), "NO_ACTION", dtype=object))
        for a in ACTION_REGISTRY:
            if a != "NO_ACTION":
                pa = true_p_recovery(self.df, np.full(len(self.df), a, dtype=object))
                self.assertGreaterEqual(pa.mean(), p0.mean() - 0.05, a)

    def test_control_policy_realizes_nonzero_recovery(self):
        df = add_common_random_numbers(generate_dataset(2000, seed=5, shift=True), seed=99)
        r = realize_policy_outcomes(df, control_policy(df))
        self.assertGreater(r["recovered"].sum(), 0)
        self.assertGreater(r["gross_paise"].sum(), 0)

    def test_incremental_vs_control_is_zero_for_control_itself(self):
        df = add_common_random_numbers(generate_dataset(1000, seed=6, shift=True), seed=100)
        r = realize_policy_outcomes(df, control_policy(df))
        inc = incremental_vs_control(r, r)
        self.assertEqual((inc["incremental_gross_paise"], inc["incremental_net_paise"]), (0, 0))

    def _scored(self, model_cls, seed):
        model = model_cls().fit(generate_dataset(3000, seed=seed))
        return compute_expected_incremental_value(model.score_all_actions(self.df, "eligible_actions"), self.df)

    def test_optimizer_respects_contact_budget(self):
        b = Budgets(daily_contacts=10, daily_incentive_inr=5000, per_customer_daily_contact_cap=1)
        for model_cls in (UpliftModel, PooledUpliftModel):
            scored = self._scored(model_cls, 10)
            for allocator in (greedy_allocate, exact_allocate):
                alloc, _ = allocator(scored, b)
                contact = alloc[(alloc.action != "NO_ACTION") & alloc.action.map(
                    lambda a: ACTION_REGISTRY[a].requires_customer_contact)]
                self.assertLessEqual(len(contact), 10)

    def test_optimizer_never_allocates_negative_value_action(self):
        alloc, _ = greedy_allocate(self._scored(PooledUpliftModel, 11), Budgets(1000, 50000))
        self.assertTrue((alloc[alloc.action != "NO_ACTION"]["expected_incremental_value_paise"] > 0).all())

    def test_llm_cannot_invent_action(self):
        self.assertNotIn("MAKE_UP_AN_OFFER", ACTION_REGISTRY)
        self.assertTrue(all(a in ACTION_REGISTRY for a in ["NO_ACTION", "RETRY_NOW", "RETRY_LATER", "PAYMENT_LINK",
                                                           "ALTERNATIVE_PAYMENT_METHOD", "CUSTOMER_NUDGE",
                                                           "SMALL_INCENTIVE"]))
