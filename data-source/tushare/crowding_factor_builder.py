"""Crowding factor builder — join 5 tushare parquet tables, compute composite
crowding per ts_code, persist to parquet + InfluxDB.

Tables (per ts_code, partitioned by ts_code=):
    {data_folder}/daily_basic/ts_code={ts_code}/data.parquet
    {data_folder}/moneyflow/ts_code={ts_code}/data.parquet
    {data_folder}/margin_detail/ts_code={ts_code}/data.parquet
    {data_folder}/hsgt_top10/year=*/data.parquet   (per-year, filtered by ts_code)
    {data_folder}/cyq_perf/ts_code={ts_code}/data.parquet
    {data_folder}/adj_factor/ts_code={ts_code}/data.parquet  (for cyq cost复权)

Output:
    {result_root}/crowding-factor/<YYYY-MM-DD>/<ts_code>.parquet
    columns: ts_code, composite, trading, fund, chip, degraded, hk_hold_stale
    Also mirrored to InfluxDB measurement `lean_ashare_crowding_factor`.

Computation:
    - davol20 derived from daily_basic.turnover_rate (short=20d / long=120d - 1)
      using the most recent 120 trading days. If insufficient history (no long
      window), falls back to 0.0.
    - net_mf_ratio = moneyflow.net_mf_amount / daily_basic.circ_mv (where
      circ_mv is in 万元; moneyflow.net_mf_amount is also in 万元).
    - margin_growth = 5-day log change of margin_detail.rzye series
      (tail 6 trading days; if fewer, 0.0).
    - hk_hold_ratio = hsgt_top10.ratio for that ts_code on trade_date, or
      forward-filled from the most recent prior record (hk_hold_stale=true
      when forward-filled).
    - cyq cost fields (cost_5pct, cost_95pct, weight_avg) multiplied by
      adj_factor → `_adj` suffix. winner_rate used as-is (复权不变).

Degradation:
    - If cyq_perf is missing for a ts_code on trade_date, the chip axis is
      dropped and composite is computed as trading*0.5 + fund*0.5 with
      degraded=true.
    - If any of daily_basic / moneyflow / margin_detail / hsgt_top10 is
      missing for a ts_code on trade_date, the ts_code is skipped (Missing).

Module-level read_* functions allow tests to monkeypatch parquet reads.
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

# Mirror barra_cne5v2_factor_bridge.py import pattern: ensure
# Algorithm.Python/ is on sys.path so `from CrowdingFactors import ...` works.
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
_PY_ALGO_DIR = _REPO_ROOT / "Algorithm.Python"
for _p in [str(_REPO_ROOT), str(_PY_ALGO_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from CrowdingFactors import CrowdingFactors, DEFAULT_PARAMS  # noqa: E402

CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_TS_PATH = os.environ.get(
    "TUSHARE_DATA_PATH", "/home/project/tushare-downloader/tushare_data_v2"
)
DEFAULT_RESULT_ROOT = os.environ.get(
    "CROWDING_RESULT_ROOT", str(_REPO_ROOT / "result")
)
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")
MEASUREMENT = "lean_ashare_crowding_factor"

OUTPUT_COLUMNS = [
    "ts_code", "composite", "trading", "fund", "chip", "degraded", "hk_hold_stale",
]


# ────────────────────────────────────────────────────────────────────────────
# Module-level parquet readers (monkeypatchable in tests)
# ────────────────────────────────────────────────────────────────────────────
def _read_ts_code_parquet(data_root: str, table: str, ts_code: str) -> pd.DataFrame:
    """Read {data_root}/{table}/ts_code={ts_code}/data.parquet. Empty if missing."""
    p = Path(data_root) / table / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    return df


def read_daily_basic(data_root: str, ts_code: str, trade_date: str) -> pd.DataFrame:
    """Daily_basic parquet for ts_code (full history — builder needs 120d for davol20)."""
    return _read_ts_code_parquet(data_root, "daily_basic", ts_code)


def read_moneyflow(data_root: str, ts_code: str, trade_date: str) -> pd.DataFrame:
    """Moneyflow parquet for ts_code, filtered to trade_date."""
    df = _read_ts_code_parquet(data_root, "moneyflow", ts_code)
    if df.empty or "trade_date" not in df.columns:
        return df
    return df[df["trade_date"].astype(str) == str(trade_date)].copy()


def read_margin_detail(data_root: str, ts_code: str, trade_date: str) -> pd.DataFrame:
    """Margin_detail parquet for ts_code (full history — builder needs tail 6d)."""
    return _read_ts_code_parquet(data_root, "margin_detail", ts_code)


def read_hsgt_top10(data_root: str, ts_code: str, trade_date: str) -> pd.DataFrame:
    """Hsgt_top10 stored per-year; filter by ts_code + trade_date.

    Builder forward-fills if trade_date not present (returns latest row <= trade_date)
    and the caller marks hk_hold_stale=true when forward-filled.
    """
    base = Path(data_root) / "hsgt_top10"
    if not base.exists():
        return pd.DataFrame()
    frames = []
    for p in sorted(base.glob("year=*/data.parquet")):
        try:
            frames.append(pd.read_parquet(p))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    if "ts_code" in df.columns:
        df = df[df["ts_code"].astype(str) == str(ts_code)]
    if "trade_date" in df.columns and not df.empty:
        df = df.sort_values("trade_date")
        # Forward-fill: take latest row with trade_date <= target; if none, empty
        prior = df[df["trade_date"].astype(str) <= str(trade_date)]
        if prior.empty:
            return pd.DataFrame()
        latest = prior["trade_date"].astype(str).max()
        return prior[prior["trade_date"].astype(str) == latest].iloc[[-1]].copy()
    return pd.DataFrame()


def read_cyq_perf(data_root: str, ts_code: str, trade_date: str) -> pd.DataFrame:
    """Cyq_perf parquet for ts_code, filtered to trade_date."""
    df = _read_ts_code_parquet(data_root, "cyq_perf", ts_code)
    if df.empty or "trade_date" not in df.columns:
        return df
    return df[df["trade_date"].astype(str) == str(trade_date)].copy()


def read_adj_factor(data_root: str, ts_code: str, trade_date: str) -> pd.DataFrame:
    """Adj_factor parquet for ts_code, filtered to trade_date (single-row)."""
    df = _read_ts_code_parquet(data_root, "adj_factor", ts_code)
    if df.empty or "trade_date" not in df.columns:
        return df
    return df[df["trade_date"].astype(str) == str(trade_date)].copy()


# ────────────────────────────────────────────────────────────────────────────
# Row assembly
# ────────────────────────────────────────────────────────────────────────────
def _to_float(v, default: float = 0.0) -> float:
    try:
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _compute_davol20(daily_basic_full: pd.DataFrame, trade_date: str,
                     short_window: int = 20, long_window: int = 120) -> float:
    """DAVOL20 = short_turnover_mean / long_turnover_mean - 1.

    Uses the row at trade_date as the "current" reference: short window =
    trailing `short_window` rows ending at trade_date; long window =
    trailing `long_window` rows. If long window is incomplete, returns 0.0
    (insufficient history → no crowding signal).
    """
    if daily_basic_full.empty or "turnover_rate" not in daily_basic_full.columns:
        return 0.0
    df = daily_basic_full.copy()
    if "trade_date" in df.columns:
        df = df[df["trade_date"].astype(str) <= str(trade_date)]
        df = df.sort_values("trade_date")
    if df.empty:
        return 0.0
    long_tail = df["turnover_rate"].astype(float).tail(long_window)
    short_tail = long_tail.tail(short_window)
    if len(long_tail) < long_window or len(short_tail) < short_window:
        # Insufficient history — don't pretend crowding exists.
        return 0.0
    long_mean = float(long_tail.mean())
    short_mean = float(short_tail.mean())
    if long_mean <= 0:
        return 0.0
    return short_mean / long_mean - 1.0


def _compute_margin_growth(margin_full: pd.DataFrame, trade_date: str,
                            window: int = 5) -> float:
    """5-day log change of margin_detail.rzye tail ending at trade_date."""
    if margin_full.empty or "rzye" not in margin_full.columns:
        return 0.0
    df = margin_full.copy()
    if "trade_date" in df.columns:
        df = df[df["trade_date"].astype(str) <= str(trade_date)]
        df = df.sort_values("trade_date")
    if df.empty:
        return 0.0
    series = df["rzye"].astype(float).tail(window + 1)
    valid = series.replace([float("inf"), float("-inf")], float("nan")).dropna()
    if len(valid) < 2 or float(valid.iloc[0]) <= 0:
        return 0.0
    log_changes = []
    arr = valid.astype(float).values
    for i in range(1, len(arr)):
        if arr[i - 1] > 0 and math.isfinite(arr[i]) and math.isfinite(arr[i - 1]):
            lc = math.log(arr[i] / arr[i - 1])
            if math.isfinite(lc):
                log_changes.append(lc)
    if not log_changes:
        return 0.0
    return float(sum(log_changes) / len(log_changes))


def _build_crowding_row(data_root: str, ts_code: str, trade_date_compact: str) -> dict | None:
    """Join 5 tables for one ts_code + trade_date (YYYYMMDD) → crowding_row dict.

    Returns None if any required table is missing (Missing). For cyq_perf
    missing specifically, the row is returned with chip fields set to NaN and
    `degraded=true`; for the other tables missing, None is returned (skip).
    """
    daily_basic_full = read_daily_basic(data_root, ts_code, trade_date_compact)
    if daily_basic_full.empty:
        return None
    db_row = daily_basic_full[daily_basic_full["trade_date"].astype(str) == str(trade_date_compact)]
    if db_row.empty:
        return None
    db_row = db_row.iloc[0]

    moneyflow_df = read_moneyflow(data_root, ts_code, trade_date_compact)
    if moneyflow_df.empty:
        return None
    mf_row = moneyflow_df.iloc[0]

    margin_full = read_margin_detail(data_root, ts_code, trade_date_compact)
    # margin_detail may legitimately be missing for some stocks (non-margin-eligible)
    # — degrade margin_growth to 0.0 but keep the row. Set margin_growth=0.

    hsgt_df = read_hsgt_top10(data_root, ts_code, trade_date_compact)
    hk_hold_stale = False
    if not hsgt_df.empty:
        hsgt_row = hsgt_df.iloc[0]
        # If the matched trade_date is strictly less than target, it's stale.
        matched_date = str(hsgt_row.get("trade_date", ""))
        if matched_date and matched_date != str(trade_date_compact):
            hk_hold_stale = True
    else:
        hsgt_row = None

    cyq_df = read_cyq_perf(data_root, ts_code, trade_date_compact)
    degraded = False
    cyq_row = None
    if cyq_df.empty:
        degraded = True
    else:
        cyq_row = cyq_df.iloc[0]

    # adj_factor for cyq cost 复权
    adj_factor = 1.0
    if cyq_row is not None:
        adj_df = read_adj_factor(data_root, ts_code, trade_date_compact)
        if not adj_df.empty and "adj_factor" in adj_df.columns:
            adj_factor = _to_float(adj_df.iloc[0]["adj_factor"], default=1.0)
        elif adj_df.empty:
            # adj_factor parquet missing — assume 1.0 (no adjustment) but mark stale? No.
            adj_factor = 1.0

    # Assemble 9 expected fields for composite_crowding
    davol20 = _compute_davol20(daily_basic_full, trade_date_compact)
    volume_ratio = _to_float(db_row.get("volume_ratio"), default=1.0)

    circ_mv = _to_float(db_row.get("circ_mv"), default=0.0)
    net_mf_amount = _to_float(mf_row.get("net_mf_amount"), default=0.0)
    if circ_mv > 0:
        net_mf_ratio = net_mf_amount / circ_mv
    else:
        net_mf_ratio = 0.0

    margin_growth = _compute_margin_growth(margin_full, trade_date_compact) if not margin_full.empty else 0.0

    hk_hold_ratio = _to_float(hsgt_row.get("ratio"), default=0.0) if hsgt_row is not None else 0.0

    if cyq_row is not None:
        cost_5pct_adj = _to_float(cyq_row.get("cost_5pct"), default=0.0) * adj_factor
        cost_95pct_adj = _to_float(cyq_row.get("cost_95pct"), default=0.0) * adj_factor
        weight_avg_adj = _to_float(cyq_row.get("weight_avg"), default=1.0) * adj_factor
        winner_rate = _to_float(cyq_row.get("winner_rate"), default=0.0)
    else:
        cost_5pct_adj = float("nan")
        cost_95pct_adj = float("nan")
        weight_avg_adj = float("nan")
        winner_rate = float("nan")

    return {
        "ts_code": ts_code,
        "trade_date": trade_date_compact,
        "davol20": davol20,
        "volume_ratio": volume_ratio,
        "net_mf_ratio": net_mf_ratio,
        "margin_growth": margin_growth,
        "hk_hold_ratio": hk_hold_ratio,
        "cost_5pct_adj": cost_5pct_adj,
        "cost_95pct_adj": cost_95pct_adj,
        "weight_avg_adj": weight_avg_adj,
        "winner_rate": winner_rate,
        "degraded": degraded,
        "hk_hold_stale": hk_hold_stale,
    }


def _composite_from_row(row: dict, degraded: bool) -> tuple[float, float, float, float]:
    """Compute composite/trading/fund/chip from crowding row.

    Returns (composite, trading, fund, chip). For degraded rows (cyq missing),
    chip is NaN and composite = trading*0.5 + fund*0.5.
    """
    # Build a pd.Series for CrowdingFactors.composite_crowding
    series = pd.Series({
        "davol20": row["davol20"],
        "volume_ratio": row["volume_ratio"],
        "net_mf_ratio": row["net_mf_ratio"],
        "margin_growth": row["margin_growth"],
        "hk_hold_ratio": row["hk_hold_ratio"],
        "cost_5pct_adj": row["cost_5pct_adj"],
        "cost_95pct_adj": row["cost_95pct_adj"],
        "weight_avg_adj": row["weight_avg_adj"],
        "winner_rate": row["winner_rate"],
    })
    trading = CrowdingFactors.trading_crowding(series, DEFAULT_PARAMS)
    fund = CrowdingFactors.fund_crowding(series, DEFAULT_PARAMS)
    if degraded:
        chip = float("nan")
        composite = float(trading) * 0.5 + float(fund) * 0.5
    else:
        chip = CrowdingFactors.chip_crowding(series, DEFAULT_PARAMS)
        composite = CrowdingFactors.composite_crowding(series, DEFAULT_PARAMS)
    # Clamp to [0, 1]
    composite = max(0.0, min(1.0, float(composite)))
    return composite, float(trading), float(fund), float(chip)


# ────────────────────────────────────────────────────────────────────────────
# Day build
# ────────────────────────────────────────────────────────────────────────────
def _ts_ns(trade_date_compact: str) -> int:
    """Convert YYYYMMDD → UTC nanoseconds (15:00 Shanghai)."""
    local = datetime(
        int(trade_date_compact[0:4]), int(trade_date_compact[4:6]),
        int(trade_date_compact[6:8]), 15, 0, 0, tzinfo=CHINA_TZ,
    )
    return int(local.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def to_line(ts_code: str, trade_date_compact: str, factors: dict[str, float]) -> str:
    fields = [
        f"{k}={v:.12g}" for k, v in factors.items()
        if v is not None and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
    ]
    if not fields:
        return ""
    return (
        f"{MEASUREMENT},ts_code={ts_code},trade_date={trade_date_compact} "
        f"{','.join(fields)} {_ts_ns(trade_date_compact)}"
    )


def write_influx(lines: Iterable[str], url: str, org: str, bucket: str, token: str) -> int:
    """Mirror export_forward_factors.py:283 line-protocol write."""
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
        headers={"Authorization": f"Token {token}", "Content-Type": "text/plain; charset=utf-8"},
    )
    with request.urlopen(req, timeout=60) as resp:
        return len(payload) if 200 <= resp.status < 300 else 0


def _format_date(yyyy_mm_dd: str) -> str:
    """'2024-01-05' → '20240105'."""
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
    """Build crowding factor for one trade date across many ts_codes.

    Args:
        date_yyyy_mm_dd: 'YYYY-MM-DD' (e.g., '2024-01-05').
        ts_codes: list of Tushare codes like '600519.SH'.
        data_root: tushare parquet root. Defaults to DEFAULT_TS_PATH.
        result_root: result dir root. Defaults to DEFAULT_RESULT_ROOT.
        write_influxdb: if True, also write to InfluxDB.
        influx_*: InfluxDB connection params (env defaults if None).

    Returns:
        DataFrame with columns OUTPUT_COLUMNS, one row per ts_code that had
        all required tables present. Skips ts_codes with Missing data.
    """
    data_root = data_root or DEFAULT_TS_PATH
    result_root = result_root or DEFAULT_RESULT_ROOT
    trade_date_compact = _format_date(date_yyyy_mm_dd)
    date_dir = Path(result_root) / "crowding-factor" / date_yyyy_mm_dd
    date_dir.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    out_rows: list[dict] = []
    for ts_code in ts_codes:
        row = _build_crowding_row(data_root, ts_code, trade_date_compact)
        if row is None:
            # Missing required table → skip
            continue
        degraded = bool(row["degraded"])
        composite, trading, fund, chip = _composite_from_row(row, degraded)
        record = {
            "ts_code": ts_code,
            "composite": composite,
            "trading": trading,
            "fund": fund,
            "chip": chip,
            "degraded": degraded,
            "hk_hold_stale": bool(row["hk_hold_stale"]),
        }
        out_rows.append(record)
        # Write per-ts_code parquet
        parquet_path = date_dir / f"{ts_code}.parquet"
        pd.DataFrame([record]).to_parquet(parquet_path, index=False)
        # Build InfluxDB line
        if write_influxdb:
            factors = {
                "composite": composite,
                "trading": trading,
                "fund": fund,
                "chip": chip if not (isinstance(chip, float) and math.isnan(chip)) else None,
                "degraded": 1.0 if degraded else 0.0,
                "hk_hold_stale": 1.0 if record["hk_hold_stale"] else 0.0,
            }
            line = to_line(ts_code, trade_date_compact, factors)
            if line:
                lines.append(line)

    if write_influxdb and lines:
        write_influx(
            lines,
            url=influx_url or DEFAULT_INFLUX_URL,
            org=influx_org or DEFAULT_INFLUX_ORG,
            bucket=influx_bucket or DEFAULT_INFLUX_BUCKET,
            token=influx_token or DEFAULT_INFLUX_TOKEN or "",
        )

    if not out_rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.DataFrame(out_rows, columns=OUTPUT_COLUMNS)


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────
def _cli() -> int:
    parser = argparse.ArgumentParser(description="Crowding factor builder")
    parser.add_argument("--date", required=True, help="Trade date YYYY-MM-DD")
    parser.add_argument("--ts-codes", nargs="*", default=[],
                        help="Tushare codes; if empty, CSI300 universe is used")
    parser.add_argument("--data-root", default=DEFAULT_TS_PATH)
    parser.add_argument("--result-root", default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--influx", action="store_true",
                        help="Also write to InfluxDB")
    args = parser.parse_args()

    ts_codes = args.ts_codes
    if not ts_codes:
        # Load CSI300 constituents via barra loader if available
        try:
            sys.path.insert(0, str(_HERE))
            from barra_cne5_data_loader import BarraCNE5DataLoader  # noqa: E402
            loader = BarraCNE5DataLoader(args.data_root)
            ts_codes = loader.load_index_constituents(
                asof_date=_format_date(args.date), index_code="000300.SH"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: failed to load CSI300 universe: {exc}", file=sys.stderr)
            ts_codes = []

    if not ts_codes:
        print("ERROR: no ts_codes provided and CSI300 universe unavailable",
              file=sys.stderr)
        return 2

    out = build_day(
        args.date, ts_codes,
        data_root=args.data_root, result_root=args.result_root,
        write_influxdb=args.influx,
    )
    print(f"Built {len(out)} crowding rows for {args.date}")
    if not out.empty:
        print(out.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
