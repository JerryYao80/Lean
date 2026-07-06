"""Forward-looking factor exporter (前瞻性因子导出器).

3 个立足:
  - 数据立足: tushare parquet 表 (moneyflow / moneyflow_hsgt / bak_daily / stk_auction_o / express / forecast / stk_holdertrade / pledge_stat / disclosure_date / cn_m / limit_list_d / cb_share)
  - 计算立足: 本地算子 (与 C# Forward 因子类静态方法同口径)
  - 消费立足: 写入 InfluxDB (lean_ashare_forward_factor), 供 Layer A 选股 / Layer B 风控消费

3 层管线:
  - Layer A (选股): big_order_net_flow / auction_gap / momentum_acceleration / earnings_surprise / insider_trade → alpha
  - Layer B (风控): pledge_risk / volume_anomaly_zscore / disclosure_timing(推迟) → 风险预警
  - Layer C (执行): m1_m2_scissors / northbound_momentum → 仓位调节外生变量

详见 docs/qianzhan.md.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib import parse, request
from zoneinfo import ZoneInfo

import pandas as pd

CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_TS_PATH = "/home/project/tushare-downloader/tushare_data_v2"
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")
MEASUREMENT = "lean_ashare_forward_factor"


def _read_ts_code_table(root: str, name: str, ts_code: str) -> pd.DataFrame:
    p = Path(root) / name / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    return df


def _read_date_table(root: str, name: str, trade_date: str) -> pd.DataFrame:
    p = Path(root) / name / f"trade_date={trade_date}" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def _read_glob(root: str, name: str) -> pd.DataFrame:
    fs = list(Path(root).glob(f"{name}/**/*.parquet"))
    if not fs:
        return pd.DataFrame()
    try:
        return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    except Exception:
        return pd.DataFrame()


def _read_cn_m(root: str) -> pd.DataFrame:
    fs = sorted(Path(root).glob("cn_m/**/*.parquet"))
    if not fs:
        return pd.DataFrame()
    df = pd.read_parquet(fs[-1])
    df["month"] = df["month"].astype(str)
    return df.sort_values("month")


def _read_hsgt(root: str) -> pd.DataFrame:
    fs = sorted(Path(root).glob("moneyflow_hsgt/**/*.parquet"))
    if not fs:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    return df.sort_values("trade_date") if "trade_date" in df.columns else df


def compute_big_order_net_flow(mf: pd.Series) -> float | None:
    try:
        buy_lg = float(mf.get("buy_lg_amount", 0) or 0)
        buy_elg = float(mf.get("buy_elg_amount", 0) or 0)
        sell_lg = float(mf.get("sell_lg_amount", 0) or 0)
        sell_elg = float(mf.get("sell_elg_amount", 0) or 0)
        amount = float(mf.get("amount", 0) or 0)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    rate = (buy_lg + buy_elg - sell_lg - sell_elg) / amount
    return max(-1.0, min(1.0, rate))


def compute_northbound_momentum(hsgt: pd.DataFrame, trade_date: str, window: int = 3) -> float | None:
    if hsgt.empty or "north_money" not in hsgt.columns:
        return None
    recent = hsgt[hsgt["trade_date"] <= trade_date].sort_values("trade_date").tail(window)
    if recent.empty:
        return None
    series = recent["north_money"].astype(float).tolist()
    mean_abs = sum(abs(x) for x in series) / len(series)
    if mean_abs <= 0:
        return 0.0
    mom = series[-1] / mean_abs
    return max(-5.0, min(5.0, mom))


def compute_auction_gap(auc: pd.Series, prev_close: float) -> float | None:
    if prev_close <= 0:
        return None
    o = auc.get("open")
    if o is None or pd.isna(o):
        return None
    gap = float(o) / prev_close - 1.0
    return max(-0.2, min(0.2, gap))


def compute_momentum_acceleration(bak: pd.Series) -> float | None:
    def _sig(x: float) -> float:
        return 1.0 / (1.0 + math.exp(-x / 10.0))
    try:
        attack = float(bak.get("attack", 0) or 0)
        strength = float(bak.get("strength", 0) or 0)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, 0.5 * _sig(attack) + 0.5 * _sig(strength)))


def compute_volume_anomaly_zscore(bak_df: pd.DataFrame) -> dict[str, float]:
    if bak_df.empty or "vol_ratio" not in bak_df.columns:
        return {}
    vr = bak_df["vol_ratio"].astype(float)
    mean, std = vr.mean(), vr.std()
    if std <= 0 or math.isnan(std):
        return {}
    z = (vr - mean) / std
    return {tc: max(-5.0, min(5.0, v)) for tc, v in zip(bak_df["ts_code"], z) if not math.isnan(v)}


def compute_earnings_surprise(express: pd.DataFrame, forecast: pd.DataFrame, ts_code: str) -> float | None:
    if express.empty or forecast.empty:
        return None
    exp = express[express["ts_code"] == ts_code]
    if exp.empty or "net_profit" not in exp.columns:
        return None
    exp_np = exp.iloc[-1].get("net_profit")
    if exp_np is None or pd.isna(exp_np):
        return None
    fc = forecast[forecast["ts_code"] == ts_code]
    if fc.empty:
        return None
    r = fc.iloc[-1]
    fc_min, fc_max = r.get("net_profit_min"), r.get("net_profit_max")
    if fc_min is None or fc_max is None or pd.isna(fc_min) or pd.isna(fc_max):
        return None
    mid = (float(fc_min) + float(fc_max)) / 2.0
    if abs(mid) < 1.0:
        return 0.0
    surprise = (float(exp_np) - mid) / abs(mid)
    return max(-5.0, min(5.0, surprise))


def compute_disclosure_timing(disc: pd.DataFrame, ts_code: str) -> float | None:
    if disc.empty:
        return None
    row = disc[disc["ts_code"] == ts_code]
    if row.empty:
        return None
    r = row.iloc[-1]
    pre, actual = r.get("pre_date"), r.get("actual_date")
    if pre is None or actual is None or pd.isna(pre) or pd.isna(actual):
        return None
    try:
        pre_d = datetime.strptime(str(pre)[:8], "%Y%m%d")
        actual_d = datetime.strptime(str(actual)[:8], "%Y%m%d")
    except ValueError:
        return None
    days = (pre_d - actual_d).days
    return max(-90.0, min(90.0, float(days)))


def compute_insider_trade(holder: pd.DataFrame, ts_code: str, circ_mv: float) -> float | None:
    if holder.empty or circ_mv <= 0:
        return None
    rows = holder[holder["ts_code"] == ts_code]
    if rows.empty:
        return 0.0
    buy_amt = sell_amt = 0.0
    for _, r in rows.iterrows():
        in_de = str(r.get("in_de", "")).upper()
        vol = float(r.get("change_vol", 0) or 0)
        price = float(r.get("avg_price", 0) or 0)
        amt = abs(vol) * price
        if in_de == "BUY":
            buy_amt += amt
        elif in_de == "SELL":
            sell_amt += amt
    ratio = (buy_amt - sell_amt) / (circ_mv * 10000.0)
    return max(-0.2, min(0.2, ratio))


def compute_pledge_risk(pledge: pd.DataFrame, ts_code: str, close: float, liq_ratio: float = 1.5) -> float | None:
    if pledge.empty or close <= 0:
        return None
    rows = pledge[pledge["ts_code"] == ts_code]
    if rows.empty:
        return 0.0
    r = rows.iloc[-1]
    pr = r.get("pledge_ratio")
    if pr is None or pd.isna(pr):
        return None
    ratio = max(0.0, min(1.0, float(pr) / 100.0))
    liq_price = close / liq_ratio
    distance = close / liq_price - 1.0
    proximity = 1.0 if distance <= 0 else max(0.0, 1.0 - distance / 0.5)
    return max(0.0, min(1.0, ratio * proximity))


def compute_m1_m2_scissors(cn_m: pd.DataFrame, month: str | None = None) -> float | None:
    if cn_m.empty:
        return None
    df = cn_m[cn_m["month"] <= month] if month else cn_m
    if df.empty:
        return None
    r = df.iloc[-1]
    m1, m2 = r.get("m1_yoy"), r.get("m2_yoy")
    if m1 is None or m2 is None or pd.isna(m1) or pd.isna(m2):
        return None
    return max(-15.0, min(15.0, float(m1) - float(m2)))


def compute_theme_heat(limit_list: pd.DataFrame) -> float | None:
    if limit_list.empty:
        return None
    count = len(limit_list)
    boards = []
    for s in limit_list.get("up_stat", []):
        if pd.isna(s):
            continue
        s = str(s)
        if "板" in s:
            try:
                boards.append(float(s.split("板")[0][-1]))
            except (ValueError, IndexError):
                pass
    avg_b = sum(boards) / len(boards) if boards else 1.0
    heat = math.log(1 + count) * avg_b
    return max(0.0, min(20.0, heat))


def compute_convert_premium(cb: pd.DataFrame, ts_code: str) -> float | None:
    if cb.empty:
        return None
    rows = cb[cb["ts_code"] == ts_code]
    if rows.empty or "convert_val" not in rows.columns:
        return None
    v = rows.iloc[-1]["convert_val"]
    if v is None or pd.isna(v):
        return None
    return max(-100.0, min(100.0, float(v)))


def _ts_ns(trade_date: str) -> int:
    local = datetime(int(trade_date[0:4]), int(trade_date[4:6]), int(trade_date[6:8]), 15, 0, 0, tzinfo=CHINA_TZ)
    return int(local.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def to_line(ts_code: str, trade_date: str, factors: dict[str, float]) -> str:
    fields = [f"{k}={v:.12g}" for k, v in factors.items()
              if v is not None and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))]
    if not fields:
        return ""
    return f"{MEASUREMENT},ts_code={ts_code},trade_date={trade_date} {','.join(fields)} {_ts_ns(trade_date)}"


def write_influx(lines: Iterable[str], url: str, org: str, bucket: str, token: str) -> int:
    payload = [l for l in lines if l]
    if not payload:
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


def run(trade_date: str, ts_path: str = DEFAULT_TS_PATH,
        underlyings: list[str] | None = None, dry_run: bool = False) -> dict:
    bak_day = _read_date_table(ts_path, "bak_daily", trade_date)
    if underlyings is None:
        underlyings = bak_day["ts_code"].astype(str).tolist() if not bak_day.empty else []

    limit_day = _read_date_table(ts_path, "limit_list_d", trade_date)
    cn_m = _read_cn_m(ts_path)
    hsgt = _read_hsgt(ts_path)
    pledge = _read_glob(ts_path, "pledge_stat")
    holder = _read_glob(ts_path, "stk_holdertrade")
    express = _read_glob(ts_path, "express")
    forecast = _read_glob(ts_path, "forecast") if Path(ts_path, "forecast").exists() else pd.DataFrame()
    disc = _read_glob(ts_path, "disclosure_date")
    cb = _read_glob(ts_path, "cb_share")

    vol_z = compute_volume_anomaly_zscore(bak_day)
    theme_heat = compute_theme_heat(limit_day)
    m1m2 = compute_m1_m2_scissors(cn_m)

    lines = []
    for tc in underlyings:
        factors: dict[str, float] = {}

        mf = _read_ts_code_table(ts_path, "moneyflow", tc)
        if not mf.empty:
            row = mf[mf["trade_date"] <= trade_date]
            if not row.empty:
                v = compute_big_order_net_flow(row.iloc[-1])
                if v is not None:
                    factors["big_order_net_flow"] = v

        nb = compute_northbound_momentum(hsgt, trade_date)
        if nb is not None:
            factors["northbound_momentum"] = nb

        auc_day = _read_date_table(ts_path, "stk_auction_o", trade_date)
        if not auc_day.empty:
            ar = auc_day[auc_day["ts_code"] == tc]
            if not ar.empty:
                br = bak_day[bak_day["ts_code"] == tc]
                pc = float(br.iloc[-1]["pre_close"]) if not br.empty and "pre_close" in br.columns else 0.0
                g = compute_auction_gap(ar.iloc[-1], pc)
                if g is not None:
                    factors["auction_gap"] = g

        if not bak_day.empty:
            br = bak_day[bak_day["ts_code"] == tc]
            if not br.empty:
                a = compute_momentum_acceleration(br.iloc[-1])
                if a is not None:
                    factors["momentum_acceleration"] = a

        if tc in vol_z:
            factors["volume_anomaly_zscore"] = vol_z[tc]

        es = compute_earnings_surprise(express, forecast, tc)
        if es is not None:
            factors["earnings_surprise"] = es

        dt = compute_disclosure_timing(disc, tc)
        if dt is not None:
            factors["disclosure_timing"] = dt

        br = bak_day[bak_day["ts_code"] == tc] if not bak_day.empty else pd.DataFrame()
        circ_mv = float(br.iloc[-1]["float_mv"]) if not br.empty and "float_mv" in br.columns else 0.0
        ins = compute_insider_trade(holder, tc, circ_mv)
        if ins is not None:
            factors["insider_trade"] = ins

        close = float(br.iloc[-1]["close"]) if not br.empty and "close" in br.columns else 0.0
        pr = compute_pledge_risk(pledge, tc, close)
        if pr is not None:
            factors["pledge_risk"] = pr

        if m1m2 is not None:
            factors["m1_m2_scissors"] = m1m2
        if theme_heat is not None:
            factors["theme_heat"] = theme_heat

        cp = compute_convert_premium(cb, tc)
        if cp is not None:
            factors["convert_premium"] = cp

        if factors:
            lines.append(to_line(tc, trade_date, factors))

    written = 0
    if dry_run:
        print(f"[dry-run] {len(lines)} factor rows for {trade_date}")
        if lines:
            print("sample:", lines[0][:200])
    else:
        token = DEFAULT_INFLUX_TOKEN
        if token and lines:
            try:
                written = write_influx(lines, DEFAULT_INFLUX_URL, DEFAULT_INFLUX_ORG, DEFAULT_INFLUX_BUCKET, token)
            except Exception as e:
                print(f"InfluxDB write failed: {e}", file=sys.stderr)

    return {"trade_date": trade_date, "symbols": len(underlyings), "rows": len(lines), "written": written}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Forward-looking factor exporter (docs/qianzhan.md)")
    p.add_argument("--trade-date", required=True, help="YYYYMMDD")
    p.add_argument("--tushare-data-path", default=DEFAULT_TS_PATH)
    p.add_argument("--symbols", help="comma-separated ts_code (default: all bak_daily of the day)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    underlyings = args.symbols.split(",") if args.symbols else None
    summary = run(args.trade_date, args.tushare_data_path, underlyings, args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
