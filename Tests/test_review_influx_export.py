"""Tests for review_* InfluxDB line-protocol export. Spec §4.2. Dry-run (no live InfluxDB)."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "review"))
sys.path.insert(0, str(_REPO / "Scripts"))
from influx_export import build_lines  # noqa: E402


def _doc():
    return {
        "run_meta": {"strategy_name": "Gold2BetaVolTargetStrategy", "total_closed_trade_pnl": "100.0",
                     "period_end": "2026-06-23T00:00:00Z"},
        "layer_attribution": {
            "trend": {"pnl_abs": "60.0", "pnl_pct_of_total": 0.6, "n_trades": 5, "n_wins": 3, "contribution_to_total_return": 0.06},
        },
        "per_trade_narrative": [
            {"trade_id": 1, "symbol": "518880", "exit_time": "2020-07-05T00:00:00Z",
             "direction": "long", "pnl": "100.0", "mae": "0.0", "mfe": "1.5", "days_held": 4,
             "layer_contributions": {"trend": "100.0"}, "regime_at_entry": {}},
        ],
        "drawdown_attribution": [
            {"trough_time": "2021-03-01T00:00:00Z", "depth_pct": -0.15, "top_layer": "trend", "duration_days": 60},
        ],
        "tca": {"avg_slippage_bps": 1.2, "fill_quality_score": 0.95, "n_fills": 100},
    }


def test_build_lines_returns_review_layer_attribution():
    lines = build_lines(_doc(), algorithm_id="Gold2BetaVolTargetStrategy", mode="backtesting", run_id="r1")
    layer_lines = [l for l in lines if l.startswith("review_layer_attribution")]
    assert len(layer_lines) >= 1
    assert "algorithm_id=Gold2BetaVolTargetStrategy" in layer_lines[0]
    assert "layer=trend" in layer_lines[0]
    assert "pnl_abs=60" in layer_lines[0]


def test_build_lines_returns_review_trade():
    lines = build_lines(_doc(), algorithm_id="Gold2", mode="backtesting", run_id="r1")
    trade_lines = [l for l in lines if l.startswith("review_trade")]
    assert len(trade_lines) == 1
    assert "symbol=518880" in trade_lines[0]
    assert "direction=long" in trade_lines[0]
    assert "trend_contrib=100" in trade_lines[0]


def test_build_lines_returns_review_drawdown():
    lines = build_lines(_doc(), algorithm_id="Gold2", mode="backtesting", run_id="r1")
    dd_lines = [l for l in lines if l.startswith("review_drawdown")]
    assert len(dd_lines) == 1
    assert "depth_pct=" in dd_lines[0]
    assert "top_layer=\"trend\"" in dd_lines[0]


def test_build_lines_returns_review_tca():
    lines = build_lines(_doc(), algorithm_id="Gold2", mode="backtesting", run_id="r1")
    tca_lines = [l for l in lines if l.startswith("review_tca")]
    assert len(tca_lines) == 1
    assert "avg_slippage_bps=1.2" in tca_lines[0]
