"""Unit tests: money, economics, leakage boundaries, optimizer correctness
(including brute-force optimality on tiny instances), metrics edge cases,
baselines, and the live decision engine's guardrails."""
import itertools
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd

from crro.action_registry import ACTION_REGISTRY
from crro.data_generator import eligible_actions_for, generate_dataset
from crro.economics import (action_cost_paise, add_economics, auto_offer_for, expected_incremental_net_paise,
                            incentive_discount_paise, realized_economics)
from crro.evaluate import (add_common_random_numbers, calibration_report, incremental_vs_control, mean_ci95,
                           qini_rct, realize_policy_outcomes, summarize_policy)
from crro.model import FEATURES, PooledUpliftModel, UpliftModel
from crro.money import MoneyError, format_inr, paise_to_rupees, percent_of_paise, rupees_to_paise
from crro.optimizer import Budgets, check_feasibility, exact_allocate, greedy_allocate


class TestMoney(unittest.TestCase):
    def test_conversions_and_rounding(self):
        self.assertEqual(rupees_to_paise("0.01"), 1)
        self.assertEqual(rupees_to_paise(0.1 + 0.2), 30)            # float artefact does not leak
        self.assertEqual(rupees_to_paise("100"), 10000)
        self.assertEqual(rupees_to_paise("2.345"), 235)             # half-up
        self.assertEqual(rupees_to_paise("99999999.99"), 9_999_999_999)
        self.assertEqual(paise_to_rupees(1), Decimal("0.01"))
        self.assertEqual(percent_of_paise(1_000_001, 0.02), 20_000)
        self.assertEqual(format_inr(123_456_789), "₹12,34,567.89")
        self.assertEqual(format_inr(-5), "-₹0.05")

    def test_rejects_invalid(self):
        for bad in ("0", -1, "abc", float("nan"), float("inf"), None, True, "100000000.01"):
            with self.assertRaises(MoneyError, msg=repr(bad)):
                rupees_to_paise(bad)
        self.assertEqual(rupees_to_paise("0", allow_zero=True), 0)


class TestEconomics(unittest.TestCase):
    def test_expected_value_formula(self):
        # (p_a - p_0) * A - p_a * D - c
        self.assertAlmostEqual(expected_incremental_net_paise(0.5, 0.3, 1_000_000, "SMALL_INCENTIVE"),
                               0.2 * 1_000_000 - 0.5 * 20_000 - 1_000)
        self.assertEqual(expected_incremental_net_paise(0.9, 0.1, 10_000, "NO_ACTION"), 0.0)

    def test_offer_never_requires_approval_and_respects_cap(self):
        self.assertEqual(auto_offer_for("SMALL_INCENTIVE", 1_000_000), ("OFFER_2PCT", 20_000))
        self.assertEqual(auto_offer_for("SMALL_INCENTIVE", 1_999_900)[1], 30_000)   # ₹300 cap
        self.assertEqual(auto_offer_for("PAYMENT_LINK", 1_000_000), (None, 0))

    def test_incentive_only_paid_on_recovery(self):
        self.assertEqual(realized_economics("SMALL_INCENTIVE", 1_000_000, False)["incentive_cost_paise"], 0)
        r = realized_economics("SMALL_INCENTIVE", 1_000_000, True)
        self.assertEqual((r["gross_recovered_paise"], r["incentive_cost_paise"], r["net_value_paise"]),
                         (1_000_000, 20_000, 1_000_000 - 20_000 - 1_000))

    def test_optimizer_and_evaluator_price_incentives_identically(self):
        """Regression for audit R5 (optimizer 5% vs evaluator 2%)."""
        scored = pd.DataFrame([{"case_id": "C1", "action": "SMALL_INCENTIVE", "recovery_probability": 0.5,
                                "baseline_probability": 0.3}])
        econ = add_economics(scored, pd.Series({"C1": 1_000_000}))
        test = pd.DataFrame([{"case_id": "C1", "amount_inr": 10000.0, "failure_reason": "bank_decline",
                              "payment_method": "upi", "merchant_category": "x", "attempt_count": 1,
                              "minutes_since_failure": 10, "_latent_trait": 0.0, "_u": 0.0}])
        real = realize_policy_outcomes(test, pd.DataFrame([{"case_id": "C1", "action": "SMALL_INCENTIVE"}]))
        self.assertEqual(int(econ["incentive_discount_paise"].iloc[0]), int(real["incentive_cost_paise"].iloc[0]))


class TestDataAndLeakage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.train = generate_dataset(4000, seed=11)
        cls.test = generate_dataset(600, seed=12, shift=True)
        cls.test["eligible_actions"] = cls.test.apply(eligible_actions_for, axis=1)
        cls.model = PooledUpliftModel().fit(cls.train)

    def test_generator_is_reproducible_and_ids_unique_across_datasets(self):
        a, b = generate_dataset(300, seed=5), generate_dataset(300, seed=5)
        pd.testing.assert_frame_equal(a, b)
        self.assertFalse(set(generate_dataset(50, 1).case_id) & set(generate_dataset(50, 2).case_id))
        self.assertFalse(set(generate_dataset(50, 1).case_id) & set(generate_dataset(50, 1, shift=True).case_id))

    def test_features_exclude_hidden_and_outcome_columns(self):
        for f in FEATURES:
            self.assertFalse(f.startswith("_"), f)
            self.assertNotIn(f, ("assigned_action", "outcome_recovered", "case_id", "customer_id"))

    def test_predictions_invariant_to_test_labels_and_hidden_columns(self):
        """If test outcomes / assignments / oracle columns leaked into scoring,
        permuting them would change predictions."""
        base = self.model.score_all_actions(self.test)
        mutated = self.test.copy()
        rng = np.random.default_rng(0)
        for col in ("outcome_recovered", "assigned_action", "_true_p_recovery", "_latent_trait"):
            mutated[col] = rng.permutation(mutated[col].to_numpy())
        pd.testing.assert_frame_equal(base, self.model.score_all_actions(mutated))

    def test_uplift_is_action_minus_control(self):
        s = self.model.score_all_actions(self.test)
        self.assertTrue(np.allclose(s["estimated_uplift"], s["recovery_probability"] - s["baseline_probability"]))
        p0 = self.model.predict_proba(self.test, "NO_ACTION")
        first = s.drop_duplicates("case_id").set_index("case_id")["baseline_probability"]
        self.assertTrue(np.allclose(first.loc[self.test.case_id].to_numpy(), p0))

    def test_batched_live_scoring_equals_per_action_scoring(self):
        row = self.test.head(1)
        batched = self.model.predict_actions(row, self.model.actions)
        for a in self.model.actions:
            self.assertAlmostEqual(batched[a], float(self.model.predict_proba(row, a)[0]), places=12)
        tl = UpliftModel().fit(self.train)
        self.assertEqual(set(tl.predict_actions(row, tl.actions)), set(tl.actions))

    def test_unknown_category_does_not_crash(self):
        x = self.test.head(3).copy()
        x["payment_method"] = "crypto_never_seen"
        for m in (self.model,):
            p = m.predict_proba(x, "PAYMENT_LINK")
            self.assertTrue(np.all((p >= 0) & (p <= 1)))

    def test_control_is_real_in_simulator(self):
        from crro.oracle import true_p_recovery
        p = true_p_recovery(self.test, np.full(len(self.test), "NO_ACTION", dtype=object), shift=True)
        self.assertTrue(0.02 < p.mean() < 0.6 and p.min() > 0)
        realized = realize_policy_outcomes(add_common_random_numbers(self.test, 1),
                                           pd.DataFrame({"case_id": self.test.case_id, "action": "NO_ACTION"}))
        self.assertGreater(realized["recovered"].sum(), 0)

    def test_fit_requires_control_rows(self):
        with self.assertRaises(ValueError):
            PooledUpliftModel().fit(self.train[self.train.assigned_action != "NO_ACTION"])
        with self.assertRaises(ValueError):
            UpliftModel().fit(self.train[self.train.assigned_action != "NO_ACTION"])


def _scored(rows):
    df = pd.DataFrame(rows)
    if "customer_id" not in df:
        df["customer_id"] = df["case_id"]
    for c, v in (("incentive_discount_paise", 0), ("requires_customer_contact", True), ("action_cost_paise", 0),
                 ("amount_paise", 100_000), ("recovery_probability", 0.5), ("baseline_probability", 0.2)):
        if c not in df:
            df[c] = v
    df["estimated_uplift"] = df["recovery_probability"] - df["baseline_probability"]
    return df


def _brute_force(df, b: Budgets):
    pos = df[df.expected_incremental_value_paise > 0].reset_index(drop=True)
    best = 0
    for mask in itertools.product([0, 1], repeat=len(pos)):
        sel = pos[np.array(mask, dtype=bool)]
        if sel.case_id.duplicated().any() or sel.requires_customer_contact.sum() > b.daily_contacts \
                or sel.incentive_discount_paise.sum() > b.incentive_paise:
            continue
        if any((sel.action == a).sum() > c for a, c in b.per_action_daily_cap.items()):
            continue
        cc = sel[sel.requires_customer_contact].customer_id.value_counts()
        if len(cc) and cc.max() > b.per_customer_daily_contact_cap:
            continue
        best = max(best, int(np.floor(sel.expected_incremental_value_paise).sum()))
    return best


class TestOptimizer(unittest.TestCase):
    def random_instance(self, rng, n_cases=4):
        rows = []
        for i in range(n_cases):
            for a in rng.choice(["PAYMENT_LINK", "SMALL_INCENTIVE", "RETRY_NOW"], size=rng.integers(1, 3),
                                replace=False):
                rows.append({"case_id": f"c{i}", "customer_id": f"u{rng.integers(0, 3)}", "action": a,
                             "expected_incremental_value_paise": float(rng.integers(-500, 5000)),
                             "requires_customer_contact": a != "RETRY_NOW",
                             "incentive_discount_paise": int(rng.integers(100, 900)) if a == "SMALL_INCENTIVE" else 0})
        return _scored(rows)

    def test_exact_matches_brute_force_and_beats_or_ties_greedy(self):
        rng = np.random.default_rng(42)
        for trial in range(40):
            df = self.random_instance(rng)
            b = Budgets(int(rng.integers(0, 3)), float(rng.integers(0, 15)),
                        {"PAYMENT_LINK": int(rng.integers(0, 3))}, 1)
            ea, eu = exact_allocate(df, b)
            ga, gu = greedy_allocate(df, b)
            self.assertIn(eu["status"], ("OPTIMAL", "EMPTY"))
            self.assertEqual(eu["objective_expected_value_paise"], _brute_force(df, b), f"trial {trial}")
            self.assertGreaterEqual(eu["objective_expected_value_paise"], gu["objective_expected_value_paise"])
            cust = df.drop_duplicates("case_id").set_index("case_id")["customer_id"]
            self.assertEqual(check_feasibility(ea, b, cust), [])
            self.assertEqual(check_feasibility(ga, b, cust), [])

    def test_greedy_is_not_always_optimal(self):
        """Knapsack counter-example: one big incentive vs two smaller ones."""
        df = _scored([{"case_id": "a", "action": "SMALL_INCENTIVE", "expected_incremental_value_paise": 100.0,
                       "incentive_discount_paise": 10, "requires_customer_contact": False},
                      {"case_id": "b", "action": "SMALL_INCENTIVE", "expected_incremental_value_paise": 60.0,
                       "incentive_discount_paise": 5, "requires_customer_contact": False},
                      {"case_id": "c", "action": "SMALL_INCENTIVE", "expected_incremental_value_paise": 60.0,
                       "incentive_discount_paise": 5, "requires_customer_contact": False}])
        b = Budgets(10, 0.10)
        self.assertEqual(greedy_allocate(df, b)[1]["objective_expected_value_paise"], 100)
        self.assertEqual(exact_allocate(df, b)[1]["objective_expected_value_paise"], 120)

    def test_zero_budget_empty_and_nonpositive(self):
        df = _scored([{"case_id": "a", "action": "PAYMENT_LINK", "expected_incremental_value_paise": 50.0}])
        alloc, u = exact_allocate(df, Budgets(0, 0))
        self.assertEqual(alloc.action.tolist(), ["NO_ACTION"])
        self.assertEqual(alloc.rejected_reason.tolist(), ["BLOCKED_BY:CONTACT_BUDGET"])
        neg = _scored([{"case_id": "a", "action": "PAYMENT_LINK", "expected_incremental_value_paise": -5.0}])
        alloc, u = exact_allocate(neg, Budgets(10, 10))
        self.assertEqual((u["status"], alloc.rejected_reason.iloc[0]), ("EMPTY", "NO_POSITIVE_EXPECTED_VALUE"))
        with self.assertRaises(ValueError):
            Budgets(-1, 0)

    def test_ties_are_deterministic(self):
        df = _scored([{"case_id": c, "action": "PAYMENT_LINK", "expected_incremental_value_paise": 10.0}
                      for c in ("b", "a", "c")])
        runs = {tuple(greedy_allocate(df.sample(frac=1, random_state=s), Budgets(1, 0))[0].action) for s in range(5)}
        self.assertEqual(len(runs), 1)
        runs = {tuple(exact_allocate(df.sample(frac=1, random_state=s), Budgets(1, 0))[0].action) for s in range(5)}
        self.assertEqual(len(runs), 1)


class TestMetrics(unittest.TestCase):
    def test_calibration_edge_cases(self):
        self.assertIsNone(calibration_report([], [])["brier_score"])
        one_class = calibration_report([0, 0, 0], [0.1, 0.2, 0.0])
        self.assertAlmostEqual(one_class["brier_score"], (0.01 + 0.04) / 3, places=5)
        with self.assertRaises(ValueError):
            calibration_report([1], [1.5])
        perfect = calibration_report([0, 1], [0.0, 1.0])
        self.assertEqual((perfect["brier_score"], perfect["ece"]), (0.0, 0.0))

    def test_qini_edge_cases_and_direction(self):
        self.assertIsNone(qini_rct([1, 1], [1, 0], [0.5, 0.4])["qini_coefficient"])   # no control rows
        self.assertIsNone(qini_rct([], [], [])["qini_coefficient"])
        t = np.array([1, 0] * 100)
        y = np.r_[np.tile([1, 0], 50), np.zeros(100)]            # uplift only in first half
        good = qini_rct(t, y, np.r_[np.ones(100), np.zeros(100)])["qini_coefficient"]
        bad = qini_rct(t, y, np.r_[np.zeros(100), np.ones(100)])["qini_coefficient"]
        self.assertGreater(good, 0)
        self.assertLess(bad, 0)
        tied = qini_rct(t, y, np.zeros(200))                       # all ties: stable original order
        self.assertIsNotNone(tied["qini_coefficient"])

    def test_mean_ci95_uses_t_distribution(self):
        self.assertIsNone(mean_ci95([5.0])["ci95_low"])
        r = mean_ci95([1.0, 2.0, 3.0])
        self.assertAlmostEqual(r["ci95_high"] - r["mean"], 4.302653 * 1.0 / np.sqrt(3), places=4)

    def test_incremental_requires_same_cases(self):
        t = add_common_random_numbers(generate_dataset(20, 3, shift=True), 1)
        a = realize_policy_outcomes(t, pd.DataFrame({"case_id": t.case_id, "action": "NO_ACTION"}))
        self.assertEqual(incremental_vs_control(a, a)["incremental_net_paise"], 0)
        with self.assertRaises(ValueError):
            incremental_vs_control(a.head(10), a)

    def test_crn_shared_draws_make_control_vs_itself_identical(self):
        t = add_common_random_numbers(generate_dataset(500, 4, shift=True), 9)
        ctrl = pd.DataFrame({"case_id": t.case_id, "action": "NO_ACTION"})
        r1, r2 = realize_policy_outcomes(t, ctrl), realize_policy_outcomes(t, ctrl)
        self.assertEqual(summarize_policy(r1, "x")["net_value_paise"], summarize_policy(r2, "x")["net_value_paise"])


class TestBaselinesAndProtocol(unittest.TestCase):
    def test_all_constrained_policies_are_feasible(self):
        from crro.experiments import default_budgets, prepare, run_policies
        ctx = prepare(3, 3000, 400)
        r = run_policies(ctx, default_budgets(400))
        for p in r["policies"]:
            if p["constrained"]:
                self.assertEqual(p["feasibility_violations"], [], p["policy"])
        names = [p["policy"] for p in r["policies"]]
        self.assertIn("ORACLE_CONSTRAINED (simulator-only)", names)
        oracle = next(p for p in r["policies"] if p["policy"].startswith("ORACLE_CONSTRAINED"))
        crro = next(p for p in r["policies"] if p["policy"] == "CRRO_EXACT")
        # The constrained oracle maximises TRUE expected value under the same budgets.
        self.assertGreaterEqual(oracle["incremental_expected_net_paise"] + 1, crro["incremental_expected_net_paise"])

    def test_previously_broken_modules_now_run(self):
        from crro.response_curves import action_response_curve
        from crro.experiments import prepare
        ctx = prepare(4, 2500, 300)
        self.assertGreater(len(action_response_curve(ctx=ctx)), 0)


class TestDecisionEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from crro.decision.model_store import load, train_and_save
        d = tempfile.mkdtemp()
        train_and_save(n_train=6000, n_holdout=1000, out_dir=d)
        cls.model, cls.card = load(d)
        from crro.ledger.service import DEFAULT_POLICY
        cls.policy = dict(DEFAULT_POLICY)
        cls.now = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)

    def case(self, **kw):
        c = {"amount_paise": 500_000, "attempt_count": 1, "failed_at": "2026-10-01T08:30:00+00:00",
             "payment_method": "card", "failure_reason": "card_expired", "merchant_category": None, "features": {}}
        c.update(kw)
        return c

    def guard(self, **kw):
        g = {"opted_out": False, "last_action_at": {}, "customer_contacts_24h": 0, "contacts_used_today": 0,
             "incentive_committed_today_paise": 0}
        g.update(kw)
        return g

    def decide(self, case=None, guard=None, policy=None):
        from crro.decision.engine import decide
        return decide(case or self.case(), self.model, self.card, policy or self.policy, guard or self.guard(),
                      self.now)

    def test_opt_out_blocks_every_contact_action(self):
        d = self.decide(guard=self.guard(opted_out=True))
        for c in d["candidates"]:
            if ACTION_REGISTRY[c["action"]].requires_customer_contact:
                self.assertIn("CUSTOMER_OPTED_OUT", c["rejection_reasons"])
        self.assertFalse(ACTION_REGISTRY[d["selected_action"]].requires_customer_contact
                         if d["selected_action"] != "NO_ACTION" else False)

    def test_cooldown_budget_and_policy_reasons(self):
        recent = (self.now - timedelta(minutes=5)).isoformat()
        d = self.decide(guard=self.guard(last_action_at={"PAYMENT_LINK": recent}, contacts_used_today=10**6))
        pl = next(c for c in d["candidates"] if c["action"] == "PAYMENT_LINK")
        self.assertIn("COOLDOWN_ACTIVE", pl["rejection_reasons"])
        self.assertIn("CONTACT_BUDGET_EXHAUSTED", pl["rejection_reasons"])
        pol = {**self.policy, "allowed_actions": []}
        d = self.decide(policy=pol)
        self.assertEqual((d["selected_action"], d["reason_code"]),
                         ("NO_ACTION", "ALL_CANDIDATES_BLOCKED_BY_GUARDRAILS"))

    def test_approval_threshold_and_eligibility(self):
        d = self.decide(case=self.case(amount_paise=9_000_000))
        if d["selected_action"] != "NO_ACTION":
            self.assertTrue(d["requires_approval"])
        inc = next(c for c in d["candidates"] if c["action"] == "SMALL_INCENTIVE")
        self.assertIn("NOT_ELIGIBLE", inc["rejection_reasons"])          # >= ₹20,000
        retries = self.decide(case=self.case(attempt_count=3))
        for c in retries["candidates"]:
            if c["action"].startswith("RETRY"):
                self.assertIn("NOT_ELIGIBLE", c["rejection_reasons"])

    def test_imputations_are_recorded(self):
        d = self.decide(case=self.case(payment_method="crypto"))
        feats = {i["feature"] for i in d["feature_imputations"]}
        self.assertTrue({"customer_success_rate", "merchant_category", "payment_method"} <= feats)

    def test_selected_action_has_max_value_among_feasible(self):
        d = self.decide()
        feas = [c for c in d["candidates"] if c["feasible"]]
        if feas:
            self.assertEqual(d["selected_action"], max(feas, key=lambda c: c["expected_value_paise"])["action"])
            self.assertTrue(all(c["expected_value_paise"] > 0 for c in feas))


if __name__ == "__main__":
    unittest.main()
