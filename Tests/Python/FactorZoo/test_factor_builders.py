"""Phase 5 Task 1 — TDD tests for the 7 new factor builders.

Hermetic: each test writes synthetic tushare-style parquet fixtures under
tmp_path, calls build_day(date, [ts_code], data_root=tmp, result_root=tmp),
and asserts (a) the returned DataFrame is non-empty, (b) the per-ts_code
parquet exists at result/factor-zoo/<id>/<date>/<ts_code>.parquet, and
(c) the value is in a sane range.

Field names mirror the on-disk tushare_data_v2 parquet schema exactly
(verified 2026-07-25).
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


# ────────────────────────────────────────────────────────────────────────────
# Helpers: write tushare-style partitioned parquet under tmp_path
# ────────────────────────────────────────────────────────────────────────────
def _write_partition(tmp_path: Path, table: str, ts_code: str,
                     df: pd.DataFrame) -> Path:
    """Write {tmp}/{table}/ts_code={ts_code}/data.parquet."""
    d = tmp_path / table / f"ts_code={ts_code}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "data.parquet"
    df.to_parquet(p, index=False)
    return p


def _write_index_partition(tmp_path: Path, table: str, index_code: str,
                           df: pd.DataFrame) -> Path:
    """Write {tmp}/{table}/ts_code={index_code}/data.parquet (index_daily)."""
    return _write_partition(tmp_path, table, index_code, df)


# ────────────────────────────────────────────────────────────────────────────
# 1. accruals_sloan (annual PIT, min_periods=2)
# ────────────────────────────────────────────────────────────────────────────
def _bs_row(end_date, f_ann_date, total_assets, total_cur_assets, money_cap,
            total_cur_liab, st_borr=0.0, notes_payable=0.0,
            non_cur_liab_due_1y=0.0, comp_type="1"):
    return {
        "ts_code": "600519.SH", "ann_date": f_ann_date, "f_ann_date": f_ann_date,
        "end_date": end_date, "report_type": "1", "comp_type": comp_type,
        "end_type": "4",
        "total_assets": total_assets, "total_cur_assets": total_cur_assets,
        "money_cap": money_cap, "total_cur_liab": total_cur_liab,
        "st_borr": st_borr, "notes_payable": notes_payable,
        "non_cur_liab_due_1y": non_cur_liab_due_1y,
    }


def _cf_row(end_date, f_ann_date, depr_fa_coga_dpba, amort_intang_assets,
            lt_amort_deferred_exp):
    return {
        "ts_code": "600519.SH", "ann_date": f_ann_date, "f_ann_date": f_ann_date,
        "end_date": end_date, "report_type": "1", "comp_type": "1",
        "end_type": "4",
        "depr_fa_coga_dpba": depr_fa_coga_dpba,
        "amort_intang_assets": amort_intang_assets,
        "lt_amort_deferred_exp": lt_amort_deferred_exp,
    }


def test_accruals_sloan_basic(tmp_path):
    from factor_builders.accruals_sloan_builder import build_day, FACTOR_ID

    # Year t-1 (2022) and year t (2023), both disclosed before asof 2024-03-01.
    bs = pd.DataFrame([
        _bs_row("20221231", "20230425", 100.0, 50.0, 10.0, 30.0),
        _bs_row("20231231", "20240301", 120.0, 55.0, 12.0, 35.0),
    ])
    cf = pd.DataFrame([
        _cf_row("20221231", "20230425", 5.0, 1.0, 0.5),
        _cf_row("20231231", "20240301", 7.0, 1.5, 0.5),
    ])
    _write_partition(tmp_path, "balancesheet", "600519.SH", bs)
    _write_partition(tmp_path, "cashflow", "600519.SH", cf)

    out = build_day("2024-03-01", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    # Hand-computed:
    #   ncwc_t  = (55-12) - (35-0-0-0)       = 43 - 35 = 8
    #   ncwc_{t-1} = (50-10) - (30-0-0-0)    = 40 - 30 = 10
    #   d_ncwc  = -2
    #   d_cash  = 12-10 = 2
    #   depr_t  = 7+1.5+0.5 = 9 ; depr_{t-1} = 5+1+0.5 = 6.5 ; d_depr = 2.5
    #   accruals = (-2 - 2 - 2.5) / ((120+100)/2) = -6.5 / 110 = -0.0590909...
    assert val == pytest.approx(-6.5 / 110.0, rel=1e-9)
    # parquet path
    p = tmp_path / "factor-zoo" / FACTOR_ID / "2024-03-01" / "600519.SH.parquet"
    assert p.exists()


def test_accruals_sloan_pit_excludes_future_disclosed_row(tmp_path):
    """A row whose f_ann_date > asof must NOT be used (PIT boundary)."""
    from factor_builders.accruals_sloan_builder import build_day, FACTOR_ID

    # Three annual periods. The 2023 row has TWO disclosures:
    #   - f_ann_date=20240301 (<=asof, eligible)
    #   - f_ann_date=20240501 (>asof, FUTURE — must be excluded)
    # The future row has a different total_assets (999). If the builder leaks
    # it, the accruals value will differ from the value computed using only
    # the eligible rows.
    bs_eligible_only = pd.DataFrame([
        _bs_row("20221231", "20230425", 100.0, 50.0, 10.0, 30.0),
        _bs_row("20231231", "20240301", 120.0, 55.0, 12.0, 35.0),
    ])
    bs_with_future = pd.DataFrame([
        _bs_row("20221231", "20230425", 100.0, 50.0, 10.0, 30.0),
        _bs_row("20231231", "20240301", 120.0, 55.0, 12.0, 35.0),
        # Future-disclosed restatement of 2023 (f_ann_date > asof):
        _bs_row("20231231", "20240501", 999.0, 55.0, 12.0, 35.0),
    ])
    cf = pd.DataFrame([
        _cf_row("20221231", "20230425", 5.0, 1.0, 0.5),
        _cf_row("20231231", "20240301", 7.0, 1.5, 0.5),
    ])

    # asof = 2024-04-01 (after the 0301 disclosure, before the 0501 one).
    _write_partition(tmp_path, "balancesheet", "600519.SH", bs_with_future)
    _write_partition(tmp_path, "cashflow", "600519.SH", cf)
    out_future = build_day("2024-04-01", ["600519.SH"],
                           data_root=str(tmp_path), result_root=str(tmp_path))
    val_with_future_present = float(out_future.iloc[0][FACTOR_ID])

    # Recompute with ONLY eligible rows.
    import shutil
    tmp2 = tmp_path / "eligible_only"
    tmp2.mkdir()
    _write_partition(tmp2, "balancesheet", "600519.SH", bs_eligible_only)
    _write_partition(tmp2, "cashflow", "600519.SH", cf)
    out_eligible = build_day("2024-04-01", ["600519.SH"],
                             data_root=str(tmp2), result_root=str(tmp2))
    val_eligible_only = float(out_eligible.iloc[0][FACTOR_ID])

    assert val_with_future_present == pytest.approx(val_eligible_only, rel=1e-9)
    # And specifically NOT equal to what 999 would produce:
    expected_with_leak = (-2 - 2 - 2.5) / ((999.0 + 100.0) / 2.0)
    assert val_with_future_present != pytest.approx(expected_with_leak, rel=1e-6)


def test_accruals_sloan_insufficient_history(tmp_path):
    """Only 1 annual period (< min_periods=2) -> skip (empty output)."""
    from factor_builders.accruals_sloan_builder import build_day

    bs = pd.DataFrame([_bs_row("20231231", "20240301", 120.0, 55.0, 12.0, 35.0)])
    cf = pd.DataFrame([_cf_row("20231231", "20240301", 7.0, 1.5, 0.5)])
    _write_partition(tmp_path, "balancesheet", "600519.SH", bs)
    _write_partition(tmp_path, "cashflow", "600519.SH", cf)
    out = build_day("2024-03-01", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert out.empty


# ────────────────────────────────────────────────────────────────────────────
# 2. gross_profitability (annual PIT, min_periods=1)
# ────────────────────────────────────────────────────────────────────────────
def test_gross_profitability_basic(tmp_path):
    from factor_builders.gross_profitability_builder import build_day, FACTOR_ID

    # income and balancesheet must share the SAME latest annual end_date.
    inc = pd.DataFrame([{
        "ts_code": "000001.SZ", "ann_date": "20240301", "f_ann_date": "20240301",
        "end_date": "20231231", "report_type": "1", "comp_type": "1",
        "end_type": "4", "revenue": 200.0, "oper_cost": 120.0,
    }])
    bs = pd.DataFrame([{
        "ts_code": "000001.SZ", "ann_date": "20240301", "f_ann_date": "20240301",
        "end_date": "20231231", "report_type": "1", "comp_type": "1",
        "end_type": "4", "total_assets": 800.0,
    }])
    _write_partition(tmp_path, "income", "000001.SZ", inc)
    _write_partition(tmp_path, "balancesheet", "000001.SZ", bs)

    out = build_day("2024-03-15", ["000001.SZ"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    # GP = (200-120)/800 = 0.1
    assert val == pytest.approx(0.1, rel=1e-9)
    p = tmp_path / "factor-zoo" / FACTOR_ID / "2024-03-15" / "000001.SZ.parquet"
    assert p.exists()


def test_gross_profitability_pit_boundary(tmp_path):
    """Future-disclosed income row must NOT be used."""
    from factor_builders.gross_profitability_builder import build_day, FACTOR_ID

    inc = pd.DataFrame([
        {"ts_code": "000001.SZ", "ann_date": "20240301", "f_ann_date": "20240301",
         "end_date": "20231231", "report_type": "1", "comp_type": "1",
         "end_type": "4", "revenue": 200.0, "oper_cost": 120.0},
        # Future-disclosed restatement (f_ann_date > asof):
        {"ts_code": "000001.SZ", "ann_date": "20240501", "f_ann_date": "20240501",
         "end_date": "20231231", "report_type": "1", "comp_type": "1",
         "end_type": "4", "revenue": 999.0, "oper_cost": 1.0},
    ])
    bs = pd.DataFrame([{
        "ts_code": "000001.SZ", "ann_date": "20240301", "f_ann_date": "20240301",
        "end_date": "20231231", "report_type": "1", "comp_type": "1",
        "end_type": "4", "total_assets": 800.0,
    }])
    _write_partition(tmp_path, "income", "000001.SZ", inc)
    _write_partition(tmp_path, "balancesheet", "000001.SZ", bs)
    # asof between the two disclosures
    out = build_day("2024-04-01", ["000001.SZ"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    val = float(out.iloc[0][FACTOR_ID])
    assert val == pytest.approx(0.1, rel=1e-9)  # uses 200/120, not 999/1


# ────────────────────────────────────────────────────────────────────────────
# 3. asset_growth (annual PIT, min_periods=2, bank-excluded)
# ────────────────────────────────────────────────────────────────────────────
def test_asset_growth_basic(tmp_path):
    from factor_builders.asset_growth_builder import build_day, FACTOR_ID

    bs = pd.DataFrame([
        _bs_row("20221231", "20230425", 100.0, 50.0, 10.0, 30.0),
        _bs_row("20231231", "20240301", 120.0, 55.0, 12.0, 35.0),
    ])
    _write_partition(tmp_path, "balancesheet", "600519.SH", bs)
    out = build_day("2024-03-01", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    # AG = (120-100)/100 = 0.2
    assert val == pytest.approx(0.2, rel=1e-9)
    p = tmp_path / "factor-zoo" / FACTOR_ID / "2024-03-01" / "600519.SH.parquet"
    assert p.exists()


def test_asset_growth_excludes_banks(tmp_path):
    """comp_type in {2,4,5} -> bank -> skip."""
    from factor_builders.asset_growth_builder import build_day
    bs = pd.DataFrame([
        _bs_row("20221231", "20230425", 100.0, 50.0, 10.0, 30.0, comp_type="2"),
        _bs_row("20231231", "20240301", 120.0, 55.0, 12.0, 35.0, comp_type="2"),
    ])
    _write_partition(tmp_path, "balancesheet", "600036.SH", bs)
    out = build_day("2024-03-01", ["600036.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert out.empty


# ────────────────────────────────────────────────────────────────────────────
# 4. roe_change (annual PIT, min_periods=2)
# ────────────────────────────────────────────────────────────────────────────
def test_roe_change_basic(tmp_path):
    from factor_builders.roe_change_builder import build_day, FACTOR_ID

    fi = pd.DataFrame([
        {"ts_code": "600519.SH", "ann_date": "20230425", "end_date": "20221231",
         "roe": 20.0, "roe_yoy": 5.0},
        {"ts_code": "600519.SH", "ann_date": "20240301", "end_date": "20231231",
         "roe": 25.0, "roe_yoy": 25.0},
    ])
    _write_partition(tmp_path, "fina_indicator", "600519.SH", fi)
    out = build_day("2024-03-15", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    # dROE = 25 - 20 = 5
    assert val == pytest.approx(5.0, rel=1e-9)
    p = tmp_path / "factor-zoo" / FACTOR_ID / "2024-03-15" / "600519.SH.parquet"
    assert p.exists()


def test_roe_change_pit_boundary(tmp_path):
    from factor_builders.roe_change_builder import build_day, FACTOR_ID
    fi = pd.DataFrame([
        {"ts_code": "600519.SH", "ann_date": "20230425", "end_date": "20221231",
         "roe": 20.0, "roe_yoy": 5.0},
        {"ts_code": "600519.SH", "ann_date": "20240301", "end_date": "20231231",
         "roe": 25.0, "roe_yoy": 25.0},
        # Future-disclosed restatement (ann_date > asof):
        {"ts_code": "600519.SH", "ann_date": "20240501", "end_date": "20231231",
         "roe": 999.0, "roe_yoy": 0.0},
    ])
    _write_partition(tmp_path, "fina_indicator", "600519.SH", fi)
    out = build_day("2024-04-01", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    val = float(out.iloc[0][FACTOR_ID])
    assert val == pytest.approx(5.0, rel=1e-9)


# ────────────────────────────────────────────────────────────────────────────
# 5. ivol_20d (daily, market-model residual vol)
# ────────────────────────────────────────────────────────────────────────────
def _daily_row(trade_date, close, pct_chg, vol=1000.0, pre_close=10.0):
    return {
        "ts_code": "600519.SH", "trade_date": trade_date,
        "close": close, "pct_chg": pct_chg, "vol": vol, "pre_close": pre_close,
    }


def _adj_row(trade_date, adj_factor):
    return {"ts_code": "600519.SH", "trade_date": trade_date, "adj_factor": adj_factor}


def _idx_row(trade_date, pct_chg, close=10.0):
    return {"ts_code": "000300.SH", "trade_date": trade_date,
            "pct_chg": pct_chg, "close": close}


def test_ivol_20d_basic(tmp_path):
    from factor_builders.ivol_20d_builder import build_day, FACTOR_ID

    # Build 25 daily rows + matching adj_factor + index_daily.
    dates = [f"202401{d:02d}" for d in range(1, 26)]
    # Stock returns: pct_chg/100. Make them mostly market + small noise.
    import random
    random.seed(42)
    daily_rows = []
    idx_rows = []
    adj_rows = []
    close = 10.0
    for d in dates:
        mkt = 0.001  # +0.1% market
        noise = random.gauss(0, 0.002)
        ri = (mkt + noise) * 100.0  # pct_chg
        close = close * (1 + ri / 100.0)
        daily_rows.append(_daily_row(d, close, ri))
        idx_rows.append(_idx_row(d, mkt * 100.0))
        adj_rows.append(_adj_row(d, 1.0))

    _write_partition(tmp_path, "daily", "600519.SH", pd.DataFrame(daily_rows))
    _write_partition(tmp_path, "adj_factor", "600519.SH", pd.DataFrame(adj_rows))
    _write_index_partition(tmp_path, "index_daily", "000300.SH",
                           pd.DataFrame(idx_rows))

    asof = "2024-01-25"
    out = build_day(asof, ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    # Idiosyncratic vol is positive (residual std * sqrt(252) * df correction).
    assert val > 0.0
    # Sanity: noise sigma was 0.002, so annualized ~ 0.002*sqrt(252) ≈ 0.0317.
    # Allow wide range because of finite-sample + df correction.
    assert 0.005 < val < 0.5
    p = tmp_path / "factor-zoo" / FACTOR_ID / asof / "600519.SH.parquet"
    assert p.exists()


def test_ivol_20d_insufficient_history(tmp_path):
    """< 20 overlapping days -> skip."""
    from factor_builders.ivol_20d_builder import build_day
    dates = [f"202401{d:02d}" for d in range(1, 11)]  # only 10 days
    daily_rows = [_daily_row(d, 10.0, 0.1) for d in dates]
    idx_rows = [_idx_row(d, 0.1) for d in dates]
    adj_rows = [_adj_row(d, 1.0) for d in dates]
    _write_partition(tmp_path, "daily", "600519.SH", pd.DataFrame(daily_rows))
    _write_partition(tmp_path, "adj_factor", "600519.SH", pd.DataFrame(adj_rows))
    _write_index_partition(tmp_path, "index_daily", "000300.SH",
                           pd.DataFrame(idx_rows))
    out = build_day("2024-01-10", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert out.empty


# ────────────────────────────────────────────────────────────────────────────
# 6. max_ret_20d (daily, NEGATED max return)
# ────────────────────────────────────────────────────────────────────────────
def test_max_ret_20d_basic(tmp_path):
    from factor_builders.max_ret_20d_builder import build_day, FACTOR_ID

    dates = [f"202401{d:02d}" for d in range(1, 26)]  # 25 days
    # Plant a known max return of 5.0% on the last day.
    pct = [0.1] * 24 + [5.0]
    daily_rows = [_daily_row(d, 10.0, p) for d, p in zip(dates, pct)]
    _write_partition(tmp_path, "daily", "600519.SH", pd.DataFrame(daily_rows))

    asof = "2024-01-25"
    out = build_day(asof, ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    # MAX = 5/100 = 0.05; negated -> -0.05
    assert val == pytest.approx(-0.05, rel=1e-9)
    p = tmp_path / "factor-zoo" / FACTOR_ID / asof / "600519.SH.parquet"
    assert p.exists()


def test_max_ret_20d_insufficient_history(tmp_path):
    from factor_builders.max_ret_20d_builder import build_day
    dates = [f"202401{d:02d}" for d in range(1, 11)]  # 10 days
    daily_rows = [_daily_row(d, 10.0, 0.1) for d in dates]
    _write_partition(tmp_path, "daily", "600519.SH", pd.DataFrame(daily_rows))
    out = build_day("2024-01-10", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert out.empty


# ────────────────────────────────────────────────────────────────────────────
# 7. short_term_reversal (daily, adj-factor based)
# ────────────────────────────────────────────────────────────────────────────
def test_short_term_reversal_basic(tmp_path):
    from factor_builders.short_term_reversal_builder import build_day, FACTOR_ID

    # 22 daily rows. adj_close grows by 1% each day (close*adj).
    # close_t / close_{t-20} - 1 = 1.01^20 - 1 ≈ 0.2202 ; REV20 = -0.2202
    dates = [f"202401{d:02d}" for d in range(1, 23)]  # 22 days
    daily_rows = []
    adj_rows = []
    close = 10.0
    for i, d in enumerate(dates):
        close = 10.0 * (1.01 ** i)
        daily_rows.append(_daily_row(d, close, 1.0))
        adj_rows.append(_adj_row(d, 1.0))
    _write_partition(tmp_path, "daily", "600519.SH", pd.DataFrame(daily_rows))
    _write_partition(tmp_path, "adj_factor", "600519.SH", pd.DataFrame(adj_rows))

    asof = "2024-01-22"
    out = build_day(asof, ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert not out.empty
    val = float(out.iloc[0][FACTOR_ID])
    expected = -(close / (10.0 * (1.01 ** (22 - 1 - 20))) - 1.0)
    # Use the last 21 rows: close at index -1 vs close at index -21 (t-20).
    # With 22 rows, t = index 21, t-20 = index 1.
    close_t = 10.0 * (1.01 ** 21)
    close_t_minus_20 = 10.0 * (1.01 ** 1)
    expected = -(close_t / close_t_minus_20 - 1.0)
    assert val == pytest.approx(expected, rel=1e-9)
    p = tmp_path / "factor-zoo" / FACTOR_ID / asof / "600519.SH.parquet"
    assert p.exists()


def test_short_term_reversal_insufficient_history(tmp_path):
    from factor_builders.short_term_reversal_builder import build_day
    dates = [f"202401{d:02d}" for d in range(1, 21)]  # 20 days, need >=21
    daily_rows = [_daily_row(d, 10.0, 0.1) for d in dates]
    adj_rows = [_adj_row(d, 1.0) for d in dates]
    _write_partition(tmp_path, "daily", "600519.SH", pd.DataFrame(daily_rows))
    _write_partition(tmp_path, "adj_factor", "600519.SH", pd.DataFrame(adj_rows))
    out = build_day("2024-01-20", ["600519.SH"],
                    data_root=str(tmp_path), result_root=str(tmp_path))
    assert out.empty
