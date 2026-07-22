"""Crowding factor: registration + 3-axis composite computation.

Spec: docs/superpowers/specs/2026-07-22-crowding-factor-design.md
Plan: docs/superpowers/plans/2026-07-22-crowding-factor.md (Task 1)

Tests:
  1. test_crowding_factor_already_registered  — FactorRegistry.cs:42 已注册 CrowdingFactor
  2. test_composite_crowding_three_axes       — CrowdingFactors.composite_crowding high>low, 0..1
  3. test_builder_join_five_tables_and_composite — 5-table join + composite parquet
  4. test_builder_cyq_missing_degrades_to_two_axes — cyq 缺失 → degraded=true
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
# data-source/tushare is not an importable package (no __init__.py) — mirror
# the barra_cne5v2_factor_bridge.py pattern: insert dir onto sys.path, then
# `import crowding_factor_builder as b`.
_TS_DIR = ROOT / "data-source" / "tushare"
_PY_ALGO_DIR = ROOT / "Algorithm.Python"
for _p in [str(ROOT), str(_TS_DIR), str(_PY_ALGO_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ────────────────────────────────────────────────────────────────────────────
# Test 1: FactorRegistry.cs:42 already registers CrowdingFactor
# ────────────────────────────────────────────────────────────────────────────
def test_crowding_factor_already_registered():
    """spec §1.1 修正: CrowdingFactor 已在 FactorRegistry.cs:42 注册 (Task 1 验证, 不改)."""
    src = (ROOT / "Common/Factors/Core/FactorRegistry.cs").read_text()
    assert "Register(new QuantConnect.Factors.Sentiment.CrowdingFactor())" in src


# ────────────────────────────────────────────────────────────────────────────
# Test 2: composite_crowding high > low, bounded in [0, 1]
# ────────────────────────────────────────────────────────────────────────────
def test_composite_crowding_three_axes():
    """composite = trading*0.35 + fund*0.35 + chip*0.30 (CrowdingFactors.py:180)."""
    from CrowdingFactors import CrowdingFactors, DEFAULT_PARAMS

    high = {
        "davol20": 0.4, "volume_ratio": 2.0,
        "net_mf_ratio": 0.3, "margin_growth": 0.5, "hk_hold_ratio": 10.0,
        "cost_5pct_adj": 9.0, "cost_95pct_adj": 11.0, "weight_avg_adj": 10.0,
        "winner_rate": 90.0,
    }
    low = {
        "davol20": 0.0, "volume_ratio": 1.0,
        "net_mf_ratio": 0.0, "margin_growth": 0.0, "hk_hold_ratio": 0.0,
        "cost_5pct_adj": 5.0, "cost_95pct_adj": 15.0, "weight_avg_adj": 10.0,
        "winner_rate": 10.0,
    }
    h = CrowdingFactors.composite_crowding(high, DEFAULT_PARAMS)
    l = CrowdingFactors.composite_crowding(low, DEFAULT_PARAMS)
    assert 0.0 <= l < h <= 1.0, f"expected 0 <= low({l}) < high({h}) <= 1"


# ────────────────────────────────────────────────────────────────────────────
# Test 3: builder joins 5 tushare tables → composite parquet
# ────────────────────────────────────────────────────────────────────────────
def _make_synthetic_tables():
    """Build 5 small per-ts_code DataFrames that match real parquet schemas."""
    ts_codes = ["600519.SH", "000001.SZ"]
    trade_date = "20240105"

    rows = []
    for tc in ts_codes:
        # daily_basic: contains davol20? NO — daily_basic has turnover_rate/volume_ratio.
        # davol20 is derived; builder computes davol20 = turnover_rate/ma(turnover_rate,120)-1.
        # For this test we put a turnover_rate that yields davol20>0 for the high ts_code.
        is_high = tc == "600519.SH"
        rows.append({
            "ts_code": tc, "trade_date": trade_date,
            "turnover_rate": 0.08 if is_high else 0.02,
            "volume_ratio": 2.0 if is_high else 1.0,
            "close": 1500.0 if is_high else 10.0,
            "circ_mv": 1_000_000.0, "total_mv": 2_000_000.0,
        })
    daily_basic = pd.DataFrame(rows)

    # moneyflow → net_mf_ratio = net_mf_amount / amount (proxy via circ_mv if no amount col)
    mf_rows = []
    for tc in ts_codes:
        is_high = tc == "600519.SH"
        mf_rows.append({
            "ts_code": tc, "trade_date": trade_date,
            "net_mf_amount": 3000.0 if is_high else 0.0,
            "buy_lg_amount": 100.0, "sell_lg_amount": 50.0,
            "buy_elg_amount": 80.0, "sell_elg_amount": 40.0,
        })
    moneyflow = pd.DataFrame(mf_rows)

    # margin_detail: rzye series → margin_growth = log-change over 5d
    margin_rows = []
    for tc in ts_codes:
        is_high = tc == "600519.SH"
        base = 1_000_000.0
        for i in range(6):
            # high stock grows 10% per day, low flat
            rzye = base * (1.10 ** i) if is_high else base
            margin_rows.append({
                "ts_code": tc,
                "trade_date": f"2024010{i}",  # 20240100..05 → real dates not needed; builder tail-sorts
                "rzye": rzye, "rqye": 0.0, "rzmre": 0.0,
            })
    margin = pd.DataFrame(margin_rows)

    # hsgt_top10: hk_hold_ratio = ratio (vol / total_share * 100)
    hsgt_rows = []
    for tc in ts_codes:
        is_high = tc == "600519.SH"
        hsgt_rows.append({
            "trade_date": trade_date, "ts_code": tc, "name": "x",
            "vol": 5_000_000 if is_high else 0,
            "ratio": 10.0 if is_high else 0.0,
            "amount": 0.0, "net_amount": 0.0,
        })
    hsgt_top10 = pd.DataFrame(hsgt_rows)

    # cyq_perf: cost_5pct/95pct/weight_avg/winner_rate
    cyq_rows = []
    for tc in ts_codes:
        is_high = tc == "600519.SH"
        cyq_rows.append({
            "ts_code": tc, "trade_date": trade_date,
            "cost_5pct": 9.0 if is_high else 5.0,
            "cost_15pct": 9.5 if is_high else 7.0,
            "cost_50pct": 10.0,
            "cost_85pct": 10.5 if is_high else 13.0,
            "cost_95pct": 11.0 if is_high else 15.0,
            "weight_avg": 10.0,
            "winner_rate": 90.0 if is_high else 10.0,
        })
    cyq_perf = pd.DataFrame(cyq_rows)

    # adj_factor: 1.0 (no adjustment needed for synthetic test)
    adj_rows = []
    for tc in ts_codes:
        adj_rows.append({
            "ts_code": tc, "trade_date": trade_date, "adj_factor": 1.0,
        })
    adj_factor = pd.DataFrame(adj_rows)

    return {
        "daily_basic": daily_basic,
        "moneyflow": moneyflow,
        "margin_detail": margin,
        "hsgt_top10": hsgt_top10,
        "cyq_perf": cyq_perf,
        "adj_factor": adj_factor,
    }


def test_builder_join_five_tables_and_composite(tmp_path, monkeypatch):
    """Builder joins 5 tables per ts_code, applies adj_factor, writes parquet."""
    import crowding_factor_builder as b

    synth = _make_synthetic_tables()
    # Monkeypatch module-level table-read functions to return synthetic frames
    # filtered by ts_code. Builder's join_five_tables uses these.
    def _make_reader(frame):
        def _reader(data_root, ts_code, trade_date):
            sub = frame[frame["ts_code"] == ts_code] if "ts_code" in frame.columns else frame
            if "trade_date" in sub.columns:
                sub = sub[sub["trade_date"].astype(str) == str(trade_date)]
            return sub.copy()
        return _reader
    def _make_margin_reader(frame):
        # margin_detail needs the full history (builder tail-sorts for log-change)
        def _reader(data_root, ts_code, trade_date):
            sub = frame[frame["ts_code"] == ts_code].copy()
            return sub
        return _reader
    def _make_hsgt_reader(frame):
        # hsgt_top10 stored per year; builder forward-fills if stale
        def _reader(data_root, ts_code, trade_date):
            sub = frame[frame["ts_code"] == ts_code].copy() if "ts_code" in frame.columns else frame.copy()
            return sub
        return _reader
    def _make_adj_reader(frame):
        # Mirror real read_adj_factor forward-fill semantics so the builder's
        # adj_factor lookup behaves the same as production.
        def _reader(data_root, ts_code, trade_date):
            sub = frame[frame["ts_code"] == ts_code] if "ts_code" in frame.columns else frame
            if "trade_date" not in sub.columns:
                return sub.copy()
            target = str(trade_date)
            exact = sub[sub["trade_date"].astype(str) == target]
            if not exact.empty:
                return exact.tail(1).copy()
            prior = sub[sub["trade_date"].astype(str) <= target]
            return prior.sort_values("trade_date").tail(1).copy() if not prior.empty else pd.DataFrame()
        return _reader

    monkeypatch.setattr(b, "read_daily_basic", _make_reader(synth["daily_basic"]))
    monkeypatch.setattr(b, "read_moneyflow", _make_reader(synth["moneyflow"]))
    monkeypatch.setattr(b, "read_margin_detail", _make_margin_reader(synth["margin_detail"]))
    monkeypatch.setattr(b, "read_hsgt_top10", _make_hsgt_reader(synth["hsgt_top10"]))
    monkeypatch.setattr(b, "read_cyq_perf", _make_reader(synth["cyq_perf"]))
    monkeypatch.setattr(b, "read_adj_factor", _make_adj_reader(synth["adj_factor"]))
    # Disable InfluxDB writes during test
    monkeypatch.setattr(b, "write_influx", lambda lines, **kw: 0)
    # Redirect result root into tmp_path
    monkeypatch.setattr(b, "DEFAULT_RESULT_ROOT", str(tmp_path))

    out = b.build_day("2024-01-05", ["600519.SH", "000001.SZ"])
    assert isinstance(out, pd.DataFrame)
    expected_cols = {"ts_code", "composite", "trading", "fund", "chip", "degraded", "hk_hold_stale"}
    assert expected_cols.issubset(set(out.columns)), (
        f"missing columns: {expected_cols - set(out.columns)}; got {list(out.columns)}"
    )
    assert len(out) == 2

    high_row = out[out["ts_code"] == "600519.SH"].iloc[0]
    low_row = out[out["ts_code"] == "000001.SZ"].iloc[0]
    assert high_row["composite"] > low_row["composite"]
    assert high_row["degraded"] is False or high_row["degraded"] == False
    assert high_row["hk_hold_stale"] is False or high_row["hk_hold_stale"] == False

    # Parquet file written at result/crowding-factor/<YYYY-MM-DD>/<ts_code>.parquet
    parquet_path = tmp_path / "crowding-factor" / "2024-01-05" / "600519.SH.parquet"
    assert parquet_path.exists(), f"parquet not written at {parquet_path}"
    df = pd.read_parquet(parquet_path)
    assert "composite" in df.columns
    assert "ts_code" in df.columns


# ────────────────────────────────────────────────────────────────────────────
# Test 4: cyq_perf missing for a ts_code → degraded=true, 2-axis (0.5/0.5)
# ────────────────────────────────────────────────────────────────────────────
def test_builder_cyq_missing_degrades_to_two_axes(tmp_path, monkeypatch):
    """cyq_perf missing for ts_code → degrade to trading+fund (0.5/0.5), degraded=true."""
    import crowding_factor_builder as b

    synth = _make_synthetic_tables()
    # Remove cyq_perf entry for 000001.SZ to simulate missing
    cyq_no_low = synth["cyq_perf"][synth["cyq_perf"]["ts_code"] != "000001.SZ"]
    synth["cyq_perf"] = cyq_no_low

    def _make_reader(frame):
        def _reader(data_root, ts_code, trade_date):
            sub = frame[frame["ts_code"] == ts_code] if "ts_code" in frame.columns else frame
            if "trade_date" in sub.columns:
                sub = sub[sub["trade_date"].astype(str) == str(trade_date)]
            return sub.copy()
        return _reader
    def _make_margin_reader(frame):
        def _reader(data_root, ts_code, trade_date):
            return frame[frame["ts_code"] == ts_code].copy()
        return _reader
    def _make_hsgt_reader(frame):
        def _reader(data_root, ts_code, trade_date):
            return frame[frame["ts_code"] == ts_code].copy() if "ts_code" in frame.columns else frame.copy()
        return _reader
    def _make_adj_reader(frame):
        def _reader(data_root, ts_code, trade_date):
            sub = frame[frame["ts_code"] == ts_code] if "ts_code" in frame.columns else frame
            if "trade_date" not in sub.columns:
                return sub.copy()
            target = str(trade_date)
            exact = sub[sub["trade_date"].astype(str) == target]
            if not exact.empty:
                return exact.tail(1).copy()
            prior = sub[sub["trade_date"].astype(str) <= target]
            return prior.sort_values("trade_date").tail(1).copy() if not prior.empty else pd.DataFrame()
        return _reader

    monkeypatch.setattr(b, "read_daily_basic", _make_reader(synth["daily_basic"]))
    monkeypatch.setattr(b, "read_moneyflow", _make_reader(synth["moneyflow"]))
    monkeypatch.setattr(b, "read_margin_detail", _make_margin_reader(synth["margin_detail"]))
    monkeypatch.setattr(b, "read_hsgt_top10", _make_hsgt_reader(synth["hsgt_top10"]))
    monkeypatch.setattr(b, "read_cyq_perf", _make_reader(synth["cyq_perf"]))
    monkeypatch.setattr(b, "read_adj_factor", _make_adj_reader(synth["adj_factor"]))
    monkeypatch.setattr(b, "write_influx", lambda lines, **kw: 0)
    monkeypatch.setattr(b, "DEFAULT_RESULT_ROOT", str(tmp_path))

    out = b.build_day("2024-01-05", ["600519.SH", "000001.SZ"])
    # 000001.SZ has no cyq_perf → degraded=true
    low_row = out[out["ts_code"] == "000001.SZ"].iloc[0]
    assert bool(low_row["degraded"]) is True, f"expected degraded=True for missing cyq, got {low_row['degraded']}"
    # chip should be NaN/None for degraded row
    assert pd.isna(low_row["chip"]) or low_row["chip"] is None
    # 600519.SH still has all 3 axes
    high_row = out[out["ts_code"] == "600519.SH"].iloc[0]
    assert bool(high_row["degraded"]) is False


# ────────────────────────────────────────────────────────────────────────────
# Test 5: adj_factor forward-fills across date gaps (mirror ChipDataLoader)
# ────────────────────────────────────────────────────────────────────────────
def test_adj_factor_forward_fills_across_date_gap(tmp_path, monkeypatch):
    """read_adj_factor returns latest row with trade_date <= target (not exact match).

    Synthetic adj_factor has a date GAP: rows for 2024-01-01 (=1.0) and
    2024-01-10 (=0.5). Querying 2024-01-05 (between the two) must return
    factor 1.0 (prior), not 1.0 default or 0.5. Querying 2024-01-12 (after
    the gap) must return 0.5. Asserts `cost_5pct_adj == cost_5pct * <prior>`.
    """
    import crowding_factor_builder as b

    ts_code = "600519.SH"
    # Two adj_factor rows with a gap (no row on the query date 2024-01-05)
    adj_factor = pd.DataFrame([
        {"ts_code": ts_code, "trade_date": "20240101", "adj_factor": 1.0},
        {"ts_code": ts_code, "trade_date": "20240110", "adj_factor": 0.5},
    ])

    # cyq row on the query date with known cost_5pct so we can verify the
    # applied factor = prior factor (1.0 for 2024-01-05, 0.5 for 2024-01-12)
    cyq_20240105 = pd.DataFrame([{
        "ts_code": ts_code, "trade_date": "20240105",
        "cost_5pct": 10.0, "cost_15pct": 10.5, "cost_50pct": 11.0,
        "cost_85pct": 11.5, "cost_95pct": 12.0,
        "weight_avg": 11.0, "winner_rate": 80.0,
    }])
    cyq_20240112 = pd.DataFrame([{
        "ts_code": ts_code, "trade_date": "20240112",
        "cost_5pct": 10.0, "cost_15pct": 10.5, "cost_50pct": 11.0,
        "cost_85pct": 11.5, "cost_95pct": 12.0,
        "weight_avg": 11.0, "winner_rate": 80.0,
    }])

    daily_basic = pd.DataFrame([{
        "ts_code": ts_code, "trade_date": "20240105",
        "turnover_rate": 0.05, "volume_ratio": 1.5, "close": 100.0,
        "circ_mv": 1_000_000.0, "total_mv": 2_000_000.0,
    }])
    daily_basic_12 = pd.DataFrame([{
        "ts_code": ts_code, "trade_date": "20240112",
        "turnover_rate": 0.05, "volume_ratio": 1.5, "close": 100.0,
        "circ_mv": 1_000_000.0, "total_mv": 2_000_000.0,
    }])
    moneyflow = pd.DataFrame([{
        "ts_code": ts_code, "trade_date": "20240105",
        "net_mf_amount": 1000.0, "buy_lg_amount": 0.0, "sell_lg_amount": 0.0,
        "buy_elg_amount": 0.0, "sell_elg_amount": 0.0,
    }])
    moneyflow_12 = pd.DataFrame([{
        "ts_code": ts_code, "trade_date": "20240112",
        "net_mf_amount": 1000.0, "buy_lg_amount": 0.0, "sell_lg_amount": 0.0,
        "buy_elg_amount": 0.0, "sell_elg_amount": 0.0,
    }])
    margin = pd.DataFrame([{
        "ts_code": ts_code, "trade_date": "20240105",
        "rzye": 1_000_000.0, "rqye": 0.0, "rzmre": 0.0,
    }])
    margin_12 = pd.DataFrame([{
        "ts_code": ts_code, "trade_date": "20240112",
        "rzye": 1_000_000.0, "rqye": 0.0, "rzmre": 0.0,
    }])
    hsgt = pd.DataFrame([{
        "trade_date": "20240105", "ts_code": ts_code, "name": "x",
        "vol": 0, "ratio": 5.0, "amount": 0.0, "net_amount": 0.0,
    }])
    hsgt_12 = pd.DataFrame([{
        "trade_date": "20240112", "ts_code": ts_code, "name": "x",
        "vol": 0, "ratio": 5.0, "amount": 0.0, "net_amount": 0.0,
    }])

    # First query: 2024-01-05 sits in the gap; expect adj_factor=1.0 (prior)
    monkeypatch.setattr(b, "read_daily_basic", lambda dr, tc, td: daily_basic.copy())
    monkeypatch.setattr(b, "read_moneyflow", lambda dr, tc, td: moneyflow.copy())
    monkeypatch.setattr(b, "read_margin_detail", lambda dr, tc, td: margin.copy())
    monkeypatch.setattr(b, "read_hsgt_top10", lambda dr, tc, td: hsgt.copy())
    monkeypatch.setattr(b, "read_cyq_perf", lambda dr, tc, td: cyq_20240105.copy())
    monkeypatch.setattr(b, "read_adj_factor", b.read_adj_factor)  # real forward-fill impl
    monkeypatch.setattr(b, "write_influx", lambda lines, **kw: 0)
    monkeypatch.setattr(b, "DEFAULT_RESULT_ROOT", str(tmp_path))
    monkeypatch.setattr(b, "DEFAULT_TS_PATH", str(tmp_path))

    # Write the synthetic adj_factor parquet so the real read_adj_factor can find it
    adj_dir = tmp_path / "adj_factor" / f"ts_code={ts_code}"
    adj_dir.mkdir(parents=True, exist_ok=True)
    adj_factor.to_parquet(adj_dir / "data.parquet", index=False)

    out_5 = b.build_day("2024-01-05", [ts_code])
    assert len(out_5) == 1
    row_5 = out_5.iloc[0]
    # cost_5pct=10.0 * adj_factor=1.0 → cost_5pct_adj=10.0 (NOT 1.0 default)
    # The builder does not emit raw cost_5pct_adj in the parquet, so verify
    # indirectly: chip axis is non-NaN and composite computed using factor 1.0.
    # To make the assertion direct, we re-derive via the row builder.
    crowding_row = b._build_crowding_row(str(tmp_path), ts_code, "20240105")
    assert crowding_row is not None
    assert crowding_row["cost_5pct_adj"] == pytest.approx(10.0 * 1.0), (
        f"2024-01-05 (gap): expected cost_5pct_adj=10.0 (factor=1.0 prior), "
        f"got {crowding_row['cost_5pct_adj']}"
    )
    assert crowding_row["cost_95pct_adj"] == pytest.approx(12.0 * 1.0)
    assert crowding_row["weight_avg_adj"] == pytest.approx(11.0 * 1.0)

    # Second query: 2024-01-12 is after the second adj row; expect factor=0.5
    monkeypatch.setattr(b, "read_daily_basic", lambda dr, tc, td: daily_basic_12.copy())
    monkeypatch.setattr(b, "read_moneyflow", lambda dr, tc, td: moneyflow_12.copy())
    monkeypatch.setattr(b, "read_margin_detail", lambda dr, tc, td: margin_12.copy())
    monkeypatch.setattr(b, "read_hsgt_top10", lambda dr, tc, td: hsgt_12.copy())
    monkeypatch.setattr(b, "read_cyq_perf", lambda dr, tc, td: cyq_20240112.copy())

    out_12 = b.build_day("2024-01-12", [ts_code])
    assert len(out_12) == 1
    crowding_row_12 = b._build_crowding_row(str(tmp_path), ts_code, "20240112")
    assert crowding_row_12 is not None
    assert crowding_row_12["cost_5pct_adj"] == pytest.approx(10.0 * 0.5), (
        f"2024-01-12 (after gap): expected cost_5pct_adj=5.0 (factor=0.5), "
        f"got {crowding_row_12['cost_5pct_adj']}"
    )
    assert crowding_row_12["cost_95pct_adj"] == pytest.approx(12.0 * 0.5)
    assert crowding_row_12["weight_avg_adj"] == pytest.approx(11.0 * 0.5)


# ────────────────────────────────────────────────────────────────────────────
# Task 2: cyq_perf enabled in registry + active in incremental_update download set
# ────────────────────────────────────────────────────────────────────────────
def test_cyq_perf_enabled_in_registry():
    """Plan Task 2: api_registry.py cyq_perf enabled=False → True (STOCK chunk kept)."""
    # data-source/tushare is not a package; mirror crowding_factor_builder's
    # sys.path-injection pattern to import api_registry.
    import api_registry
    from api_registry import STOCK_SPECIAL_APIS, ChunkStrategy

    cyq = [a for a in STOCK_SPECIAL_APIS if a.api_name == "cyq_perf"]
    assert cyq, "cyq_perf must remain in STOCK_SPECIAL_APIS"
    assert cyq[0].enabled is True, "cyq_perf.enabled must flip to True (Plan Task 2)"
    assert cyq[0].chunk_strategy == ChunkStrategy.STOCK, (
        "keep STOCK chunk strategy (ts_code+start_date+end_date handling)"
    )

    # cyq_chips must stay disabled (sibling, NOT in scope).
    chips = [a for a in STOCK_SPECIAL_APIS if a.api_name == "cyq_chips"]
    assert chips, "cyq_chips entry must still exist"
    assert chips[0].enabled is False, "cyq_chips stays disabled (not in scope)"


def test_cyq_perf_in_active_download_set():
    """Plan Task 2: incremental_update.py source contains cyq_perf (per-ts_code
    iteration set). Mirror how margin_detail (also STOCK) is wired via
    PER_CODE_RANGE_APIS or an explicit cyq_perf branch."""
    src = (ROOT / "data-source/tushare/incremental_update.py").read_text()
    assert "cyq_perf" in src, (
        "cyq_perf must appear in incremental_update.py active download set"
    )
