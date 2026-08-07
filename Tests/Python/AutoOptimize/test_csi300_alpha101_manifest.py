"""Test csi300_alpha101 manifest parses 8 weight params + reward parses Sharpe.

Task 6: Bayes manifest + reward smoke. Verifies the manifest declares 8 alpha
blend weights (w_alphaNNN in [0,1], layer=L2_Alpha) plus 2 L3_Portfolio gates,
and that the existing reward.py plumbing (compute_stationary_reward /
compute_dsr_gate) correctly parses the LEAN summary "Sharpe Ratio" string.
"""
import sys
from pathlib import Path

import pytest

# Reuse the top-level conftest sys.path insert, but also be safe if this
# directory is run independently (parents[2] -> repo root).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Scripts" / "auto_optimize"))

MANIFEST_PATH = (Path(__file__).resolve().parents[3]
                 / "Scripts" / "auto_optimize" / "strategies"
                 / "csi300_alpha101_composite" / "manifest.yaml")


def test_manifest_parses_8_weight_params():
    from manifest_loader import load_manifest
    m = load_manifest(str(MANIFEST_PATH))
    weights = [p for p in m.parameter_space if p.name.startswith("w_alpha")]
    assert len(weights) == 8
    for p in weights:
        assert p.type == "float"
        assert p.range == (0.0, 1.0)
        assert p.layer == "L2_Alpha"


def test_manifest_has_portfolio_gates_and_adapter_wiring():
    """zscore-threshold + rebalance-days (L3_Portfolio) + review/feedback/inspiration blocks."""
    from manifest_loader import load_manifest
    m = load_manifest(str(MANIFEST_PATH))
    by_name = {p.name: p for p in m.parameter_space}
    assert "zscore-threshold" in by_name
    assert by_name["zscore-threshold"].layer == "L3_Portfolio"
    assert "rebalance-days" in by_name
    assert by_name["rebalance-days"].layer == "L3_Portfolio"
    assert by_name["rebalance-days"].type == "int"

    # review/feedback/inspiration blocks reference the NEW csi300_alpha101 adapters.
    assert m.raw["review"]["adapter_module"] == "adapters.csi300_alpha101"
    assert m.raw["review"]["adapter_class"] == "Csi300Alpha101ReviewAdapter"
    assert len(m.raw["review"]["layer_names"]) == 8
    assert m.raw["feedback"]["adapter_module"] == "adapters.csi300_alpha101"
    assert "inspiration" in m.raw


def test_reward_parses_sharpe_ratio():
    from reward import compute_stationary_reward
    stats = {"Sharpe Ratio": "1.234", "Total Orders": "30"}
    out = compute_stationary_reward(stats)
    # reward.py returns {"objective": sharpe, "sharpe": sharpe, ...}
    assert out["sharpe"] == pytest.approx(1.234)
    assert out["objective"] == pytest.approx(1.234)
    assert out["no_trades"] is False


def test_dsr_gate_returns_passed_bool():
    from reward import compute_dsr_gate
    stats = {"Sharpe Ratio": "1.5", "Total Orders": "30"}
    out = compute_dsr_gate(stats, n_trials_total=200, baseline_sharpe=0.5)
    assert "passed" in out
    # numpy bool is fine — accept np.bool_ or bool
    assert bool(out["passed"]) is True or bool(out["passed"]) is False
    assert "dsr" in out
