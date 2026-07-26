# Tests/Python/FactorZoo/test_factor_worker_alpha101.py
"""alpha101 group FactorBuilder: resolver reads alpha001 dir."""
import sys, json
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq, pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))
import factor_worker as fw  # noqa: E402


def test_alpha101_builder_registered():
    assert "alpha101" in [b.factor_id for b in fw.BUILDERS]

def test_alpha101_resolver_reads_alpha001(tmp_path, monkeypatch):
    d = tmp_path / "result" / "factor-zoo" / "alpha001" / "2026-07-23"
    d.mkdir(parents=True)
    (d / "000001.SZ.parquet").write_bytes(b"")
    monkeypatch.setattr(fw, "CROWDING_RESULT_ROOT", str(tmp_path / "result"))
    builder = next(b for b in fw.BUILDERS if b.factor_id == "alpha101")
    assert builder.latest_date_resolver() == "20260723"

def test_alpha101_resolver_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "CROWDING_RESULT_ROOT", str(tmp_path / "result"))
    builder = next(b for b in fw.BUILDERS if b.factor_id == "alpha101")
    assert builder.latest_date_resolver() is None
