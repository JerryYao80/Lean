"""Tests for limit_behavior_builder.

Hermetic: each test writes synthetic limit_list_d parquet fixtures under tmp_path,
calls build_day(date, [ts_code], data_root=tmp, result_root=tmp),
and asserts (a) the returned DataFrame is non-empty, (b) the per-ts_code
parquet exists at result/factor-zoo/<id>/<date>/<ts_code>.parquet, and
(c) the value is correct.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# Make data-source/tushare importable as `factor_builders` package.
_TUSHARE_DIR = Path(__file__).resolve().parents[3] / "data-source" / "tushare"
if str(_TUSHARE_DIR) not in sys.path:
    sys.path.insert(0, str(_TUSHARE_DIR))


# ────────────────────────────────────────────────────────────────────────────
# Helpers: write tushare-style partitioned parquet under tmp_path
# ────────────────────────────────────────────────────────────────────────────
def _write_date_partition(tmp_path: Path, table: str, year: int,
                          df: pd.DataFrame) -> Path:
    """Write {tmp}/{table}/year={year}/data.parquet."""
    d = tmp_path / table / f"year={year}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "data.parquet"
    df.to_parquet(p, index=False)
    return p


def _limit_row(trade_date, ts_code, limit="Z", up_stat=None):
    """Build a limit_list_d row."""
    return {
        "trade_date": trade_date,
        "ts_code": ts_code,
        "limit": limit,
        "up_stat": up_stat,
        "year": int(str(trade_date)[:4]),
    }


# ────────────────────────────────────────────────────────────────────────────
# 1. Basic test: 3 limit-ups + 1 limit-down in 5 days -> score 0.8
# ────────────────────────────────────────────────────────────────────────────
def test_limit_behavior_basic(tmp_path):
    from factor_builders.limit_behavior_builder import build_day, FACTOR_ID

    # 5 days of data for stock 600519.SH
    # Day 1: limit-up (U, up_stat non-null)
    # Day 2: no limit (Z, up_stat null)
    # Day 3: limit-up (U, up_stat non-null)
    # Day 4: limit-down (D, up_stat non-null per task spec)
    # Day 5: limit-up (U, up_stat non-null)
    rows = [
        _limit_row("20240101", "600519.SH", limit="U", up_stat="1/1"),
        _limit_row("20240102", "600519.SH", limit="Z", up_stat=None),
        _limit_row("20240103", "600519.SH", limit="U", up_stat="1/1"),
        _limit_row("20240104", "600519.SH", limit="D", up_stat="1/1"),
        _limit_row("20240105", "600519.SH", limit="U", up_stat="1/1"),
    ]
    df = pd.DataFrame(rows)
    _write_date_partition(tmp_path, "limit_list_d", 2024, df)

    out = build_day("2024-01-05", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    # 3 ups + 1 down = 4 limit events / 5 = 0.8
    assert val == pytest.approx(0.8, rel=1e-9)
    p = tmp_path / "factor-zoo" / FACTOR_ID / "2024-01-05.parquet"
    assert p.exists()


# ────────────────────────────────────────────────────────────────────────────
# 2. No limit records -> score 0.0
# ────────────────────────────────────────────────────────────────────────────
def test_limit_behavior_no_limits(tmp_path):
    from factor_builders.limit_behavior_builder import build_day, FACTOR_ID

    rows = [
        _limit_row("20240101", "600519.SH", limit="Z", up_stat=None),
        _limit_row("20240102", "600519.SH", limit="Z", up_stat=None),
        _limit_row("20240103", "600519.SH", limit="Z", up_stat=None),
    ]
    df = pd.DataFrame(rows)
    _write_date_partition(tmp_path, "limit_list_d", 2024, df)

    out = build_day("2024-01-03", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    assert val == pytest.approx(0.0, rel=1e-9)


# ────────────────────────────────────────────────────────────────────────────
# 3. Stock not present in limit_list_d -> score 0.0
# ────────────────────────────────────────────────────────────────────────────
def test_limit_behavior_missing_stock(tmp_path):
    from factor_builders.limit_behavior_builder import build_day, FACTOR_ID

    rows = [
        _limit_row("20240101", "000001.SZ", limit="U", up_stat="1/1"),
    ]
    df = pd.DataFrame(rows)
    _write_date_partition(tmp_path, "limit_list_d", 2024, df)

    out = build_day("2024-01-01", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    assert val == pytest.approx(0.0, rel=1e-9)


# ────────────────────────────────────────────────────────────────────────────
# 4. Trailing window correctly limits to 5 days
# ────────────────────────────────────────────────────────────────────────────
def test_limit_behavior_window_5d(tmp_path):
    from factor_builders.limit_behavior_builder import build_day, FACTOR_ID

    # 7 days, all limit-ups.  Window=5, so score = 5/5 = 1.0 (not 7/5)
    rows = [
        _limit_row("20240101", "600519.SH", limit="U", up_stat="1/1"),
        _limit_row("20240102", "600519.SH", limit="U", up_stat="1/1"),
        _limit_row("20240103", "600519.SH", limit="U", up_stat="1/1"),
        _limit_row("20240104", "600519.SH", limit="U", up_stat="1/1"),
        _limit_row("20240105", "600519.SH", limit="U", up_stat="1/1"),
        _limit_row("20240106", "600519.SH", limit="U", up_stat="1/1"),
        _limit_row("20240107", "600519.SH", limit="U", up_stat="1/1"),
    ]
    df = pd.DataFrame(rows)
    _write_date_partition(tmp_path, "limit_list_d", 2024, df)

    out = build_day("2024-01-07", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    # Only 5 days in window, all limit-ups -> 5/5 = 1.0
    assert val == pytest.approx(1.0, rel=1e-9)


# ────────────────────────────────────────────────────────────────────────────
# 5. Multiple stocks in one call
# ────────────────────────────────────────────────────────────────────────────
def test_limit_behavior_multiple_stocks(tmp_path):
    from factor_builders.limit_behavior_builder import build_day, FACTOR_ID

    rows = [
        _limit_row("20240101", "600519.SH", limit="U", up_stat="1/1"),
        _limit_row("20240102", "600519.SH", limit="U", up_stat="1/1"),
        _limit_row("20240101", "000001.SZ", limit="D", up_stat="1/1"),
        _limit_row("20240102", "000001.SZ", limit="Z", up_stat=None),
    ]
    df = pd.DataFrame(rows)
    _write_date_partition(tmp_path, "limit_list_d", 2024, df)

    out = build_day("2024-01-02", ["600519.SH", "000001.SZ"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert len(out) == 2
    vals = {r["ts_code"]: float(r[FACTOR_ID]) for _, r in out.iterrows()}
    # 600519.SH: 2 ups / 5 = 0.4
    assert vals["600519.SH"] == pytest.approx(0.4, rel=1e-9)
    # 000001.SZ: 1 down / 5 = 0.2
    assert vals["000001.SZ"] == pytest.approx(0.2, rel=1e-9)


# ────────────────────────────────────────────────────────────────────────────
# 6. Empty data_root -> empty output
# ────────────────────────────────────────────────────────────────────────────
def test_limit_behavior_empty_data_root(tmp_path):
    from factor_builders.limit_behavior_builder import build_day

    out = build_day("2024-01-01", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert out.empty
