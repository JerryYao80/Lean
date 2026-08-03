"""Amplitude Anomaly factor builder (Z-score of intraday amplitude).

Amplitude = (high - low) / pre_close
Z-score = (amplitude_t - mean(amplitude, 20)) / std(amplitude, 20)
Clamped to [-5, 5].

Need >= 21 days (current + trailing 20). Daily factor.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib import parse, request
from zoneinfo import ZoneInfo

import pandas as pd

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[3]
_FACTOR_ZOO_DIR = _REPO_ROOT / "Scripts" / "factor_zoo"
if str(_FACTOR_ZOO_DIR) not in sys.path:
    sys.path.insert(0, str(_FACTOR_ZOO_DIR))

CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_TS_PATH = os.environ.get(
    "TUSHARE_DATA_PATH", "/home/project/tushare-downloader/tushare_data_v2"
)
DEFAULT_RESULT_ROOT = os.environ.get(
    "AMPLITUDE_ANOMALY_RESULT_ROOT", str(_REPO_ROOT / "result")
)
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")

FACTOR_ID = "amplitude_anomaly"
MEASUREMENT = "lean_factor_amplitude_anomaly"
OUTPUT_COLUMNS = ["ts_code", FACTOR_ID]

_WINDOW = 20
_CLAMP_MIN = -5.0
_CLAMP_MAX = 5.0


def _read_partition(data_root: str, table: str, ts_code: str) -> pd.DataFrame:
    p = Path(data_root) / table / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


def _to_float(v, default=None):
    try:
        if v is None:
            return default
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _compute_amplitude_zscore(
    daily_df: pd.DataFrame, asof: str, window: int = _WINDOW
) -> float | None:
    """Compute amplitude Z-score: (amp_t - mean) / std over trailing window.

    Amplitude = (high - low) / pre_close
    Z-score clamped to [-5, 5].
    Need >= window + 1 days (current + trailing window).
    """
    if daily_df is None or daily_df.empty:
        return None
    required = {"trade_date", "high", "low", "pre_close"}
    if not required.issubset(daily_df.columns):
        return None

    d = daily_df.copy()
    d["trade_date"] = d["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    d = d[d["trade_date"].astype(str) <= str(asof)]
    if d.empty:
        return None
    d = d.sort_values("trade_date")

    # Need at least window + 1 days
    if len(d) < window + 1:
        return None

    # Compute amplitude = (high - low) / pre_close
    d["high_f"] = d["high"].apply(lambda x: _to_float(x))
    d["low_f"] = d["low"].apply(lambda x: _to_float(x))
    d["pre_close_f"] = d["pre_close"].apply(lambda x: _to_float(x))
    d = d.dropna(subset=["high_f", "low_f", "pre_close_f"])

    if len(d) < window + 1:
        return None

    # Filter out rows with zero pre_close
    d = d[d["pre_close_f"] > 0]
    if len(d) < window + 1:
        return None

    d["amplitude"] = (d["high_f"] - d["low_f"]) / d["pre_close_f"]
    d = d.dropna(subset=["amplitude"])

    if len(d) < window + 1:
        return None

    # Trailing window for mean/std (excluding current day)
    trailing = d.iloc[-(window + 1) : -1]  # exclude the current (last) row
    current_amp = float(d["amplitude"].iloc[-1])

    if len(trailing) < window:
        return None

    amplitudes = trailing["amplitude"].astype(float).values
    n = len(amplitudes)
    if n < 2:
        return None

    mean_amp = sum(amplitudes) / n
    variance = sum((a - mean_amp) ** 2 for a in amplitudes) / n
    # Use a tolerance to avoid floating-point noise when all values are identical.
    _VAR_TOL = 1e-30
    if variance <= _VAR_TOL:
        return 0.0

    std_amp = math.sqrt(variance)
    if std_amp == 0:
        return 0.0

    zscore = (current_amp - mean_amp) / std_amp
    return float(max(_CLAMP_MIN, min(_CLAMP_MAX, zscore)))


def _ts_ns(trade_date_compact: str) -> int:
    local = datetime(
        int(trade_date_compact[0:4]),
        int(trade_date_compact[4:6]),
        int(trade_date_compact[6:8]),
        15,
        0,
        0,
        tzinfo=CHINA_TZ,
    )
    return int(local.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def to_line(ts_code: str, trade_date_compact: str, factors: dict) -> str:
    fields = [
        f"{k}={v:.12g}"
        for k, v in factors.items()
        if v is not None and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
    ]
    if not fields:
        return ""
    return (
        f"{MEASUREMENT},ts_code={ts_code},trade_date={trade_date_compact} "
        f"{','.join(fields)} {_ts_ns(trade_date_compact)}"
    )


def write_influx(
    lines: Iterable[str], url: str, org: str, bucket: str, token: str
) -> int:
    payload = [l for l in lines if l]
    if not payload:
        return 0
    if not token:
        return 0
    q = parse.urlencode({"org": org, "bucket": bucket, "precision": "ns"})
    req = request.Request(
        f"{url.rstrip('/')}/api/v2/write?{q}",
        data=("\n".join(payload) + "\n").encode(),
        method="POST",
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "text/plain; charset=utf-8",
        },
    )
    with request.urlopen(req, timeout=60) as resp:
        return len(payload) if 200 <= resp.status < 300 else 0


def _format_date(yyyy_mm_dd: str) -> str:
    return yyyy_mm_dd.replace("-", "")


def build_day(
    date_yyyy_mm_dd: str,
    ts_codes: list[str],
    data_root: str | None = None,
    result_root: str | None = None,
    write_influxdb: bool = False,
    influx_url: str | None = None,
    influx_org: str | None = None,
    influx_bucket: str | None = None,
    influx_token: str | None = None,
) -> pd.DataFrame:
    data_root = data_root or DEFAULT_TS_PATH
    result_root = result_root or DEFAULT_RESULT_ROOT
    trade_date_compact = _format_date(date_yyyy_mm_dd)
    factor_dir = Path(result_root) / "factor-zoo" / FACTOR_ID
    factor_dir.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    out_rows: list[dict] = []
    for ts_code in ts_codes:
        daily_df = _read_partition(data_root, "daily", ts_code)
        value = _compute_amplitude_zscore(daily_df, trade_date_compact)
        if value is None:
            continue
        record = {"ts_code": ts_code, FACTOR_ID: float(value)}
        out_rows.append(record)
        if write_influxdb:
            line = to_line(ts_code, trade_date_compact, {FACTOR_ID: float(value)})
            if line:
                lines.append(line)

    if out_rows:
        pd.DataFrame(out_rows).to_parquet(
            factor_dir / f"{date_yyyy_mm_dd}.parquet", index=False
        )
    if write_influxdb and lines:
        try:
            write_influx(
                lines,
                url=influx_url or DEFAULT_INFLUX_URL,
                org=influx_org or DEFAULT_INFLUX_ORG,
                bucket=influx_bucket or DEFAULT_INFLUX_BUCKET,
                token=influx_token or DEFAULT_INFLUX_TOKEN or "",
            )
        except Exception:
            pass

    if not out_rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.DataFrame(out_rows, columns=OUTPUT_COLUMNS)


def _cli() -> int:
    parser = argparse.ArgumentParser(description=f"{FACTOR_ID} factor builder")
    parser.add_argument("--date", required=True, help="Trade date YYYY-MM-DD")
    parser.add_argument(
        "--ts-codes", nargs="*", default=[],
        help="Tushare codes; if empty, CSI300 universe is used"
    )
    parser.add_argument("--data-root", default=DEFAULT_TS_PATH)
    parser.add_argument("--result-root", default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--influx", action="store_true")
    args = parser.parse_args()

    ts_codes = args.ts_codes
    if not ts_codes:
        try:
            sys.path.insert(0, str(_HERE.parent))
            from barra_cne5_data_loader import BarraCNE5DataLoader  # noqa: E402

            loader = BarraCNE5DataLoader(args.data_root)
            ts_codes = loader.load_index_constituents(
                asof_date=_format_date(args.date), index_code="000300.SH"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: failed to load CSI300 universe: {exc}", file=sys.stderr)
            ts_codes = []

    if not ts_codes:
        print(
            "ERROR: no ts_codes provided and CSI300 universe unavailable",
            file=sys.stderr,
        )
        return 2

    out = build_day(
        args.date,
        ts_codes,
        data_root=args.data_root,
        result_root=args.result_root,
        write_influxdb=args.influx,
    )
    print(f"Built {len(out)} {FACTOR_ID} rows for {args.date}")
    if not out.empty:
        print(out.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
