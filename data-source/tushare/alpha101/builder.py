# data-source/tushare/alpha101/builder.py
"""build_day: load panel once, eval all 101 alphas, write parquet + Influx per alpha."""
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

from alpha101.formulas import ALPHAS, INDCLASS_LEVELS  # noqa: E402
from alpha101.panel_loader import load_panel, DEFAULT_TS_PATH  # noqa: E402

CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_RESULT_ROOT = os.environ.get("ALPHA101_RESULT_ROOT", str(_HERE.parents[2] / "result"))
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")


def _ts_ns(trade_date_compact: str) -> int:
    local = datetime(int(trade_date_compact[0:4]), int(trade_date_compact[4:6]),
                     int(trade_date_compact[6:8]), 15, 0, 0, tzinfo=CHINA_TZ)
    return int(local.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def to_line(ts_code: str, trade_date_compact: str, aid: str, value: float) -> str:
    return (f"lean_factor_{aid},ts_code={ts_code},trade_date={trade_date_compact} "
            f"{aid}={value:.12g} {_ts_ns(trade_date_compact)}")


def write_influx(lines: Iterable[str], url: str, org: str, bucket: str, token: str) -> int:
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
    for aid, fn in ALPHAS.items():
        try:
            wide = fn(panel)
        except Exception as exc:  # noqa: BLE001
            failed[aid] = f"{type(exc).__name__}: {exc}"
            continue
        if wide is None or wide.empty:
            continue
        if panel.asof in wide.index:
            row = wide.loc[panel.asof]
        else:
            row = wide.iloc[-1]
        date_dir = Path(result_root) / "factor-zoo" / aid / date_yyyy_mm_dd
        date_dir.mkdir(parents=True, exist_ok=True)
        for ts_code, val in row.items():
            if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
                continue
            rec = {"ts_code": ts_code, aid: float(val)}
            pd.DataFrame([rec]).to_parquet(date_dir / f"{ts_code}.parquet", index=False)
            rows_written += 1
            if write_influxdb:
                line = to_line(ts_code, trade_date_compact, aid, float(val))
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
    parser = argparse.ArgumentParser(description="alpha101 group builder")
    parser.add_argument("--date", required=True)
    parser.add_argument("--ts-codes", nargs="*", default=[])
    parser.add_argument("--data-root", default=DEFAULT_TS_PATH)
    parser.add_argument("--result-root", default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--influx", action="store_true")
    args = parser.parse_args()

    ts_codes = args.ts_codes
    if not ts_codes:
        from barra_cne5_data_loader import BarraCNE5DataLoader
        from alpha101.panel_loader import load_csi800_universe
        loader = BarraCNE5DataLoader(args.data_root)
        ts_codes = load_csi800_universe(loader, args.date.replace("-", ""))
    if not ts_codes:
        print("ERROR: no ts_codes", file=sys.stderr); return 2
    out = build_day(args.date, ts_codes, data_root=args.data_root,
                    result_root=args.result_root, write_influxdb=args.influx)
    print(f"Built {out['rows']} rows in {out['duration_ms']}ms; failed={list(out.get('failed', {}))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
