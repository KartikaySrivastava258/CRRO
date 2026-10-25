"""Backwards-compatible alias: the evaluation harness lives in crro/evaluate.py.
(Two diverging copies previously priced incentives differently.)"""
from .evaluate import (add_common_random_numbers, realize_policy_outcomes, summarize_policy,  # noqa: F401
                       segment_performance, incremental_vs_control, calibration_report, mean_ci95)
