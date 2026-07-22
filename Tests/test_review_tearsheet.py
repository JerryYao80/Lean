"""Tests for HTML tearsheet builder. Spec §4.1, §6.1."""
import sys
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "Scripts" / "review"))
from tearsheet.builder import build_html  # noqa: E402


def _doc():
    return {
        "run_meta": {"strategy_name": "Gold2BetaVolTargetStrategy", "backtest_id": "x",
                     "period_start": "2020", "period_end": "2026",
                     "total_closed_trade_pnl": "100.0", "generated_at": "now",
                     "adapter_version": "1", "schema_version": "1"},
        "layer_attribution": {
            "trend": {"pnl_abs": "60.0", "pnl_pct_of_total": 0.6, "n_trades": 5, "n_wins": 3, "contribution_to_total_return": 0.06},
            "vol_target": {"pnl_abs": "40.0", "pnl_pct_of_total": 0.4, "n_trades": 5, "n_wins": 2, "contribution_to_total_return": 0.04},
            "extreme_risk": {"pnl_abs": "0.0", "pnl_pct_of_total": 0.0, "n_trades": 0, "n_wins": 0, "contribution_to_total_return": 0.0},
            "realrate_cap": {"pnl_abs": "0.0", "pnl_pct_of_total": 0.0, "n_trades": 0, "n_wins": 0, "contribution_to_total_return": 0.0},
        },
        "per_trade_narrative": [],
        "drawdown_attribution": [],
        "tca": None,
    }


def test_build_html_returns_string():
    html = build_html(_doc())
    assert isinstance(html, str)
    assert "<html" in html.lower()


def test_html_contains_layer_attribution_panel():
    html = build_html(_doc())
    assert "trend" in html and "vol_target" in html


def test_html_is_self_contained_no_external_deps():
    """No CDN, no external script/style links. Spec §4.1."""
    html = build_html(_doc())
    assert "cdn" not in html.lower()
    assert "https://" not in html
    assert "plotly" not in html.lower()


def test_self_check_sum_invariant():
    """--self-check: sum(layer pnl_abs) == total_closed_trade_pnl. Spec §4.1 fail-fast."""
    import pytest
    doc = _doc()
    doc["layer_attribution"]["trend"]["pnl_abs"] = "999.0"  # break invariant
    with pytest.raises(AssertionError, match="sum"):
        build_html(doc, self_check=True)
