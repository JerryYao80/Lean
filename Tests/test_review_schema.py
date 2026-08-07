"""Tests for review_schema.json. Spec §2, §6.1."""
import json
import sys
from pathlib import Path

import jsonschema
import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "review"))
SCHEMA = json.loads((_REPO / "Scripts" / "review" / "schema" / "review_schema.json").read_text())


def _good_doc():
    return {
        "run_meta": {
            "strategy_name": "Gold2BetaVolTargetStrategy",
            "backtest_id": "Gold2BetaVolTargetStrategy",
            "period_start": "2020-07-01",
            "period_end": "2026-06-23",
            "total_closed_trade_pnl": "12345.67",
            "generated_at": "2026-07-10T00:00:00Z",
            "adapter_version": "1.0.0",
            "schema_version": "1",
        },
        "layer_attribution": {
            "trend": {"pnl_abs": "100.0", "pnl_pct_of_total": 0.5, "n_trades": 10,
                      "n_wins": 6, "contribution_to_total_return": 0.05},
            "vol_target": {"pnl_abs": "50.0", "pnl_pct_of_total": 0.25, "n_trades": 10,
                           "n_wins": 5, "contribution_to_total_return": 0.025},
            "extreme_risk": {"pnl_abs": "-20.0", "pnl_pct_of_total": -0.1, "n_trades": 3,
                             "n_wins": 1, "contribution_to_total_return": -0.01},
            "realrate_cap": {"pnl_abs": "70.0", "pnl_pct_of_total": 0.35, "n_trades": 8,
                             "n_wins": 4, "contribution_to_total_return": 0.035},
        },
        "per_trade_narrative": [
            {"trade_id": 1, "symbol": "518880", "entry_time": "2020-07-01",
             "exit_time": "2020-07-05", "entry_price": "10.0", "exit_price": "11.0",
             "direction": "long", "quantity": "100", "pnl": "100.0", "fees": "5.0",
             "mae": "0.0", "mfe": "1.5", "end_trade_drawdown": "0.0", "days_held": 4,
             "layer_contributions": {"trend": "100.0"}},
        ],
        "drawdown_attribution": [
            {"peak_time": "2021-01-01", "trough_time": "2021-03-01",
             "recovery_time": "2021-06-01", "depth_pct": -0.15,
             "top_contributing_layers": [{"layer": "trend", "pnl_abs": "-50.0"}],
             "top_contributing_trades": [5, 12], "regime_during": "RISING_FAST",
             "regime_source": "state_trace"},
        ],
        "tca": {"avg_slippage_bps": 1.2, "fill_quality_score": 0.95, "n_fills": 1220,
                "source": "orderSubmissionData"},
    }


def test_good_doc_validates():
    jsonschema.validate(_good_doc(), SCHEMA)


@pytest.mark.parametrize("missing", ["run_meta", "layer_attribution", "per_trade_narrative", "drawdown_attribution", "tca"])
def test_missing_top_level_fails(missing):
    doc = _good_doc()
    doc.pop(missing)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCHEMA)


def test_layer_attribution_layer_missing_required_field_fails():
    doc = _good_doc()
    doc["layer_attribution"]["trend"].pop("pnl_abs")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCHEMA)


def test_tca_can_be_null():
    """tca block present but null is allowed (artifact-trimmed case)."""
    doc = _good_doc()
    doc["tca"] = None
    jsonschema.validate(doc, SCHEMA)
