# data-source/tushare/technical/builder.py
"""build_day: load stk_factor_pro panel once, write all technical indicators.

Mirrors alpha101/builder.py group pattern: panel loaded once, each indicator's
latest value (asof row) written to result/factor-zoo/<factor_id>/<date>/
<ts_code>.parquet + InfluxDB lean_factor_<factor_id>.

stk_factor_pro indicators are ALREADY computed by tushare (MACD/RSI/KDJ/BOLL/
BIAS/CCI/WR/MFI/MTM/ROC/OBV/PSY/TRIX/DPO/CR/EMV/MASS/ASI/BBI/ATR/VR/DMI/EXPMA/
KTN/TAQ/DFMA/XSII). The builder only extracts the qfq variant of each for the
asof trade_date — no re-computation (tushare is the canonical source).
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib import parse, request
from zoneinfo import ZoneInfo

import pandas as pd

_HERE = Path(__file__).resolve().parent
_TUSHARE_DIR = _HERE.parents[1]
if str(_TUSHARE_DIR) not in sys.path:
    sys.path.insert(0, str(_TUSHARE_DIR))

from technical.panel_loader import load_panel, DEFAULT_TS_PATH, FACTOR_IDS  # noqa: E402

CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_RESULT_ROOT = os.environ.get("TECHNICAL_RESULT_ROOT", str(_HERE.parents[2] / "result"))
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")


def _ts_ns(trade_date_compact: str) -> int:
    local = datetime(int(trade_date_compact[0:4]), int(trade_date_compact[4:6]),
                     int(trade_date_compact[6:8]), 15, 0, 0, tzinfo=CHINA_TZ)
    return int(local.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def to_line(ts_code: str, trade_date_compact: str, fid: str, value: float) -> str:
    return (f"lean_factor_{fid},ts_code={ts_code},trade_date={trade_date_compact} "
            f"{fid}={value:.12g} {_ts_ns(trade_date_compact)}")


def write_influx(lines: Iterable[str], url: str, org: str, bucket: str,
                 token: str) -> int:
    payload = [l for l in lines if l]
    if not payload or not token:
        return 0
    q = parse.urlencode({"org": org, "bucket": bucket, "precision": "ns"})
    req = request.Request(
        f"{url.rstrip('/')}/api/v2/write?{q}",
        data=("\n".join(payload) + "\n").encode(), method="POST",
        headers={"Authorization": f"Token {token}", "Content-Type": "text/plain; charset=utf-8"},
    )
    with request.urlopen(req, timeout=60) as resp:
        return len(payload) if 200 <= resp.status < 300 else 0


def build_day(date_yyyy_mm_dd: str, ts_codes: list[str],
              data_root: str | None = None, result_root: str | None = None,
              write_influxdb: bool = False,
              influx_url: str | None = None, influx_org: str | None = None,
              influx_bucket: str | None = None, influx_token: str | None = None) -> dict:
    data_root = data_root or DEFAULT_TS_PATH
    result_root = result_root or DEFAULT_RESULT_ROOT
    trade_date_compact = date_yyyy_mm_dd.replace("-", "")
    started = time.perf_counter()

    panel = load_panel(ts_codes, trade_date_compact, data_root=data_root)
    if panel is None:
        return {"rows": 0, "duration_ms": 0, "failed": {}, "reason": "panel_empty"}

    lines: list[str] = []
    rows_written = 0
    failed: dict[str, str] = {}
    for fid in FACTOR_IDS:
        wide = panel.wide.get(fid)
        if wide is None or wide.empty:
            failed[fid] = "empty_indicator"
            continue
        try:
            if panel.asof in wide.index:
                row = wide.loc[panel.asof]
            else:
                row = wide.iloc[-1]
        except Exception as exc:  # noqa: BLE001
            failed[fid] = f"{type(exc).__name__}: {exc}"
            continue
        date_dir = Path(result_root) / "factor-zoo" / fid / date_yyyy_mm_dd
        date_dir.mkdir(parents=True, exist_ok=True)
        for ts_code, val in row.items():
            if val is None:
                continue
            try:
                fv = float(val)
            except (TypeError, ValueError):
                continue
            if math.isnan(fv) or math.isinf(fv):
                continue
            rec = {"ts_code": ts_code, fid: fv}
            pd.DataFrame([rec]).to_parquet(date_dir / f"{ts_code}.parquet", index=False)
            rows_written += 1
            if write_influxdb:
                line = to_line(ts_code, trade_date_compact, fid, fv)
                if line:
                    lines.append(line)

    if write_influxdb and lines:
        try:
            write_influx(lines, url=influx_url or DEFAULT_INFLUX_URL,
                         org=influx_org or DEFAULT_INFLUX_ORG,
                         bucket=influx_bucket or DEFAULT_INFLUX_BUCKET,
                         token=influx_token or DEFAULT_INFLUX_TOKEN or "")
        except Exception:
            pass

    return {"rows": rows_written, "duration_ms": int((time.perf_counter() - started) * 1000),
            "failed": failed}


def _cli() -> int:
    parser = argparse.ArgumentParser(description="technical group builder")
    parser.add_argument("--date", required=True)
    parser.add_argument("--ts-codes", nargs="*", default=[])
    parser.add_argument("--data-root", default=DEFAULT_TS_PATH)
    parser.add_argument("--result-root", default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--influx", action="store_true")
    args = parser.parse_args()

    ts_codes = args.ts_codes
    if not ts_codes:
        from barra_cne5_data_loader import BarraCNE5DataLoader
        loader = BarraCNE5DataLoader(args.data_root)
        ts_codes = loader.load_index_constituents(
            asof_date=args.date.replace("-", ""), index_code="000300.SH")
    if not ts_codes:
        print("ERROR: no ts_codes", file=sys.stderr)
        return 2
    out = build_day(args.date, ts_codes, data_root=args.data_root,
                    result_root=args.result_root, write_influxdb=args.influx)
    print(f"Built {out['rows']} rows in {out['duration_ms']}ms; "
          f"failed={len(out.get('failed', {}))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
