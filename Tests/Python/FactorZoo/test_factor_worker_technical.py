# Tests/Python/FactorZoo/test_factor_worker_technical.py
"""technical group FactorBuilder: registered + resolver reads tech_macd dir."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))
import factor_worker as fw  # noqa: E402


def test_technical_builder_registered():
    assert "technical" in [b.factor_id for b in fw.BUILDERS]


def test_technical_resolver_reads_tech_macd(tmp_path, monkeypatch):
    d = tmp_path / "result" / "factor-zoo" / "tech_macd" / "2026-07-23"
    d.mkdir(parents=True)
    (d / "000001.SZ.parquet").write_bytes(b"")
    monkeypatch.setattr(fw, "CROWDING_RESULT_ROOT", str(tmp_path / "result"))
    builder = next(b for b in fw.BUILDERS if b.factor_id == "technical")
    assert builder.latest_date_resolver() == "20260723"


def test_technical_resolver_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "CROWDING_RESULT_ROOT", str(tmp_path / "result"))
    builder = next(b for b in fw.BUILDERS if b.factor_id == "technical")
    assert builder.latest_date_resolver() is None
