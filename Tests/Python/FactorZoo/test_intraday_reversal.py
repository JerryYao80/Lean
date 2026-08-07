"""Tests for intraday_reversal factor builder.

Tests:
- Accumulation: close near high -> reversal near 1
- Distribution: close near low -> reversal near 0
- Neutral: high == low -> reversal = 0.5
- Missing data -> empty output
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_TUSHARE_DIR = Path(__file__).resolve().parents[3] / "data-source" / "tushare"
if str(_TUSHARE_DIR) not in sys.path:
    sys.path.insert(0, str(_TUSHARE_DIR))


def _write_partition(tmp_path: Path, table: str, ts_code: str,
                     df: pd.DataFrame) -> Path:
    """Write {tmp}/{table}/ts_code={ts_code}/data.parquet."""
    d = tmp_path / table / f"ts_code={ts_code}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "data.parquet"
    df.to_parquet(p, index=False)
    return p


def _daily_row(trade_date, close, high, low, vol=1000.0, pre_close=10.0, pct_chg=0.0):
    return {
        "ts_code": "600519.SH",
        "trade_date": trade_date,
        "close": close,
        "high": high,
        "low": low,
        "vol": vol,
        "pre_close": pre_close,
        "pct_chg": pct_chg,
    }


def test_intraday_reversal_accumulation(tmp_path):
    """Close near high -> accumulation -> reversal near 1."""
    from factor_builders.intraday_reversal_builder import build_day, FACTOR_ID

    # high=100, low=90, close=99 -> (99-90)/(100-90) = 9/10 = 0.9
    daily = pd.DataFrame([_daily_row("20240115", 99.0, 100.0, 90.0)])
    _write_partition(tmp_path, "daily", "600519.SH", daily)

    out = build_day("2024-01-15", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    assert val == pytest.approx(0.9, rel=1e-9)
    # Builder writes: result_root/factor-zoo/<factor_id>/<date>.parquet
    p = tmp_path / "factor-zoo" / FACTOR_ID / "2024-01-15.parquet"
    assert p.exists()


def test_intraday_reversal_distribution(tmp_path):
    """Close near low -> distribution -> reversal near 0."""
    from factor_builders.intraday_reversal_builder import build_day, FACTOR_ID

    # high=100, low=90, close=91 -> (91-90)/(100-90) = 1/10 = 0.1
    daily = pd.DataFrame([_daily_row("20240115", 91.0, 100.0, 90.0)])
    _write_partition(tmp_path, "daily", "600519.SH", daily)

    out = build_day("2024-01-15", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    assert val == pytest.approx(0.1, rel=1e-9)


def test_intraday_reversal_neutral(tmp_path):
    """High == low -> neutral -> reversal = 0.5."""
    from factor_builders.intraday_reversal_builder import build_day, FACTOR_ID

    # high=low=100, close=100 -> 0.5
    daily = pd.DataFrame([_daily_row("20240115", 100.0, 100.0, 100.0)])
    _write_partition(tmp_path, "daily", "600519.SH", daily)

    out = build_day("2024-01-15", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    assert val == pytest.approx(0.5, rel=1e-9)


def test_intraday_reversal_multiple_days_latest(tmp_path):
    """Multiple days present; should use the latest (closest to asof)."""
    from factor_builders.intraday_reversal_builder import build_day, FACTOR_ID

    daily = pd.DataFrame([
        _daily_row("20240114", 50.0, 100.0, 40.0),   # old
        _daily_row("20240115", 99.0, 100.0, 90.0),   # latest -> 0.9
    ])
    _write_partition(tmp_path, "daily", "600519.SH", daily)

    out = build_day("2024-01-15", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    # Should use 2024-01-15: (99-90)/(100-90) = 0.9
    assert val == pytest.approx(0.9, rel=1e-9)


def test_intraday_reversal_missing_data(tmp_path):
    """No daily data -> empty output."""
    from factor_builders.intraday_reversal_builder import build_day

    out = build_day("2024-01-15", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert out.empty


def test_intraday_reversal_missing_columns(tmp_path):
    """Daily data missing required columns -> empty output."""
    from factor_builders.intraday_reversal_builder import build_day

    # Missing 'low' column
    daily = pd.DataFrame([{
        "ts_code": "600519.SH",
        "trade_date": "20240115",
        "close": 99.0,
        "high": 100.0,
    }])
    _write_partition(tmp_path, "daily", "600519.SH", daily)

    out = build_day("2024-01-15", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert out.empty
