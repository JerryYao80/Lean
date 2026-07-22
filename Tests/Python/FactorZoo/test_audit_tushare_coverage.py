"""Tests for the tushare coverage audit script (Phase 1, spec §6)."""
import sys
from pathlib import Path

# allow importing Scripts.factor_zoo when run from repo root
# test file lives at Tests/Python/FactorZoo/test_*.py so parents[3] is repo root
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "Scripts"))

from factor_zoo.audit_tushare_coverage import audit_table, CoverageStatus


def test_audit_table_missing_dir_returns_not_downloaded(tmp_path):
    """A table dir that does not exist -> NOT_DOWNLOADED."""
    report = audit_table("does_not_exist", data_root=str(tmp_path), latest_trade_date="20260722")
    assert report.status == CoverageStatus.NOT_DOWNLOADED
    assert report.rows == 0
    assert report.last_date is None


def test_audit_table_empty_dir_returns_empty(tmp_path):
    """A table dir that exists but has no parquet -> EMPTY."""
    (tmp_path / "empty_table").mkdir()
    report = audit_table("empty_table", data_root=str(tmp_path), latest_trade_date="20260722")
    assert report.status == CoverageStatus.EMPTY


def test_audit_table_stale_returns_stale(tmp_path):
    """A table whose last trade_date < latest - 10 calendar days -> STALE."""
    # build a fake ts_code-partitioned table with one row at 20260101
    import pyarrow as pa, pyarrow.parquet as pq
    d = tmp_path / "daily_basic" / "ts_code=600519.SH"
    d.mkdir(parents=True)
    tbl = pa.table({"ts_code": ["600519.SH"], "trade_date": ["20260101"], "close": [100.0]})
    pq.write_table(tbl, d / "data.parquet")
    report = audit_table("daily_basic", data_root=str(tmp_path), latest_trade_date="20260722")
    assert report.status == CoverageStatus.STALE
    assert report.last_date == "20260101"
    assert report.rows == 1
