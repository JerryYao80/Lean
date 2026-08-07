"""Tests for turnover_anomaly_builder.py.

Hermetic: each test writes synthetic tushare-style parquet fixtures under
tmp_path, calls build_day(date, [ts_code], data_root=tmp, result_root=tmp),
and asserts (a) the returned DataFrame is non-empty, (b) the per-date
parquet exists at result/factor-zoo/turnover_anomaly/<date>.parquet, and
(c) the value matches expected Z-score.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Make data-source/tushare importable as `factor_builders` package.
_TUSHARE_DIR = Path(__file__).resolve().parents[3] / "data-source" / "tushare"
if str(_TUSHARE_DIR) not in sys.path:
    sys.path.insert(0, str(_TUSHARE_DIR))


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────
def _write_partition(tmp_path: Path, table: str, ts_code: str,
                     df: pd.DataFrame) -> Path:
    """Write {tmp}/{table}/ts_code={ts_code}/data.parquet."""
    d = tmp_path / table / f"ts_code={ts_code}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "data.parquet"
    df.to_parquet(p, index=False)
    return p


def _daily_basic_row(trade_date, turnover_rate):
    return {
        "ts_code": "600519.SH",
        "trade_date": trade_date,
        "turnover_rate": turnover_rate,
    }


# ────────────────────────────────────────────────────────────────────────────
# Tests for _compute_turnover_zscore
# ────────────────────────────────────────────────────────────────────────────
def test_compute_turnover_zscore_high_turnover():
    """High turnover today vs varying historical average -> positive Z-score."""
    from factor_builders.turnover_anomaly_builder import _compute_turnover_zscore

    # 21 days: days 1-20 have varying turnover around 1.0, day 21 has high (5.0)
    dates = [f"202401{d:02d}" for d in range(1, 22)]
    turnover = [0.8, 1.2, 0.9, 1.1, 1.0, 0.7, 1.3, 0.85, 1.15, 1.0,
                0.9, 1.1, 0.8, 1.2, 1.0, 0.95, 1.05, 0.88, 1.12, 1.0,
                5.0]  # day 21 = high
    df = pd.DataFrame([
        _daily_basic_row(d, t) for d, t in zip(dates, turnover)
    ])

    z = _compute_turnover_zscore(df, "20240121")
    assert z is not None
    assert z > 0.0
    assert z > 3.0  # significantly positive
    assert z <= 5.0  # clamped


def test_compute_turnover_zscore_high_vs_varying_history():
    """High turnover today vs varying history -> positive Z-score."""
    from factor_builders.turnover_anomaly_builder import _compute_turnover_zscore

    # 21 days: days 1-20 have varying turnover around 1.0, day 21 has 5.0
    dates = [f"202401{d:02d}" for d in range(1, 22)]
    turnover = [0.8, 1.2, 0.9, 1.1, 1.0, 0.7, 1.3, 0.85, 1.15, 1.0,
                0.9, 1.1, 0.8, 1.2, 1.0, 0.95, 1.05, 0.88, 1.12, 1.0,
                5.0]  # day 21 = high
    df = pd.DataFrame([
        _daily_basic_row(d, t) for d, t in zip(dates, turnover)
    ])

    z = _compute_turnover_zscore(df, "20240121")
    assert z is not None
    assert z > 0.0
    # z should be significantly positive (5.0 is far above ~1.0 mean)
    assert z > 3.0
    # Clamped to [-5, 5]
    assert z <= 5.0


def test_compute_turnover_zscore_neutral_turnover():
    """Turnover at historical mean -> near-zero Z-score."""
    from factor_builders.turnover_anomaly_builder import _compute_turnover_zscore

    dates = [f"202401{d:02d}" for d in range(1, 22)]
    turnover = [0.8, 1.2, 0.9, 1.1, 1.0, 0.7, 1.3, 0.85, 1.15, 1.0,
                0.9, 1.1, 0.8, 1.2, 1.0, 0.95, 1.05, 0.88, 1.12, 1.0,
                1.0]  # day 21 = exactly at mean
    df = pd.DataFrame([
        _daily_basic_row(d, t) for d, t in zip(dates, turnover)
    ])

    z = _compute_turnover_zscore(df, "20240121")
    assert z is not None
    assert abs(z) < 0.01  # essentially zero


def test_compute_turnover_zscore_low_turnover():
    """Low turnover today vs high historical average -> negative Z-score."""
    from factor_builders.turnover_anomaly_builder import _compute_turnover_zscore

    dates = [f"202401{d:02d}" for d in range(1, 22)]
    turnover = [2.0, 3.0, 2.5, 3.5, 2.8, 3.2, 2.1, 3.9, 2.7, 3.3,
                2.4, 3.6, 2.9, 3.1, 2.2, 3.8, 2.6, 3.4, 2.3, 3.7,
                0.1]  # day 21 = very low
    df = pd.DataFrame([
        _daily_basic_row(d, t) for d, t in zip(dates, turnover)
    ])

    z = _compute_turnover_zscore(df, "20240121")
    assert z is not None
    assert z < 0.0
    assert z < -3.0
    # Clamped to [-5, 5]
    assert z >= -5.0


def test_compute_turnover_zscore_clamped():
    """Extreme outlier is clamped to [-5, 5]."""
    from factor_builders.turnover_anomaly_builder import _compute_turnover_zscore

    dates = [f"202401{d:02d}" for d in range(1, 22)]
    # Varying history so std > 0, then extreme outlier on day 21
    turnover = [0.8, 1.2, 0.9, 1.1, 1.0, 0.7, 1.3, 0.85, 1.15, 1.0,
                0.9, 1.1, 0.8, 1.2, 1.0, 0.95, 1.05, 0.88, 1.12, 1.0,
                100.0]  # extreme outlier
    df = pd.DataFrame([
        _daily_basic_row(d, t) for d, t in zip(dates, turnover)
    ])

    z = _compute_turnover_zscore(df, "20240121")
    assert z is not None
    assert z == 5.0  # clamped at upper bound


def test_compute_turnover_zscore_zero_std():
    """If historical std == 0, return 0.0."""
    from factor_builders.turnover_anomaly_builder import _compute_turnover_zscore

    dates = [f"202401{d:02d}" for d in range(1, 22)]
    turnover = [1.0] * 21  # all identical
    df = pd.DataFrame([
        _daily_basic_row(d, t) for d, t in zip(dates, turnover)
    ])

    z = _compute_turnover_zscore(df, "20240121")
    assert z is not None
    assert z == 0.0


def test_compute_turnover_zscore_insufficient_history():
    """Less than 21 days -> None."""
    from factor_builders.turnover_anomaly_builder import _compute_turnover_zscore

    dates = [f"202401{d:02d}" for d in range(1, 21)]  # only 20 days
    turnover = [1.0] * 20
    df = pd.DataFrame([
        _daily_basic_row(d, t) for d, t in zip(dates, turnover)
    ])

    z = _compute_turnover_zscore(df, "20240120")
    assert z is None


def test_compute_turnover_zscore_empty_df():
    """Empty DataFrame -> None."""
    from factor_builders.turnover_anomaly_builder import _compute_turnover_zscore

    z = _compute_turnover_zscore(pd.DataFrame(), "20240121")
    assert z is None


def test_compute_turnover_zscore_missing_columns():
    """Missing required columns -> None."""
    from factor_builders.turnover_anomaly_builder import _compute_turnover_zscore

    df = pd.DataFrame({"trade_date": ["20240121"], "close": [100.0]})
    z = _compute_turnover_zscore(df, "20240121")
    assert z is None


# ────────────────────────────────────────────────────────────────────────────
# Tests for build_day (integration)
# ────────────────────────────────────────────────────────────────────────────
def test_turnover_anomaly_basic(tmp_path):
    """Full build_day integration test with synthetic parquet."""
    from factor_builders.turnover_anomaly_builder import build_day, FACTOR_ID

    # 25 days of data for 600519.SH
    dates = [f"202401{d:02d}" for d in range(1, 26)]
    turnover = [0.8, 1.2, 0.9, 1.1, 1.0, 0.7, 1.3, 0.85, 1.15, 1.0,
                0.9, 1.1, 0.8, 1.2, 1.0, 0.95, 1.05, 0.88, 1.12, 1.0,
                1.0, 1.0, 1.0, 1.0,
                5.0]  # day 25 = high turnover
    df = pd.DataFrame([
        _daily_basic_row(d, t) for d, t in zip(dates, turnover)
    ])
    _write_partition(tmp_path, "daily_basic", "600519.SH", df)

    asof = "2024-01-25"
    out = build_day(asof, ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    assert len(out) == 1
    val = float(out.iloc[0][FACTOR_ID])
    assert val > 0.0
    assert val > 3.0  # significantly positive
    assert val <= 5.0  # clamped

    # Check parquet was written
    p = tmp_path / "factor-zoo" / FACTOR_ID / f"{asof}.parquet"
    assert p.exists()


def test_turnover_anomaly_neutral(tmp_path):
    """Turnover at historical mean -> near-zero Z-score via build_day."""
    from factor_builders.turnover_anomaly_builder import build_day, FACTOR_ID

    dates = [f"202401{d:02d}" for d in range(1, 26)]
    turnover = [0.8, 1.2, 0.9, 1.1, 1.0, 0.7, 1.3, 0.85, 1.15, 1.0,
                0.9, 1.1, 0.8, 1.2, 1.0, 0.95, 1.05, 0.88, 1.12, 1.0,
                1.0, 1.0, 1.0, 1.0,
                1.0]  # day 25 = at mean
    df = pd.DataFrame([
        _daily_basic_row(d, t) for d, t in zip(dates, turnover)
    ])
    _write_partition(tmp_path, "daily_basic", "600519.SH", df)

    out = build_day("2024-01-25", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    assert abs(val) < 0.01


def test_turnover_anomaly_insufficient_history(tmp_path):
    """Only 20 days (< 21 needed) -> empty output."""
    from factor_builders.turnover_anomaly_builder import build_day

    dates = [f"202401{d:02d}" for d in range(1, 21)]  # 20 days only
    turnover = [1.0] * 20
    df = pd.DataFrame([
        _daily_basic_row(d, t) for d, t in zip(dates, turnover)
    ])
    _write_partition(tmp_path, "daily_basic", "600519.SH", df)

    out = build_day("2024-01-20", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert out.empty


def test_turnover_anomaly_missing_table(tmp_path):
    """Missing daily_basic table -> empty output."""
    from factor_builders.turnover_anomaly_builder import build_day

    # Don't write any parquet
    out = build_day("2024-01-25", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert out.empty


def test_turnover_anomaly_multiple_ts_codes(tmp_path):
    """Build for multiple ts_codes in one call."""
    from factor_builders.turnover_anomaly_builder import build_day, FACTOR_ID

    dates = [f"202401{d:02d}" for d in range(1, 26)]

    # Stock A: high turnover on last day
    turnover_a = [0.8, 1.2, 0.9, 1.1, 1.0, 0.7, 1.3, 0.85, 1.15, 1.0,
                  0.9, 1.1, 0.8, 1.2, 1.0, 0.95, 1.05, 0.88, 1.12, 1.0,
                  1.0, 1.0, 1.0, 1.0, 5.0]
    df_a = pd.DataFrame([
        {"ts_code": "600519.SH", "trade_date": d, "turnover_rate": t}
        for d, t in zip(dates, turnover_a)
    ])
    _write_partition(tmp_path, "daily_basic", "600519.SH", df_a)

    # Stock B: low turnover on last day
    turnover_b = [2.0, 3.0, 2.5, 3.5, 2.8, 3.2, 2.1, 3.9, 2.7, 3.3,
                  2.4, 3.6, 2.9, 3.1, 2.2, 3.8, 2.6, 3.4, 2.3, 3.7,
                  3.0, 3.0, 3.0, 3.0, 0.1]
    df_b = pd.DataFrame([
        {"ts_code": "000001.SZ", "trade_date": d, "turnover_rate": t}
        for d, t in zip(dates, turnover_b)
    ])
    _write_partition(tmp_path, "daily_basic", "000001.SZ", df_b)

    out = build_day("2024-01-25", ["600519.SH", "000001.SZ"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    assert len(out) == 2

    # Stock A should have positive Z
    val_a = float(out[out["ts_code"] == "600519.SH"].iloc[0][FACTOR_ID])
    assert val_a > 0.0

    # Stock B should have negative Z
    val_b = float(out[out["ts_code"] == "000001.SZ"].iloc[0][FACTOR_ID])
    assert val_b < 0.0


def test_turnover_anomaly_clamped_lower(tmp_path):
    """Extreme low turnover is clamped to -5."""
    from factor_builders.turnover_anomaly_builder import build_day, FACTOR_ID

    dates = [f"202401{d:02d}" for d in range(1, 26)]
    # Varying history so std > 0, then extreme low on last day
    turnover = [2.0, 3.0, 2.5, 3.5, 2.8, 3.2, 2.1, 3.9, 2.7, 3.3,
                2.4, 3.6, 2.9, 3.1, 2.2, 3.8, 2.6, 3.4, 2.3, 3.7,
                3.0, 3.0, 3.0, 3.0, 0.001]  # extreme low
    df = pd.DataFrame([
        _daily_basic_row(d, t) for d, t in zip(dates, turnover)
    ])
    _write_partition(tmp_path, "daily_basic", "600519.SH", df)

    out = build_day("2024-01-25", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    assert val == -5.0
