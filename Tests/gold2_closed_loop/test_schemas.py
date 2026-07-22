"""Failing contracts for the preregistration + candidate-event JSON schemas
(Task 9, Phase 2 STOP gate).

Loads Draft 2020-12 schemas from
``Scripts/gold2_closed_loop/schema/*.schema.json`` and enforces every numeric
threshold listed in design §13 plus finite-value rejection (NaN/Infinity are
not tolerated even though ``jsonschema`` alone would permit them).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from Scripts.gold2_closed_loop.schemas import (
    validate_candidate_event,
    validate_preregistration,
    validate_verdict,
)


SCHEMA_DIR = Path(__file__).resolve().parents[2] / "Scripts" / "gold2_closed_loop" / "schema"


def _minimal_preregistration() -> dict:
    """A minimal valid preregistration draft covering every §13 threshold."""
    return {
        "experiment_id": "gold2-proof-2026-001",
        "session_timezone": "Asia/Shanghai",
        "session_close_time": "15:00:00",
        "windows": {
            "W1": {
                "train": ["2018-01-02", "2020-12-31"],
                "review": ["2021-01-01", "2021-12-31"],
                "blind": ["2022-01-01", "2022-12-31"],
            },
            "W2": {
                "train": ["2019-01-01", "2021-12-31"],
                "review": ["2022-01-01", "2022-12-31"],
                "blind": ["2023-01-01", "2023-12-31"],
            },
            "W3": {
                "train": ["2020-01-01", "2022-12-31"],
                "review": ["2023-01-01", "2023-12-31"],
                "blind": ["2024-01-01", "2024-12-31"],
            },
            "W4": {
                "train": ["2021-01-01", "2023-12-31"],
                "review": ["2024-01-01", "2024-12-31"],
                "blind": ["2025-01-01", "2025-12-31"],
            },
        },
        "random_seed_set": [17, 23, 31, 47],
        "candidate_budget": 256,
        "retry_policy": {
            "max_infrastructure_retries": 2,
            "retry_only_when_results_unobserved": True,
        },
        "metric_paths": {
            "total_net_profit": "statistics.TotalNetProfit",
            "cagr": "statistics.CompoundingAnnualReturn",
            "sharpe_ratio": "statistics.SharpeRatio",
            "max_drawdown": "statistics.Drawdown",
            "information_ratio": "statistics.Alpha",
        },
        "as_of_utc": "2026-07-14T00:00:00Z",
        "maximum_staleness_days": 1,
        "equivalence_tolerances": {
            "decimal": 0.0,
            "double": 1e-12,
        },
        "success_thresholds": {
            "max_single_trade_contribution_fraction": 0.25,
            "max_single_month_contribution_fraction": 0.40,
            "leave_one_window_out_min_delta": 0.0001,
            "paired_bootstrap_min_probability": 0.95,
            "paired_bootstrap_ci_level": 0.90,
            "max_drawdown_degradation": 0.10,
        },
        "candidate_gates": {
            "min_trades": 4,
            "max_mdd": 0.30,
            "min_dsr": 0.0,
            "subwindow_spread_max": 0.05,
        },
        "selection": {
            "ranking_metric": "sharpe",
            "tie_break": [
                {"metric": "net_profit", "direction": "desc"},
                {"metric": "mdd", "direction": "asc"},
            ],
        },
        "g3": {
            "attribution_gap_threshold": 0.001,
            "non_convergence_threshold": 0.05,
            "float_tolerance": 1e-9,
            "generation_continuity_rule": "BREAK_ON_FAILURE",
            "min_generations_for_trigger": 3,
            "per_generation_candidate_budget": 16,
            "shaping_weight_cap": 3.0,
            "min_generations_per_window": 1,
            "max_generations_per_window": 6,
            "generation_budget_counts_in_candidate_budget": True,
            "allow_post_trigger_generations": False,
        },
    }


def _minimal_candidate_event() -> dict:
    return {
        "event_id": "a" * 32,
        "sequence": 1,
        "candidate_id": "c1",
        "event_type": "REGISTERED",
        "execution_status": "PENDING",
        "partition": "W1/train",
        "parameters": {"p": 1},
        "metrics": None,
        "attempted_at_utc": "2026-07-14T00:00:00Z",
        "previous_event_sha256": "0" * 64,
        "event_sha256": "0" * 64,
    }


# --- anchor tests from the plan (verbatim) ----------------------------


def test_disposition_pairs_are_closed():
    assert validate_verdict("NOT_EVALUATED", "UNPROVEN")
    with pytest.raises(ValueError):
        validate_verdict("NOT_EFFECTIVE", "UNPROVEN")


# --- verdict / disposition pairing ----------------------------------


def test_verdict_disposition_pairing_all_legal():
    assert validate_verdict("EFFECTIVE", None)
    assert validate_verdict("PARTIALLY_EFFECTIVE", None)
    assert validate_verdict("NOT_EFFECTIVE", "REFUTED")
    assert validate_verdict("NOT_EVALUATED", "UNPROVEN")


def test_verdict_disposition_pairing_illegal():
    with pytest.raises(ValueError):
        validate_verdict("EFFECTIVE", "UNPROVEN")
    with pytest.raises(ValueError):
        validate_verdict("PARTIALLY_EFFECTIVE", "REFUTED")
    with pytest.raises(ValueError):
        validate_verdict("NOT_EFFECTIVE", None)
    with pytest.raises(ValueError):
        validate_verdict("NOT_EVALUATED", None)


# --- preregistration schema ----------------------------------------


def test_minimal_preregistration_passes():
    validate_preregistration(_minimal_preregistration())


def test_preregistration_missing_threshold_fails():
    draft = _minimal_preregistration()
    del draft["success_thresholds"]["leave_one_window_out_min_delta"]
    with pytest.raises((ValueError, Exception)):
        validate_preregistration(draft)


def test_preregistration_missing_g3_threshold_fails():
    draft = _minimal_preregistration()
    del draft["g3"]["attribution_gap_threshold"]
    with pytest.raises((ValueError, Exception)):
        validate_preregistration(draft)


def test_preregistration_nan_threshold_fails():
    draft = _minimal_preregistration()
    draft["success_thresholds"]["paired_bootstrap_min_probability"] = float("nan")
    with pytest.raises(ValueError, match="non-finite|NaN|finite"):
        validate_preregistration(draft)


def test_preregistration_infinity_threshold_fails():
    draft = _minimal_preregistration()
    draft["g3"]["shaping_weight_cap"] = float("inf")
    with pytest.raises(ValueError, match="non-finite|Infinity|finite"):
        validate_preregistration(draft)


def test_preregistration_leave_one_window_out_non_number_fails():
    draft = _minimal_preregistration()
    draft["success_thresholds"]["leave_one_window_out_min_delta"] = "small"
    with pytest.raises((ValueError, Exception)):
        validate_preregistration(draft)


def test_preregistration_missing_windows_fails():
    draft = _minimal_preregistration()
    del draft["windows"]["W4"]
    with pytest.raises((ValueError, Exception)):
        validate_preregistration(draft)


def test_preregistration_empty_seed_set_fails():
    draft = _minimal_preregistration()
    draft["random_seed_set"] = []
    with pytest.raises((ValueError, Exception)):
        validate_preregistration(draft)


def test_preregistration_candidate_budget_zero_fails():
    draft = _minimal_preregistration()
    draft["candidate_budget"] = 0
    with pytest.raises((ValueError, Exception)):
        validate_preregistration(draft)


def test_preregistration_generation_continuity_rule_enum_fails():
    draft = _minimal_preregistration()
    draft["g3"]["generation_continuity_rule"] = "MAYBE_BREAK"
    with pytest.raises((ValueError, Exception)):
        validate_preregistration(draft)


def test_preregistration_unknown_top_level_rejected():
    draft = _minimal_preregistration()
    draft["rogue_field"] = "forbidden"
    with pytest.raises((ValueError, Exception)):
        validate_preregistration(draft)


# --- candidate event schema -----------------------------------------


def test_minimal_candidate_event_passes():
    validate_candidate_event(_minimal_candidate_event())


def test_candidate_event_missing_event_id_fails():
    ev = _minimal_candidate_event()
    del ev["event_id"]
    with pytest.raises((ValueError, Exception)):
        validate_candidate_event(ev)


def test_candidate_event_bad_event_type_fails():
    ev = _minimal_candidate_event()
    ev["event_type"] = "WAT"
    with pytest.raises((ValueError, Exception)):
        validate_candidate_event(ev)


def test_candidate_event_bad_execution_status_fails():
    ev = _minimal_candidate_event()
    ev["execution_status"] = "MAYBE"
    with pytest.raises((ValueError, Exception)):
        validate_candidate_event(ev)


def test_candidate_event_nan_metric_fails():
    ev = _minimal_candidate_event()
    ev["metrics"] = {"sharpe": float("nan")}
    with pytest.raises(ValueError, match="non-finite|NaN|finite"):
        validate_candidate_event(ev)


# --- schema files exist on disk ------------------------------------


def test_schema_files_present_on_disk():
    for name in ("preregistration.schema.json", "candidate-event.schema.json"):
        path = SCHEMA_DIR / name
        assert path.is_file(), f"missing schema file {path}"
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded.get("$id", "").endswith(name.replace(".schema.json", ".schema.json"))
        assert loaded.get("$schema", "").startswith("https://json-schema.org/draft/2020-12")
