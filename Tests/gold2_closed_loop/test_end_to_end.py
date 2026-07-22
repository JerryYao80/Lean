"""Failing contracts for the end-to-end CLI (Task 17, Phase 4 STOP gate).

Design invariants (spec §5 lifecycle, §15 final CLI):

* The final CLI exposes four commands:
    assess-feasibility --input DRAFT --output AUDIT
    preregister --input DRAFT --experiment-id ID
    execute --experiment-root ROOT [--resume]
    verify-seal --experiment-root ROOT
* Each command enforces the lifecycle + phase gates: a stopped/negative
  run retains all evidence (spec §15 line 877).
* ``verify-seal`` must report the same published root hash + receipt as
  ``execute`` produced.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUN_EXPERIMENT = "Scripts.gold2_closed_loop.run_experiment"


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", RUN_EXPERIMENT, *args],
        cwd=str(cwd) if cwd else str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )


_DRAFT = """\
experiment_id: E1
session_timezone: Asia/Shanghai
session_close_time: "15:00:00"
windows:
  W1: {train: ["2018-01-02", "2020-12-31"], review: ["2021-01-01", "2021-12-31"], blind: ["2022-01-01", "2022-12-31"]}
  W2: {train: ["2019-01-01", "2021-12-31"], review: ["2022-01-01", "2022-12-31"], blind: ["2023-01-01", "2023-12-31"]}
  W3: {train: ["2020-01-01", "2022-12-31"], review: ["2023-01-01", "2023-12-31"], blind: ["2024-01-01", "2024-12-31"]}
  W4: {train: ["2021-01-01", "2023-12-31"], review: ["2024-01-01", "2024-12-31"], blind: ["2025-01-01", "2025-12-31"]}
random_seed_set: [17]
candidate_budget: 4
retry_policy: {max_infrastructure_retries: 0, retry_only_when_results_unobserved: true}
metric_paths: {sharpe: "totalPerformance.portfolioStatistics.sharpeRatio"}
as_of_utc: "16:00:00"
maximum_staleness_days: 5
equivalence_tolerances: {decimal: 0.0, double: 1.0e-9}
success_thresholds:
  max_single_trade_contribution_fraction: 0.5
  max_single_month_contribution_fraction: 0.5
  leave_one_window_out_min_delta: 0.0
  paired_bootstrap_min_probability: 0.95
  paired_bootstrap_ci_level: 0.95
  max_drawdown_degradation: 0.1
data_sources:
  fund_518880: {path: "data/518880.csv"}
g3:
  attribution_gap_threshold: 0.15
  non_convergence_threshold: 0.1
  float_tolerance: 1.0e-9
  generation_continuity_rule: BREAK_ON_HASH_CHANGE
  min_generations_for_trigger: 3
  per_generation_candidate_budget: 4
  shaping_weight_cap: 3.0
  min_generations_per_window: 3
  max_generations_per_window: 5
  generation_budget_counts_in_candidate_budget: true
  allow_post_trigger_generations: false
"""


def test_assess_feasibility_command_exists(tmp_path):
    draft = tmp_path / "draft.yaml"
    draft.write_text(_DRAFT)
    out = tmp_path / "audit"
    res = _run_cli("assess-feasibility", "--input", str(draft), "--output", str(out))
    assert res.returncode in (0, 2), res.stderr


def test_preregister_command_exists(tmp_path):
    draft = tmp_path / "draft.yaml"
    draft.write_text(_DRAFT)
    draft_with_real_paths = tmp_path / "draft2.yaml"
    draft_with_real_paths.write_text(_DRAFT.replace(
        "data/518880.csv", str(draft)
    ))
    res = _run_cli(
        "preregister", "--input", str(draft_with_real_paths),
        "--experiment-id", "E1",
        "--experiment-root", str(tmp_path / "exp-root"),
    )
    assert "error: the following arguments are required" not in res.stderr, res.stderr
    assert res.returncode in (0, 2), res.stderr


def test_verify_seal_command_exists(tmp_path):
    res = _run_cli("verify-seal", "--experiment-root", str(tmp_path / "nope"))
    assert "usage:" not in res.stderr.lower(), res.stderr
    assert res.returncode != 0


def test_execute_command_exists(tmp_path):
    res = _run_cli("execute", "--experiment-root", str(tmp_path / "nope"))
    assert "usage:" not in res.stderr.lower(), res.stderr
    assert res.returncode != 0
