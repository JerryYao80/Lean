# data-source/tushare/alpha101/panel_loader.py
"""Load tushare_data_v2 parquet into an Alpha101Panel (wide date×ts_code)."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pandas as pd

DEFAULT_TS_PATH = os.environ.get(
    "TUSHARE_DATA_PATH", "/home/project/tushare-downloader/tushare_data_v2"
)
ADV_HORIZONS = (5, 10, 15, 20, 30, 40, 50, 60, 81, 120, 150, 180)


@dataclass
class Alpha101Panel:
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame        # adjusted
    close_raw: pd.DataFrame    # unadjusted
    volume: pd.DataFrame
    vwap: pd.DataFrame
    returns: pd.DataFrame
    cap: pd.DataFrame
    adv: dict[int, pd.DataFrame]
    industry: pd.DataFrame     # long df: ts_code, l1, l2, l3, in_date, out_date
    dates: list[str]
    asof: str


def _read_partition(data_root: str, table: str, ts_code: str) -> pd.DataFrame:
    p = Path(data_root) / table / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


def _wide_from_long(long_df: pd.DataFrame, ts_codes: list[str], col: str,
                    asof: str, lookback_days: int) -> pd.DataFrame:
    if long_df is None or long_df.empty:
        return pd.DataFrame()
    d = long_df.copy()
    d["trade_date"] = d["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    d = d[d["trade_date"] <= asof].sort_values("trade_date")
    d = d[d["ts_code"].isin(ts_codes)]
    w = d.pivot(index="trade_date", columns="ts_code", values=col)
    w = w.tail(lookback_days)
    return w


def load_panel(ts_codes: list[str], asof: str, data_root: str | None = None,
               lookback_days: int = 270) -> Alpha101Panel | None:
    data_root = data_root or DEFAULT_TS_PATH
    if not ts_codes:
        return None

    o_parts, h_parts, l_parts, c_parts, craw_parts, v_parts, mv_parts = [], [], [], [], [], [], []
    for code in ts_codes:
        daily = _read_partition(data_root, "daily", code)
        adj = _read_partition(data_root, "adj_factor", code)
        basic = _read_partition(data_root, "daily_basic", code)
        if daily.empty:
            continue
        daily["trade_date"] = daily["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
        daily = daily[daily["trade_date"] <= asof]
        if not adj.empty:
            adj["trade_date"] = adj["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
            adj = adj[adj["trade_date"] <= asof]
            daily = daily.merge(adj[["trade_date", "adj_factor"]], on="trade_date", how="left")
        else:
            daily["adj_factor"] = 1.0
        daily["adj_factor"] = daily["adj_factor"].fillna(1.0).astype(float)
        o_parts.append(daily[["trade_date", "ts_code", "open"]].assign(
            open=daily["open"].astype(float) * daily["adj_factor"]))
        h_parts.append(daily[["trade_date", "ts_code", "high"]].assign(
            high=daily["high"].astype(float) * daily["adj_factor"]))
        l_parts.append(daily[["trade_date", "ts_code", "low"]].assign(
            low=daily["low"].astype(float) * daily["adj_factor"]))
        c_parts.append(daily[["trade_date", "ts_code", "close"]].assign(
            close=daily["close"].astype(float) * daily["adj_factor"]))
        craw_parts.append(daily[["trade_date", "ts_code", "close"]].rename(columns={"close": "close_raw"}))
        v_parts.append(daily[["trade_date", "ts_code", "vol", "amount"]])
        if not basic.empty:
            mv_parts.append(basic[["trade_date", "ts_code", "total_mv"]])

    if not c_parts:
        return None
    op = _wide_from_long(pd.concat(o_parts, ignore_index=True), ts_codes, "open", asof, lookback_days)
    hi = _wide_from_long(pd.concat(h_parts, ignore_index=True), ts_codes, "high", asof, lookback_days)
    lo = _wide_from_long(pd.concat(l_parts, ignore_index=True), ts_codes, "low", asof, lookback_days)
    cl = _wide_from_long(pd.concat(c_parts, ignore_index=True), ts_codes, "close", asof, lookback_days)
    craw = _wide_from_long(pd.concat(craw_parts, ignore_index=True), ts_codes, "close_raw", asof, lookback_days)
    long_v = pd.concat(v_parts, ignore_index=True)
    long_v["amount"] = long_v["amount"].astype(float)
    long_v["vol"] = long_v["vol"].astype(float)
    vol = _wide_from_long(long_v, ts_codes, "vol", asof, lookback_days)
    amt = _wide_from_long(long_v, ts_codes, "amount", asof, lookback_days)
    vwap = (amt * 10.0).divide(vol.where(vol != 0))
    rets = cl.pct_change()
    cap = _wide_from_long(pd.concat(mv_parts, ignore_index=True), ts_codes, "total_mv", asof, lookback_days) if mv_parts else cl * 0.0
    adv = {n: amt.rolling(n).mean() for n in ADV_HORIZONS}

    ind_path = Path(data_root) / "index_member_all" / "data.parquet"
    ind_df = pd.DataFrame()
    if ind_path.exists():
        ind_df = pd.read_parquet(ind_path)
        ind_df["ts_code"] = ind_df["ts_code"].astype(str)
        ind_df = ind_df[ind_df["ts_code"].isin(ts_codes)]
        ind_df["in_date"] = ind_df["in_date"].astype(str)
        ind_df["out_date"] = ind_df["out_date"].fillna("29991231").astype(str)

    return Alpha101Panel(
        open=op, high=hi, low=lo, close=cl, close_raw=craw, volume=vol,
        vwap=vwap, returns=rets, cap=cap, adv=adv, industry=ind_df,
        dates=list(cl.index), asof=asof,
    )


def load_csi800_universe(loader, asof_date: str) -> list[str]:
    """000300.SH ∪ 000905.SH, deduped. `loader` exposes load_index_constituents."""
    csi300 = loader.load_index_constituents(asof_date=asof_date, index_code="000300.SH")
    csi500 = loader.load_index_constituents(asof_date=asof_date, index_code="000905.SH")
    return sorted(set(csi300) | set(csi500))
