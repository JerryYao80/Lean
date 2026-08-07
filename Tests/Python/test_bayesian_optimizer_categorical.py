"""Phase 4 Task 3: bayesian_optimizer categorical param (factor-include).

Drives the real optimize() with a manifest declaring type=='categorical' and
asserts the sampled value is a valid factor id — proving objective() routes
categorical params through trial.suggest_categorical (spec §3.2).
"""
import sys
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[2]
_AO = _REPO / "Scripts" / "auto_optimize"
if str(_AO) not in sys.path:
    sys.path.insert(0, str(_AO))


_MANIFEST_YAML = """\
strategy_name: TestCat
lean_config: Launcher/config/config-test.json
parameter_space:
  - {name: factor-include, type: categorical, range: [crowding, hv_20d], default: crowding, layer: L2_Alpha}
  - {name: trend-ma-short, type: int, range: [10, 40], default: 20, layer: L2_Alpha}
state_schema:
  fields: []
reward_config:
  primary: dsr
  shaping: []
universe:
  symbols: ["510050"]
  timezone: Asia/Shanghai
"""


def test_optimize_samples_categorical_factor_include(tmp_path):
    """optimize() must sample factor-include ∈ {crowding, hv_20d} via suggest_categorical."""
    import yaml
    import bayesian_optimizer as bo

    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(_MANIFEST_YAML, encoding="utf-8")

    captured_params = []

    def _fake_run_backtest(manifest, params, config_path, timeout=600):
        captured_params.append(dict(params))
        return {"Sharpe Ratio": 1.0}

    config = {"lean": {"timeout_seconds": 60}}
    with patch.object(bo, "run_backtest", side_effect=_fake_run_backtest), \
         patch.object(bo, "compute_stationary_reward",
                      return_value={"sharpe": 1.0, "objective": 1.0, "no_trades": False}), \
         patch.object(bo, "check_convergence", return_value=True):
        result = bo.optimize(str(manifest_path), config, n_trials=3)

    assert captured_params, "run_backtest was never called"
    valid = {"crowding", "hv_20d"}
    for params in captured_params:
        assert params["factor-include"] in valid, \
            f"factor-include={params['factor-include']} not sampled as categorical"
    # best_params must also carry a valid categorical value.
    assert result["best_params"]["factor-include"] in valid
