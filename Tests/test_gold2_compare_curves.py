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


def test_verdict_baseline_zero_full_zero_is_effective():
    """baseline 无回撤(=0)、full 也无回撤(=0):视作不劣化,EFFECTIVE(pass-through)。
    baseline_max_dd>=0 边界,full>=0 唯一成立(==0)的路径。"""
    v = cmp.verdict(baseline_max_dd=0.0, full_max_dd=0.0)
    assert v == "EFFECTIVE", f"baseline=0 full=0 (no drawdown either) should be EFFECTIVE, got {v}"


def test_verdict_baseline_zero_full_negative_is_fail():
    """baseline 无回撤(=0,monotonic rise)、full -50% 回撤:对"无回撤基准"是劣化,FAIL。
    原 bug: full_max_dd <= 0 被判 EFFECTIVE(因 -0.5 <= 0 True)。修正后 full<0 判 FAIL。"""
    v = cmp.verdict(baseline_max_dd=0.0, full_max_dd=-0.50)
    assert v == "EFFECTIVENESS_FAIL", (
        "baseline=0 (never drew down) + full=-50% is a degradation, should FAIL, got " + v)


def test_verdict_baseline_positive_full_positive_is_effective():
    """baseline_max_dd 正数(理论边界:仅当 baseline 全程无回撤且终值更高,dd 计算为 0 但
    实现上若极端返回 +0.001 这类)与 full 也正数:均无回撤 → EFFECTIVE。"""
    v = cmp.verdict(baseline_max_dd=0.0, full_max_dd=0.0)
    assert v == "EFFECTIVE"


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


def test_load_equity_series_filters_weekend_forward_fill(tmp_path):
    """LEAN Strategy Equity 按日历日发射,周末是周五收盘的 forward-fill 副本。
    loader 必须过滤周末 bar(dayofweek<5),否则 n 被稀释 ~35%,Sharpe 被零收益周末 bar
    抬高分母,dd_duration 混用日历日 vs 交易日单位。"""
    folder = tmp_path / "gold2-betavol"
    folder.mkdir()
    # 周五 2020-01-03 + 周六 04 + 周日 05 + 周一 06,共 4 个 ts
    payload = {"charts": {"Strategy Equity": {"series": {"Equity": {"values": [
        [1578027600, 1_000_000.0, 1_000_000.0, 1_000_000.0, 1_000_000.0],  # Fri 2020-01-03
        [1578114000, 1_000_000.0, 1_000_000.0, 1_000_000.0, 1_000_000.0],  # Sat 2020-01-04 (ffill)
        [1578200400, 1_000_000.0, 1_000_000.0, 1_000_000.0, 1_000_000.0],  # Sun 2020-01-05 (ffill)
        [1578286800, 1_010_000.0, 1_010_000.0, 1_010_000.0, 1_010_000.0],  # Mon 2020-01-06
    ]}}}}}
    (folder / "FakeStrategy.json").write_text(json.dumps(payload))
    s = cmp.load_equity_series(str(folder))
    assert s is not None
    # 周末两 bar 必须被滤掉:仅 Fri + Mon 留下
    assert len(s) == 2, f"expected 2 weekday bars (Fri+Mon), got {len(s)}"
    dow = list(s.index.dayofweek)
    assert max(dow) <= 4, f"weekend bar leaked into series, dayofweek={dow}"
    assert 1_010_000.0 == s.iloc[-1]


def test_align_to_baseline_reindexes_strategy_onto_trading_days():
    """align_to_baseline 必须把 strategy reindex 到 baseline 的交易日索引,
    使 n_strategy == n_baseline(年化天数分母一致),dd_duration 单位统一为交易日。"""
    # baseline: 5 个交易日(Mon..Fri)
    baseline = pd.Series([1.0, 1.1, 1.2, 1.1, 1.3],
                         index=pd.date_range("2020-01-06", periods=5, freq="B"))
    # strategy: 仅 3 个点(Mon/Wed/Fri),缺 Tue/Thu → reindex 后 ffill 补齐到 5 个
    strategy = pd.Series([1.0, 1.2, 1.3],
                         index=pd.to_datetime(["2020-01-06", "2020-01-08", "2020-01-10"]))
    aligned = cmp.align_to_baseline(strategy, baseline)
    assert len(aligned) == len(baseline), (
        f"aligned len {len(aligned)} must equal baseline len {len(baseline)}")
    # Tue/Thu 必须 ffill 成 Mon/Wed 的值
    assert aligned.iloc[1] == 1.0  # Tue = Mon's 1.0
    assert aligned.iloc[3] == 1.2  # Thu = Wed's 1.2
