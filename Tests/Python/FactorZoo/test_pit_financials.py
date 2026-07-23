"""STEP 0b: PIT financials — the财报延迟披露 lookahead boundary (spec §4.1, §7).

The hidden trap: if a factor reads the current-year annual report on a day
when that report has NOT been announced yet, it leaks future info. The fix:
anchor on f_ann_date <= t; if the current-year report isn't announced yet,
fall back to the most-recent announced report (last year's). These tests
enforce that.
"""
import sys
from pathlib import Path

# test file lives at Tests/Python/FactorZoo/test_*.py so parents[3] is repo root
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "Scripts"))

import pyarrow as pa
import pyarrow.parquet as pq

from factor_zoo.pit_financials import load_pit_annual


def _write_balancesheet(root, ts_code, rows):
    d = root / "balancesheet" / f"ts_code={ts_code}"
    d.mkdir(parents=True)
    tbl = pa.table(rows)
    pq.write_table(tbl, d / "data.parquet")


def test_load_pit_uses_announced_report_not_period_end():
    """On 2026-03-15, the 2025 annual report (end_date 20251231) is announced
    (f_ann_date 20260315). Factor may use FY2025 data. 2026-03-14 must NOT."""
    _write_balancesheet(Path("/tmp/fz_pit_test1"), "600519.SH", {
        "ts_code": ["600519.SH", "600519.SH"],
        "end_date": ["20241231", "20251231"],
        "f_ann_date": ["20250328", "20260315"],
        "ann_date": ["20250328", "20260315"],
        "report_type": ["1", "1"],
        "end_type": ["4", "4"],
        "total_assets": [1.0e10, 1.1e10],
    })
    # 2026-03-14: FY2025 not announced yet -> must return FY2024
    r = load_pit_annual("balancesheet", "600519.SH", "20260314",
                        data_root="/tmp/fz_pit_test1", value_col="total_assets")
    assert r is not None
    assert r["end_date"] == "20241231"
    assert r["total_assets"] == 1.0e10
    # 2026-03-15: FY2025 announced -> may use FY2025
    r2 = load_pit_annual("balancesheet", "600519.SH", "20260315",
                         data_root="/tmp/fz_pit_test1", value_col="total_assets")
    assert r2["end_date"] == "20251231"
    assert r2["total_assets"] == 1.1e10


def test_load_pit_insufficient_history_returns_none():
    """A newly listed firm with <2 annual reports announced by t -> None."""
    _write_balancesheet(Path("/tmp/fz_pit_test2"), "300999.SZ", {
        "ts_code": ["300999.SZ"],
        "end_date": ["20251231"],
        "f_ann_date": ["20260420"],
        "ann_date": ["20260420"],
        "report_type": ["1"],
        "end_type": ["4"],
        "total_assets": [5.0e8],
    })
    r = load_pit_annual("balancesheet", "300999.SZ", "20260420",
                        data_root="/tmp/fz_pit_test2", value_col="total_assets",
                        min_periods=2)
    assert r is None  # only 1 period available


def test_load_pit_picks_latest_revision_within_window():
    """If a restatement (update_flag='1') is announced later than the original
    (update_flag='0'), the PIT reader on that later date must pick the restatement."""
    _write_balancesheet(Path("/tmp/fz_pit_test3"), "000001.SZ", {
        "ts_code": ["000001.SZ", "000001.SZ"],
        "end_date": ["20241231", "20241231"],
        "f_ann_date": ["20250328", "20250610"],
        "ann_date": ["20250328", "20250610"],
        "report_type": ["1", "1"],
        "end_type": ["4", "4"],
        "update_flag": ["0", "1"],
        "total_assets": [2.0e12, 2.05e12],  # restated
    })
    r = load_pit_annual("balancesheet", "000001.SZ", "20250601",
                        data_root="/tmp/fz_pit_test3", value_col="total_assets")
    # 20250601 < 20250610, so restatement not yet announced -> original
    assert r["total_assets"] == 2.0e12
    r2 = load_pit_annual("balancesheet", "000001.SZ", "20250610",
                         data_root="/tmp/fz_pit_test3", value_col="total_assets")
    assert r2["total_assets"] == 2.05e12  # restatement now usable
