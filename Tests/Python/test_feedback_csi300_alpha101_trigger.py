"""Test csi300_alpha101 feedback adapter triggers hypothesize for low-contribution alpha."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Scripts" / "feedback"))


def test_low_contribution_alpha_triggers_hypothesize(tmp_path):
    from adapters.csi300_alpha101 import check_triggers

    review = {"layer_attribution": {
        "alpha_006": {"pnl_pct_of_total": -0.15, "n_trades": 30}}}
    gen_history = [
        {"generation": 1, "layer_gaps": {"alpha_006": {"gap": 0.25}},
         "shaping_overrides": {"alpha_006_contrib_penalty": 2.5}},
        {"generation": 2, "layer_gaps": {"alpha_006": {"gap": 0.22}},
         "shaping_overrides": {"alpha_006_contrib_penalty": 2.8}},
        {"generation": 3, "layer_gaps": {"alpha_006": {"gap": 0.20}},
         "shaping_overrides": {"alpha_006_contrib_penalty": 3.0}},
    ]
    manifest_raw = {"strategy_name": "AShareCSI300Alpha101CompositeStrategy",
                    "inspiration": {"persistence": {"gap_threshold": 0.20, "min_generations": 3},
                                    "llm": {"config_key": "glm", "model": "glm-5.1", "temperature": 0.7}}}
    with patch("adapters.csi300_alpha101.hypothesize_run") as mock_hyp:
        mock_hyp.return_value = str(tmp_path / "hypothesis.md")
        action = check_triggers(review, gen_history, manifest_raw,
                                hypothesis_dir=str(tmp_path))
    assert action is not None
    assert action.trigger == "review_drift"
    assert action.inspired_layer == "alpha_006"
    mock_hyp.assert_called_once()


def test_optimizing_layer_skipped(tmp_path):
    """A layer with gap < threshold does NOT trigger."""
    from adapters.csi300_alpha101 import check_triggers
    review = {"layer_attribution": {
        "alpha_006": {"pnl_pct_of_total": -0.05, "n_trades": 30}}}
    gen_history = [{"generation": 1, "layer_gaps": {"alpha_006": {"gap": 0.10}},
                    "shaping_overrides": {"alpha_006_contrib_penalty": 0.5}}]
    manifest_raw = {"strategy_name": "x",
                    "inspiration": {"persistence": {"gap_threshold": 0.20, "min_generations": 3},
                                    "llm": {"config_key": "glm"}}}
    with patch("adapters.csi300_alpha101.hypothesize_run") as mock_hyp:
        action = check_triggers(review, gen_history, manifest_raw,
                                hypothesis_dir=str(tmp_path))
    assert action is None
    mock_hyp.assert_not_called()
