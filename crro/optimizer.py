"""
Constrained portfolio optimizer.

    maximize   sum_k x_k * v_k                     (v_k: expected incremental net value, paise)
    subject to sum_{k in case i} x_k <= 1          (one action per case; none = NO_ACTION)
               sum_k contact_k * x_k <= C          (daily contact budget)
               sum_k D_k * x_k <= B                (incentive exposure budget, paise)
               sum_{k: action a} x_k <= cap_a      (per-action caps)
               sum_{k in customer u, contact} x_k <= cap_u   (per-customer contact cap)
               x_k in {0, 1};  only candidates with v_k > 0 are considered

Backends (reported in `usage["solver"]`, never silently substituted):
  * "cp_sat"  OR-Tools CP-SAT, if importable (single worker + fixed seed => deterministic)
  * "highs"   SciPy's HiGHS MILP (scipy.optimize.milp), otherwise
  * "greedy"  value-ordered heuristic with deterministic tie-breaking

Status vocabulary: OPTIMAL (solver proved optimality), FEASIBLE_TIME_LIMIT
(incumbent, not proven), INFEASIBLE, ERROR, HEURISTIC (greedy), EMPTY (no
positive candidates). Optimality is only reported when the solver confirms it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict

import numpy as np
import pandas as pd

from .economics import add_economics


@dataclass
class Budgets:
    daily_contacts: int
    daily_incentive_inr: float
    per_action_daily_cap: Dict[str, int] = field(default_factory=dict)
    per_customer_daily_contact_cap: int = 1

    def __post_init__(self):
        if self.daily_contacts < 0 or self.daily_incentive_inr < 0:
            raise ValueError("budgets must be non-negative")
        if self.per_customer_daily_contact_cap < 0 or any(v < 0 for v in self.per_action_daily_cap.values()):
            raise ValueError("caps must be non-negative")

    @property
    def incentive_paise(self) -> int:
        return int(round(self.daily_incentive_inr * 100))


def compute_expected_incremental_value(scored_df: pd.DataFrame, cases_df: pd.DataFrame,
                                       cost_multiplier: float = 1.0) -> pd.DataFrame:
    """Attach economics (paise) and customer ids to a long (case, action) frame.

    cases_df needs case_id, amount_inr (whole/2dp rupees), customer_id."""
    amt = cases_df.set_index("case_id")["amount_inr"].map(lambda x: int(round(float(x) * 100)))
    df = add_economics(scored_df, amt, cost_multiplier)
    if "customer_id" in cases_df.columns:
        df["customer_id"] = df["case_id"].map(cases_df.set_index("case_id")["customer_id"])
    return df


def _candidates(scored_df):
    c = scored_df[scored_df["expected_incremental_value_paise"] > 0].copy()
    # Deterministic order: value desc, then case_id, then action name.
    c = c.sort_values(["expected_incremental_value_paise", "case_id", "action"],
                      ascending=[False, True, True], kind="mergesort").reset_index(drop=True)
    c["value_int"] = np.floor(c["expected_incremental_value_paise"].to_numpy()).astype("int64")
    if "customer_id" not in c.columns:
        c["customer_id"] = c["case_id"]
    return c


def _usage(selected: pd.DataFrame, budgets: Budgets, solver: str, status: str, extra=None) -> dict:
    contacts = int(selected["requires_customer_contact"].sum()) if len(selected) else 0
    inc = int(selected["incentive_discount_paise"].sum()) if len(selected) else 0
    u = {"solver": solver, "status": status, "contacts_used": contacts, "contacts_budget": budgets.daily_contacts,
         "incentive_committed_paise": inc, "incentive_budget_paise": budgets.incentive_paise,
         "actions_selected": int(len(selected)),
         "objective_expected_value_paise": int(selected["value_int"].sum()) if len(selected) else 0}
    u["incentive_used_inr"] = inc / 100            # legacy rupee mirrors
    u["incentive_budget_inr"] = budgets.daily_incentive_inr
    u.update(extra or {})
    return u


def greedy_allocate(scored_df: pd.DataFrame, budgets: Budgets):
    t0 = time.perf_counter()
    cand = _candidates(scored_df)
    contacts, inc = 0, 0
    act_counts, cust_counts, chosen_cases, keep = {}, {}, set(), []
    for i, r in enumerate(cand.itertuples(index=False)):
        if r.case_id in chosen_cases:
            continue
        if r.requires_customer_contact:
            if contacts >= budgets.daily_contacts:
                continue
            if cust_counts.get(r.customer_id, 0) >= budgets.per_customer_daily_contact_cap:
                continue
        if inc + r.incentive_discount_paise > budgets.incentive_paise:
            continue
        cap = budgets.per_action_daily_cap.get(r.action)
        if cap is not None and act_counts.get(r.action, 0) >= cap:
            continue
        keep.append(i)
        chosen_cases.add(r.case_id)
        act_counts[r.action] = act_counts.get(r.action, 0) + 1
        inc += r.incentive_discount_paise
        if r.requires_customer_contact:
            contacts += 1
            cust_counts[r.customer_id] = cust_counts.get(r.customer_id, 0) + 1
    sel = cand.iloc[keep]
    usage = _usage(sel, budgets, "greedy", "HEURISTIC" if len(cand) else "EMPTY",
                   {"wall_time_ms": round((time.perf_counter() - t0) * 1000, 2), "candidates": int(len(cand))})
    return _finalize(scored_df, sel, cand, budgets, usage), usage


def _constraint_rows(cand, budgets):
    """(indices, coefficients, upper_bound, name) for every constraint."""
    rows = []
    for _, idx in cand.groupby("case_id", sort=True).indices.items():
        if len(idx) > 1:
            rows.append((idx, np.ones(len(idx)), 1, "one_per_case"))
    contact = cand["requires_customer_contact"].to_numpy(dtype=bool)
    contact_idx = np.flatnonzero(contact)
    if len(contact_idx):
        rows.append((contact_idx, np.ones(len(contact_idx)), budgets.daily_contacts, "contact_budget"))
    inc = cand["incentive_discount_paise"].to_numpy()
    inc_idx = np.flatnonzero(inc > 0)
    if len(inc_idx):
        rows.append((inc_idx, inc[inc_idx].astype(float), budgets.incentive_paise, "incentive_budget"))
    for a, cap in budgets.per_action_daily_cap.items():
        idx = np.flatnonzero(cand["action"].to_numpy() == a)
        if len(idx):
            rows.append((idx, np.ones(len(idx)), cap, f"action_cap:{a}"))
    sub_pos = contact_idx
    if len(sub_pos):
        cust = cand["customer_id"].to_numpy()[sub_pos]
        order = np.argsort(cust, kind="mergesort")
        cust_sorted, pos_sorted = cust[order], sub_pos[order]
        bounds = np.flatnonzero(np.r_[True, cust_sorted[1:] != cust_sorted[:-1], True])
        for s, e in zip(bounds[:-1], bounds[1:]):
            if e - s > budgets.per_customer_daily_contact_cap:
                idx = pos_sorted[s:e]
                rows.append((idx, np.ones(len(idx)), budgets.per_customer_daily_contact_cap, "customer_contact_cap"))
    return rows


def _solve_highs(cand, budgets, time_limit_s):
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_matrix
    n = len(cand)
    rows = _constraint_rows(cand, budgets)
    r_idx, c_idx, vals, ub = [], [], [], []
    for j, (idx, coef, bound, _) in enumerate(rows):
        r_idx.extend([j] * len(idx))
        c_idx.extend(idx.tolist())
        vals.extend(coef.tolist())
        ub.append(bound)
    constraints = []
    if rows:
        A = coo_matrix((vals, (r_idx, c_idx)), shape=(len(rows), n)).tocsr()
        constraints = [LinearConstraint(A, -np.inf, np.array(ub, dtype=float))]
    res = milp(c=-cand["value_int"].to_numpy(dtype=float), integrality=np.ones(n), bounds=Bounds(0, 1),
               constraints=constraints, options={"time_limit": time_limit_s, "disp": False, "mip_rel_gap": 0.0})
    # scipy: 0 optimal, 1 iteration/time limit, 2 infeasible, 3 unbounded, 4 other
    status = {0: "OPTIMAL", 1: "FEASIBLE_TIME_LIMIT", 2: "INFEASIBLE"}.get(res.status, "ERROR")
    if res.x is None:
        return None, status, {"message": str(res.message)}
    x = np.round(res.x).astype(int)
    extra = {"message": str(res.message)}
    if getattr(res, "mip_dual_bound", None) is not None:
        extra["dual_bound_paise"] = float(-res.mip_dual_bound)
    if getattr(res, "mip_gap", None) is not None:
        extra["mip_gap"] = float(res.mip_gap)
    return np.flatnonzero(x == 1), status, extra


def _solve_cp_sat(cand, budgets, time_limit_s):  # pragma: no cover - OR-Tools absent in this sandbox
    from ortools.sat.python import cp_model
    m = cp_model.CpModel()
    x = [m.NewBoolVar(f"x{i}") for i in range(len(cand))]
    for idx, coef, bound, _ in _constraint_rows(cand, budgets):
        m.Add(sum(int(c) * x[i] for i, c in zip(idx, coef)) <= int(bound))
    m.Maximize(sum(int(v) * x[i] for i, v in enumerate(cand["value_int"].to_numpy())))
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = time_limit_s
    s.parameters.num_workers = 1          # determinism over speed
    s.parameters.random_seed = 0
    st = s.Solve(m)
    status = {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE_TIME_LIMIT",
              cp_model.INFEASIBLE: "INFEASIBLE"}.get(st, "ERROR")
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, status, {}
    chosen = np.array([i for i in range(len(cand)) if s.Value(x[i]) == 1], dtype=int)
    return chosen, status, {"dual_bound_paise": float(s.BestObjectiveBound())}


def exact_allocate(scored_df: pd.DataFrame, budgets: Budgets, time_limit_seconds: float = 30.0,
                   backend: str = "auto"):
    t0 = time.perf_counter()
    cand = _candidates(scored_df)
    if backend == "auto":
        try:
            import ortools  # noqa: F401
            backend = "cp_sat"
        except ImportError:
            backend = "highs"
    if len(cand) == 0:
        usage = _usage(cand, budgets, backend, "EMPTY", {"wall_time_ms": 0.0, "candidates": 0})
        return _finalize(scored_df, cand, cand, budgets, usage), usage
    solve = _solve_cp_sat if backend == "cp_sat" else _solve_highs
    chosen, status, extra = solve(cand, budgets, time_limit_seconds)
    wall = round((time.perf_counter() - t0) * 1000, 2)
    if chosen is None:
        alloc, usage = greedy_allocate(scored_df, budgets)   # labelled fallback, never silent
        usage.update(solver=f"greedy_fallback_after_{backend}", exact_status=status, wall_time_ms=wall)
        return alloc, usage
    sel = cand.iloc[np.sort(chosen)]
    usage = _usage(sel, budgets, backend, status, {"wall_time_ms": wall, "candidates": int(len(cand)), **extra})
    return _finalize(scored_df, sel, cand, budgets, usage), usage


def allocate(scored_df, budgets: Budgets, mode="exact", **kw):
    if mode == "greedy":
        return greedy_allocate(scored_df, budgets)
    return exact_allocate(scored_df, budgets, **kw)


def _finalize(scored_df, sel, cand, budgets, usage) -> pd.DataFrame:
    """One row per case: the selected action, or NO_ACTION with a reason.

      NO_POSITIVE_EXPECTED_VALUE  every scored candidate has expected value <= 0
      BLOCKED_BY:<c1>+<c2>        adding the case's best positive candidate to the final
                                  allocation would violate the listed constraint(s)
      OUTRANKED                   no single constraint blocks it (heuristic/time-limited
                                  solutions only; impossible at a proven optimum)
    """
    sel = sel.copy()
    sel_by_case = sel.set_index("case_id")
    contacts_left = budgets.daily_contacts - usage["contacts_used"]
    inc_left = budgets.incentive_paise - usage["incentive_committed_paise"]
    act_used = sel["action"].value_counts().to_dict() if len(sel) else {}
    cust_used = (sel[sel["requires_customer_contact"].astype(bool)]["customer_id"].value_counts().to_dict()
                 if len(sel) else {})
    best_pos = cand.drop_duplicates("case_id", keep="first").set_index("case_id") if len(cand) else None
    out = []
    for cid in sorted(scored_df["case_id"].unique()):
        if cid in sel_by_case.index:
            r = sel_by_case.loc[cid].to_dict()
            r.update(case_id=cid, rejected_reason=None)
            out.append(r)
            continue
        if best_pos is not None and cid in best_pos.index:
            b = best_pos.loc[cid]
            blocks = []
            if b["requires_customer_contact"] and contacts_left < 1:
                blocks.append("CONTACT_BUDGET")
            if b["incentive_discount_paise"] > inc_left:
                blocks.append("INCENTIVE_BUDGET")
            cap = budgets.per_action_daily_cap.get(b["action"])
            if cap is not None and act_used.get(b["action"], 0) >= cap:
                blocks.append(f"ACTION_CAP:{b['action']}")
            if b["requires_customer_contact"] and cust_used.get(b["customer_id"], 0) >= \
                    budgets.per_customer_daily_contact_cap:
                blocks.append("CUSTOMER_CONTACT_CAP")
            reason = ("BLOCKED_BY:" + "+".join(blocks)) if blocks else "OUTRANKED"
        else:
            reason = "NO_POSITIVE_EXPECTED_VALUE"
        out.append({"case_id": cid, "action": "NO_ACTION", "expected_incremental_value_paise": 0.0,
                    "expected_incremental_value_inr": 0.0, "estimated_uplift": 0.0, "action_cost_paise": 0,
                    "incentive_discount_paise": 0, "requires_customer_contact": False, "rejected_reason": reason})
    return pd.DataFrame(out)


def check_feasibility(alloc: pd.DataFrame, budgets: Budgets, customer_of: pd.Series = None) -> list:
    """Independent post-hoc constraint check. Returns violations (empty list = feasible)."""
    v = []
    act = alloc[alloc["action"] != "NO_ACTION"]
    if alloc["case_id"].duplicated().any():
        v.append("case allocated more than once")
    if int(act["requires_customer_contact"].astype(bool).sum()) > budgets.daily_contacts:
        v.append("contact budget exceeded")
    if int(act["incentive_discount_paise"].sum()) > budgets.incentive_paise:
        v.append("incentive budget exceeded")
    for a, cap in budgets.per_action_daily_cap.items():
        if int((act["action"] == a).sum()) > cap:
            v.append(f"action cap exceeded for {a}")
    if customer_of is not None and len(act):
        c = act[act["requires_customer_contact"].astype(bool)]["case_id"].map(customer_of).value_counts()
        if len(c) and int(c.max()) > budgets.per_customer_daily_contact_cap:
            v.append("per-customer contact cap exceeded")
    return v
