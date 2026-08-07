"""Tests for amplitude_anomaly_builder.

Tests _compute_amplitude_zscore directly and build_day integration.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

# Make data-source/tushare importable as `factor_builders` package.
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


def _daily_row(trade_date, high, low, pre_close, close=10.0, pct_chg=0.0, vol=1000.0):
    return {
        "ts_code": "600519.SH",
        "trade_date": trade_date,
        "high": high,
        "low": low,
        "pre_close": pre_close,
        "close": close,
        "pct_chg": pct_chg,
        "vol": vol,
    }


# ────────────────────────────────────────────────────────────────────────────
# Unit tests for _compute_amplitude_zscore
# ────────────────────────────────────────────────────────────────────────────
def test_compute_amplitude_zscore_extreme_amplitude():
    """Extreme amplitude (wide spread) should get a high positive Z-score."""
    from factor_builders.amplitude_anomaly_builder import _compute_amplitude_zscore

    # 21 days: trailing 20 have varying amplitude (mean ~0.1, std > 0),
    # current day has extreme amplitude = 0.5
    rows = []
    for i in range(20):
        # Vary high between 10.5 and 10.7 to get non-zero variance
        high = 10.5 + (i % 3) * 0.1  # 10.5, 10.6, 10.7 cycling
        rows.append(_daily_row(f"202401{i+1:02d}", high, 9.5, 10.0))
    # Day 21: extreme amplitude = (15.0 - 5.0) / 10.0 = 1.0
    rows.append(_daily_row("20240121", 15.0, 5.0, 10.0))

    df = pd.DataFrame(rows)
    z = _compute_amplitude_zscore(df, "20240121")
    assert z is not None
    assert z > 0.0  # extreme amplitude should have positive Z-score
    assert z <= 5.0  # clamped to max 5


def test_compute_amplitude_zscore_low_amplitude():
    """Very low amplitude (tight spread) should get a negative Z-score."""
    from factor_builders.amplitude_anomaly_builder import _compute_amplitude_zscore

    # 20 days of varying amplitude, then 1 day of very low amplitude
    rows = []
    for i in range(20):
        high = 10.5 + (i % 3) * 0.1
        rows.append(_daily_row(f"202401{i+1:02d}", high, 9.5, 10.0))
    # Day 21: very low amplitude = (10.01 - 9.99) / 10.0 = 0.002
    rows.append(_daily_row("20240121", 10.01, 9.99, 10.0))

    df = pd.DataFrame(rows)
    z = _compute_amplitude_zscore(df, "20240121")
    assert z is not None
    assert z < 0.0  # low amplitude should have negative Z-score
    assert z >= -5.0  # clamped to min -5


def test_compute_amplitude_zscore_clamped():
    """Z-score should be clamped to [-5, 5]."""
    from factor_builders.amplitude_anomaly_builder import _compute_amplitude_zscore

    # 20 days of very small varying amplitude, then 1 day of huge amplitude
    rows = []
    for i in range(20):
        high = 10.001 + (i % 3) * 0.001
        rows.append(_daily_row(f"202401{i+1:02d}", high, 9.999, 10.0))
    # Day 21: huge amplitude = (100.0 - 0.0) / 10.0 = 10.0
    rows.append(_daily_row("20240121", 100.0, 0.0, 10.0))

    df = pd.DataFrame(rows)
    z = _compute_amplitude_zscore(df, "20240121")
    assert z is not None
    assert z == 5.0  # clamped to max


def test_compute_amplitude_zscore_insufficient_data():
    """Less than 21 days should return None."""
    from factor_builders.amplitude_anomaly_builder import _compute_amplitude_zscore

    rows = [_daily_row(f"202401{i+1:02d}", 10.5, 9.5, 10.0) for i in range(20)]
    df = pd.DataFrame(rows)
    z = _compute_amplitude_zscore(df, "20240120")
    assert z is None


def test_compute_amplitude_zscore_zero_std():
    """If all trailing amplitudes are identical, Z-score should be 0.0."""
    from factor_builders.amplitude_anomaly_builder import _compute_amplitude_zscore

    # 21 days of identical amplitude
    rows = [_daily_row(f"202401{i+1:02d}", 11.0, 9.0, 10.0) for i in range(21)]
    df = pd.DataFrame(rows)
    z = _compute_amplitude_zscore(df, "20240121")
    assert z is not None
    assert z == 0.0


def test_compute_amplitude_zscore_missing_columns():
    """Missing required columns should return None."""
    from factor_builders.amplitude_anomaly_builder import _compute_amplitude_zscore

    df = pd.DataFrame({"trade_date": ["20240101"], "close": [10.0]})
    z = _compute_amplitude_zscore(df, "20240101")
    assert z is None


def test_compute_amplitude_zscore_zero_pre_close():
    """Zero pre_close should be filtered out."""
    from factor_builders.amplitude_anomaly_builder import _compute_amplitude_zscore

    rows = [_daily_row(f"202401{i+1:02d}", 11.0, 9.0, 10.0) for i in range(20)]
    # Day 21 with zero pre_close (should be filtered, leaving < 21 valid rows)
    rows.append(_daily_row("20240121", 11.0, 9.0, 0.0))
    df = pd.DataFrame(rows)
    z = _compute_amplitude_zscore(df, "20240121")
    assert z is None


# ────────────────────────────────────────────────────────────────────────────
# Integration tests for build_day
# ────────────────────────────────────────────────────────────────────────────
def test_amplitude_anomaly_basic(tmp_path):
    from factor_builders.amplitude_anomaly_builder import build_day, FACTOR_ID

    # 25 days: trailing 24 have varying amplitude, last day has extreme amplitude
    rows = []
    for i in range(24):
        high = 10.5 + (i % 3) * 0.1
        rows.append(_daily_row(f"202401{i+1:02d}", high, 9.5, 10.0))
    # Day 25: extreme amplitude
    rows.append(_daily_row("20240125", 15.0, 5.0, 10.0))

    _write_partition(tmp_path, "daily", "600519.SH", pd.DataFrame(rows))

    asof = "2024-01-25"
    out = build_day(asof, ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    assert val > 0.0  # extreme amplitude day should have positive Z-score
    assert val <= 5.0  # clamped
    p = tmp_path / "factor-zoo" / FACTOR_ID / f"{asof}.parquet"
    assert p.exists()


def test_amplitude_anomaly_insufficient_history(tmp_path):
    """< 21 days -> skip."""
    from factor_builders.amplitude_anomaly_builder import build_day

    rows = [_daily_row(f"202401{i+1:02d}", 10.5, 9.5, 10.0) for i in range(20)]
    _write_partition(tmp_path, "daily", "600519.SH", pd.DataFrame(rows))

    out = build_day("2024-01-20", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert out.empty


def test_amplitude_anomaly_negative_zscore(tmp_path):
    """A day with very low amplitude should have negative Z-score."""
    from factor_builders.amplitude_anomaly_builder import build_day, FACTOR_ID

    # 24 days of varying amplitude, then 1 day of very low amplitude
    rows = []
    for i in range(24):
        high = 10.5 + (i % 3) * 0.1
        rows.append(_daily_row(f"202401{i+1:02d}", high, 9.5, 10.0))
    # Day 25: very tight spread
    rows.append(_daily_row("20240125", 10.001, 9.999, 10.0))

    _write_partition(tmp_path, "daily", "600519.SH", pd.DataFrame(rows))

    asof = "2024-01-25"
    out = build_day(asof, ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    assert val < 0.0  # low amplitude day should have negative Z-score
    assert val >= -5.0  # clamped
