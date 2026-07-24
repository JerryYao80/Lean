#!/usr/bin/env python3
"""黄金 ETF (518880) 隔夜溢价 T+0 信号导出器。

离线算 Z_signal + regime + skip_reason，写 parquet。运行时零 API 调用。
设计依据: docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md §3, §4。
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROLLING_WINDOW = 60
CROSS_CHECK_THRESHOLD = 0.015  # |R_au - R_fxcm| > 1.5% 且方向相反 -> CROSS_CHECK_FAIL


def compute_signals(
    au: pd.DataFrame,
    etf: pd.DataFrame,
    fxcm: Optional[pd.DataFrame] = None,
    fred_df: Optional[pd.DataFrame] = None,
    min_history: int = ROLLING_WINDOW,
) -> pd.DataFrame:
    """算隔夜溢价信号。

    Args:
        au: AU.SHF fut_daily，含 trade_date(str YYYYMMDD)/pre_close/open/close。
        etf: 518880 fund_daily，含 trade_date/pre_close/open/close。
        fxcm: XAUUSD.FXCM fx_daily（交叉校验，可选），含 trade_date/bid_close。
        fred_df: DFII10 序列（可选）。None -> regime=UNAVAILABLE。
        min_history: Z-score 滚动窗口。

    Returns: DataFrame, 每 trade_date 一行，列:
        trade_date, r_au_overnight, gap_expected, gap_actual, signal,
        z_signal, regime, skip_reason, freshness_flag, cross_check_alert
    """
    au = au.sort_values("trade_date").copy()
    etf = etf.sort_values("trade_date").copy()
    au["trade_date"] = au["trade_date"].astype(str)
    etf["trade_date"] = etf["trade_date"].astype(str)

    # 对齐到 518880 的交易日（A股交易日历）
    merged = etf[["trade_date", "pre_close", "open", "close"]].rename(
        columns={"pre_close": "etf_pre_close", "open": "etf_open", "close": "etf_close"}
    ).merge(
        au[["trade_date", "pre_close", "open", "close"]].rename(
            columns={"pre_close": "au_pre_close", "open": "au_open", "close": "au_close"}
        ),
        on="trade_date", how="left"
    )

    # ---- 信号数学 (§3.1) ----
    merged["r_au_overnight"] = np.where(
        merged["au_pre_close"] > 0,
        merged["au_open"] / merged["au_pre_close"] - 1.0,
        np.nan,
    )
    merged["gap_expected"] = merged["r_au_overnight"]
    merged["gap_actual"] = np.where(
        merged["etf_pre_close"] > 0,
        merged["etf_open"] / merged["etf_pre_close"] - 1.0,
        np.nan,
    )
    merged["signal"] = merged["gap_expected"] - merged["gap_actual"]

    # ---- Z-score 滚动 (§3.1) ----
    s = merged["signal"]
    roll_mean = s.rolling(window=min_history, min_periods=min_history).mean()
    roll_std = s.rolling(window=min_history, min_periods=min_history).std(ddof=0)
    merged["z_signal"] = np.where(
        roll_std > 1e-12,
        (s - roll_mean) / roll_std.replace(0, np.nan),
        np.nan,
    )

    # ---- regime (§4.1) ----
    if fred_df is None or len(fred_df) == 0:
        merged["regime"] = "UNAVAILABLE"
    else:
        merged["regime"] = _classify_regime(fred_df, merged["trade_date"])

    # ---- 交叉校验 (§3.3) ----
    merged["cross_check_alert"] = False
    if fxcm is not None and len(fxcm):
        fx = fxcm.sort_values("trade_date").copy()
        fx["trade_date"] = fx["trade_date"].astype(str)
        fx["r_fxcm"] = fx["bid_close"].pct_change()
        merged = merged.merge(fx[["trade_date", "r_fxcm"]], on="trade_date", how="left")
        dir_opp = (merged["r_au_overnight"] * merged["r_fxcm"] < 0)
        mag_div = (merged["r_au_overnight"] - merged["r_fxcm"]).abs() > CROSS_CHECK_THRESHOLD
        merged["cross_check_alert"] = dir_opp & mag_div

    # ---- skip_reason 正交字段 (§4.2) ----
    def _skip(row):
        if pd.isna(row["au_open"]) or pd.isna(row["au_pre_close"]):
            return "DATA_STALE"
        if pd.isna(row["z_signal"]):
            return "INSUFFICIENT_HISTORY"
        if row.get("cross_check_alert", False):
            return "CROSS_CHECK_FAIL"
        if abs(row["z_signal"]) <= 1.5:
            return "NO_EDGE"
        return "NONE"
    merged["skip_reason"] = merged.apply(_skip, axis=1)

    # ---- 新鲜度标志 (§1.3 约束4, 实盘语义；回测恒 True) ----
    merged["freshness_flag"] = True

    cols = ["trade_date", "r_au_overnight", "gap_expected", "gap_actual", "signal",
            "z_signal", "regime", "skip_reason", "freshness_flag", "cross_check_alert"]
    return merged[cols]


def _classify_regime(fred_df: pd.DataFrame, trade_dates: pd.Series) -> pd.Series:
    """DFII10 5d/20d 速率分类。当前 FRED 未配置时不会被调用。
    阈值校准留 OOS 阶段（design §6.3）；当前示意值 10bp/5bp。"""
    f = fred_df.sort_values("date").copy()
    f["d5"] = f["value"].diff(5)
    f["d20"] = f["value"].diff(20)
    def _cls(r):
        if pd.isna(r["d5"]) or pd.isna(r["d20"]):
            return "UNAVAILABLE"
        if r["d5"] > 0.10 and r["d20"] > 0:
            return "RISING_FAST"
        if r["d5"] < -0.10 and r["d20"] < 0:
            return "FALLING_FAST"
        if abs(r["d20"]) <= 0.05:
            return "STABLE"
        return "DRIFTING"
    f["regime"] = f.apply(_cls, axis=1)
    out = pd.Series("UNAVAILABLE", index=trade_dates.index)
    f["td"] = f["date"].dt.strftime("%Y%m%d")
    m = pd.DataFrame({"trade_date": trade_dates, "_i": trade_dates.index}).merge(
        f[["td", "regime"]], left_on="trade_date", right_on="td", how="left"
    )
    out.loc[m["_i"].values] = m["regime"].fillna("UNAVAILABLE").values
    return out


def load_au(tushare_path: str) -> pd.DataFrame:
    import glob, pyarrow.parquet as pq
    # AU.SHF stored under ts_code=AU.SHF partition (per-year chunk merge happened at backfill)
    p = Path(tushare_path) / "fut_daily" / "ts_code=AU.SHF" / "data.parquet"
    if p.exists():
        return pd.read_parquet(p)
    # fallback: scan year partitions (legacy layout)
    parts = sorted(glob.glob(f"{tushare_path}/fut_daily/year=*/data.parquet"))
    frames = []
    for f in parts:
        t = pq.read_table(f)
        cols = [c for c in t.column_names if not c.startswith("__")]
        frames.append(t.select(cols).to_pandas())
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    return df[df["ts_code"] == "AU.SHF"].copy()


def load_etf(tushare_path: str) -> pd.DataFrame:
    p = Path(tushare_path) / "fund_daily" / "ts_code=518880.SH" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def load_fxcm(tushare_path: str) -> pd.DataFrame:
    import glob, pyarrow.parquet as pq
    parts = sorted(glob.glob(f"{tushare_path}/fx_daily/year=*/data.parquet"))
    frames = []
    for f in parts:
        t = pq.read_table(f)
        cols = [c for c in t.column_names if not c.startswith("__")]
        frames.append(t.select(cols).to_pandas())
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    return df[df["ts_code"] == "XAUUSD.FXCM"][["trade_date", "bid_close"]].copy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tushare-data-path", default="/home/project/tushare-downloader/tushare_data_v2")
    ap.add_argument("--out", default=None)
    ap.add_argument("--start-date", default="20200101")
    ap.add_argument("--end-date", default="20260623")
    args = ap.parse_args()

    out_dir = Path(args.out or Path(__file__).resolve().parents[1] / "Data" / "alternative" / "gold-overnight-premium")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "signals.parquet"

    au = load_au(args.tushare_data_path)
    etf = load_etf(args.tushare_data_path)
    fxcm = load_fxcm(args.tushare_data_path)
    if au.empty or etf.empty:
        raise SystemExit(f"Data missing: AU rows={len(au)}, ETF rows={len(etf)}")

    sig = compute_signals(au, etf, fxcm=fxcm, fred_df=None)
    sig = sig[(sig["trade_date"] >= args.start_date) & (sig["trade_date"] <= args.end_date)]
    sig.to_parquet(out_path, index=False)
    print(f"[gold-overnight-premium] Wrote {len(sig)} rows to {out_path}")
    print(f"  skip_reason dist: {sig['skip_reason'].value_counts().to_dict()}")
    print(f"  regime dist: {sig['regime'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()
