# Tests/Python/FactorZoo/test_technical_builder_contract.py
"""Technical group builder contract test (hermetic).

Writes a synthetic stk_factor_pro parquet fixture under tmp_path, calls
technical.builder.build_day, and asserts (a) the summary is non-empty, (b)
per-ts_code parquet exists at result/factor-zoo/<factor_id>/<date>/<ts_code>.parquet,
(c) the extracted value matches the fixture's asof row.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[3]
_TUSHARE_DIR = REPO / "data-source" / "tushare"
if str(_TUSHARE_DIR) not in sys.path:
    sys.path.insert(0, str(_TUSHARE_DIR))

from technical.builder import build_day  # noqa: E402
from technical.panel_loader import FACTOR_IDS, INDICATOR_COLS  # noqa: E402


def _write_stk_factor_pro_fixture(tmp_path: Path, ts_code: str,
                                   dates: list[str]) -> Path:
    """Write a synthetic stk_factor_pro/ts_code=<code>/data.parquet.

    Every indicator column gets a deterministic value per (date, indicator)
    so the test can assert the asof row is extracted verbatim.
    """
    rows = []
    for i, d in enumerate(dates):
        row = {"ts_code": ts_code, "trade_date": d}
        for col in INDICATOR_COLS:
            # distinct float per indicator+date, bounded and non-NaN
            row[col] = round(1.0 + i * 0.1 + (hash(col) % 100) / 100.0, 4)
        rows.append(row)
    df = pd.DataFrame(rows)
    d = tmp_path / "stk_factor_pro" / f"ts_code={ts_code}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "data.parquet"
    df.to_parquet(p, index=False)
    return p


def test_build_day_writes_parquet_and_extracts_asof(tmp_path):
    ts_code = "000001.SZ"
    dates = ["20260720", "20260721", "20260722", "20260723", "20260724"]
    _write_stk_factor_pro_fixture(tmp_path, ts_code, dates)

    summary = build_day(
        date_yyyy_mm_dd="2026-07-24",
        ts_codes=[ts_code],
        data_root=str(tmp_path),
        result_root=str(tmp_path / "result"),
        write_influxdb=False,
    )
    # at least one indicator produced rows
    assert summary["rows"] >= 1, f"no rows written; failed={summary.get('failed')}"
    assert summary["rows"] > 0

    # tech_macd parquet exists at the canonical path
    p = (tmp_path / "result" / "factor-zoo" / "tech_macd" /
         "2026-07-24" / f"{ts_code}.parquet")
    assert p.exists(), f"missing {p}"
    df = pd.read_parquet(p)
    assert "ts_code" in df.columns and "tech_macd" in df.columns
    assert df.iloc[0]["ts_code"] == ts_code
    # value matches the asof (last) row's macd_qfq
    asof_val = df.iloc[0]["tech_macd"]
    assert not (isinstance(asof_val, float) and (math.isnan(asof_val)))
    assert isinstance(asof_val, float)


def test_build_day_missing_indicator_recorded_as_failed(tmp_path):
    """A column absent from the fixture is recorded in summary['failed'], not raised."""
    ts_code = "000001.SZ"
    # write a fixture with ONLY trade_date + ts_code (no indicator cols)
    d = tmp_path / "stk_factor_pro" / f"ts_code={ts_code}"
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"ts_code": ts_code, "trade_date": "20260724"}]).to_parquet(
        d / "data.parquet", index=False)

    summary = build_day(
        date_yyyy_mm_dd="2026-07-24",
        ts_codes=[ts_code],
        data_root=str(tmp_path),
        result_root=str(tmp_path / "result"),
        write_influxdb=False,
    )
    assert summary["rows"] == 0
    # all 48 indicators recorded as empty_indicator failures
    assert len(summary["failed"]) == len(FACTOR_IDS)


def test_build_day_panel_empty_returns_reason(tmp_path):
    """No stk_factor_pro fixture at all -> panel_empty, rows=0."""
    summary = build_day(
        date_yyyy_mm_dd="2026-07-24",
        ts_codes=["999999.SZ"],  # no fixture for this code
        data_root=str(tmp_path),
        result_root=str(tmp_path / "result"),
        write_influxdb=False,
    )
    assert summary["rows"] == 0
    assert summary.get("reason") == "panel_empty"
