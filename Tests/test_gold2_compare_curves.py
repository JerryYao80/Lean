#!/usr/bin/env python3
"""TDD test for Scripts/gold2_compare_curves.py (plan Task 12).
Covers the pure functions: compute_stats, verdict, load_equity_series shape.
The integration (reading real LEAN JSON + tushare) is exercised by the CLI run
in plan Step 5; here we lock down the math + verdict logic so a regression in
the comparison math is caught before the verdict is reported."""
import os, sys, json
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "Scripts"))

import gold2_compare_curves as cmp  # FAILS until impl exists


def test_compute_stats_monotonic_rise_zero_drawdown():
    """A strictly rising equity curve must report max_dd == 0 and positive ann_ret."""
    eq = pd.Series([1000000 * (1.001 ** i) for i in range(252)],
                    index=pd.date_range("2020-01-02", periods=252, freq="B"))
    s = cmp.compute_stats(eq)
    assert s["max_dd"] == 0.0, f"monotonic rise should have 0 drawdown, got {s['max_dd']}"
    assert s["ann_ret"] > 0, f"rising equity should have positive annual return, got {s['ann_ret']}"
    assert s["sharpe"] > 0, f"positive-mean returns should give positive sharpe, got {s['sharpe']}"
    assert s["dd_duration"] == 0


def test_compute_stats_known_drawdown():
    """Peak 1.2M -> trough 1.0M is a -16.67% drawdown; verify exact value."""
    eq = pd.Series([1_000_000, 1_200_000, 1_000_000, 1_100_000, 1_150_000],
                    index=pd.date_range("2020-01-02", periods=5, freq="D"))
    s = cmp.compute_stats(eq)
    assert abs(s["max_dd"] - (-1 / 6)) < 1e-6, f"expected -16.67%, got {s['max_dd']}"
    # drawdown duration: from day index 1 (peak) to day index 3 (recovery above 1.2M? no, 1.1M<1.2M)
    # actually 1.15M < 1.2M so drawdown never recovers -> duration spans days 1..4 = 3 bars under water
    assert s["dd_duration"] >= 1, f"expected nonzero underwater duration, got {s['dd_duration']}"


def test_verdict_effective_when_drawdown_improves_15pct():
    """spec §7.2: full < baseline×0.85 → EFFECTIVE (>=15% improvement)."""
    v = cmp.verdict(baseline_max_dd=-0.20, full_max_dd=-0.16)
    assert v == "EFFECTIVE", f"-0.16 vs -0.20 baseline (0.80x) should be EFFECTIVE, got {v}"


def test_verdict_fail_when_drawdown_not_improved():
    """spec §7.2: otherwise EFFECTIVENESS_FAIL."""
    v = cmp.verdict(baseline_max_dd=-0.20, full_max_dd=-0.18)
    assert v == "EFFECTIVENESS_FAIL", f"-0.18 vs -0.20 (0.90x, only 10% better) should FAIL, got {v}"


def test_verdict_boundary_exactly_15pct_is_effective():
    """Exactly 15% improvement (full = baseline×0.85) is EFFECTIVE (boundary inclusive)."""
    v = cmp.verdict(baseline_max_dd=-0.20, full_max_dd=-0.17)
    assert v == "EFFECTIVE", f"-0.17 vs -0.20 (exactly 0.85x) should be EFFECTIVE, got {v}"


def test_load_equity_series_reads_lean_json(tmp_path):
    """load_equity_series must parse the LEAN result JSON Strategy Equity candle series.
    LEAN equity values are [time, o, h, l, close] candles; the close (last element) is the
    equity value we want."""
    folder = tmp_path / "gold2-betavol"
    folder.mkdir()
    payload = {
        "charts": {
            "Strategy Equity": {
                "series": {
                    "Equity": {
                        "values": [
                            [1577854800, 1000000.0, 1000000.0, 1000000.0, 1000000.0],
                            [1577941200, 1000000.0, 1010000.0, 999000.0, 1005000.0],
                            [1578027600, 1005000.0, 1005000.0, 1005000.0, 1005000.0],
                        ]
                    }
                }
            }
        }
    }
    (folder / "FakeStrategy.json").write_text(json.dumps(payload))
    s = cmp.load_equity_series(str(folder))
    assert s is not None, "expected a series, got None"
    assert len(s) == 3, f"expected 3 daily points, got {len(s)}"
    assert s.iloc[0] == 1000000.0
    assert s.iloc[1] == 1005000.0, f"close should be last candle element, got {s.iloc[1]}"
    assert s.iloc[2] == 1005000.0


def test_load_equity_series_handles_2d_point_format(tmp_path):
    """Some LEAN series emit [time, value] pairs (e.g. Benchmark). loader should still
    return a series using the single value."""
    folder = tmp_path / "gold2-betavol"
    folder.mkdir()
    payload = {"charts": {"Strategy Equity": {"series": {"Equity": {"values": [
        [1577854800, 1000000.0], [1577941200, 1010000.0]]}}}}}
    (folder / "FakeStrategy.json").write_text(json.dumps(payload))
    s = cmp.load_equity_series(str(folder))
    assert len(s) == 2
    assert s.iloc[1] == 1010000.0


def test_load_equity_series_none_when_missing(tmp_path):
    """Missing folder / missing JSON must return None (not raise) so main() can skip."""
    assert cmp.load_equity_series(str(tmp_path / "nope")) is None
