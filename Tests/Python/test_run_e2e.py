import json
from unittest.mock import patch

import numpy as np

from run_e2e import e2e


def test_e2e_smoke():
    """E2E: manifest → Layer A → overfitting → baseline end-to-end (mocked)."""
    config = {
        "optuna": {"n_trials": 3},
        "lean": {"timeout_seconds": 60},
        "overfitting": {"oos_dsr_absolute_min": 0.5, "oos_is_ratio_min": 0.6},
    }
    fake_a_result = {
        "best_params": {"iv-rv-z-score-threshold": 2.0},
        "best_value": 1.2,
        "n_trials": 3,
        "ridge_converged": True,
    }
    with patch("run_e2e.optimize", return_value=fake_a_result) as m_opt, \
         patch("run_e2e.load_manifest") as m_lm:
        m_lm.return_value.strategy_name = "TestStrategy"
        result = e2e("Scripts/auto_optimize/strategies/option_vol_arb_5layer/manifest.yaml", config)
    assert m_opt.called
    assert result["layer_a"]["best_value"] == 1.2
    assert "overfitting" in result
    assert "baseline" in result
    # OOS mean = 1.2 * 0.66 = 0.792 ≥ 0.5, ratio = 0.66 ≥ 0.6 → PASS
    assert result["overfitting"]["overfitting_flag"] == "PASS"
    assert result["baseline"]["verdict"] in ("PASS", "WARNING")


def test_e2e_overfitting_fail():
    """Low best_value triggers overfitting FAIL."""
    config = {"optuna": {"n_trials": 3}, "lean": {"timeout_seconds": 60}}
    fake_a_result = {
        "best_params": {},
        "best_value": 0.1,
        "n_trials": 3,
        "ridge_converged": False,
    }
    with patch("run_e2e.optimize", return_value=fake_a_result), \
         patch("run_e2e.load_manifest") as m_lm:
        m_lm.return_value.strategy_name = "TestStrategy"
        result = e2e("any/manifest.yaml", config)
    # OOS mean = 0.1 * 0.66 = 0.066 < 0.5 → FAIL
    assert result["overfitting"]["overfitting_flag"] == "FAIL"
    assert result["overfitting"]["ridge_converged"] is False
