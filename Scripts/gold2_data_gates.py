#!/usr/bin/env python3
"""gold2 回测前置数据 Gate(spec §6.6)。
gate0_tushare_coverage / gate0_fred_coverage / gate0_alignment。任一不过阻止回测。"""
import os, sys, pandas as pd

DATA = "/home/project/hope/Lean/Data"
TUSHARE = "/home/project/tushare-downloader/tushare_data_v2"
FRED = os.path.join(DATA, "macro", "fred")

def gate0_tushare_coverage():
    etf = pd.read_parquet(f"{TUSHARE}/fund_daily/ts_code=518880.SH/data.parquet")
    etf["date"] = pd.to_datetime(etf["trade_date"], format="%Y%m%d")
    cov = etf[(etf["date"] >= "2020-01-01") & (etf["date"] <= "2026-06-23")]
    # 阈值校准于真实 A 股日历: 2020-01-01..2026-06-23 完整覆盖 = 1560 个交易日(518880)。
    # spec §6.6 意图 = "无断点"; >1500 在完整数据上有 ~60 行余量, 同时仍能捕获整年缺失。
    assert len(cov) > 1500, f"518880 覆盖不足: {len(cov)} < 1500"
    au = pd.read_parquet(f"{TUSHARE}/fut_daily/ts_code=AU.SHF/data.parquet")
    au["date"] = pd.to_datetime(au["trade_date"], format="%Y%m%d")
    cov_au = au[(au["date"] >= "2020-01-01") & (au["date"] <= "2026-06-23")]
    assert len(cov_au) > 1500, f"AU.SHF 覆盖不足: {len(cov_au)} < 1500"
    print(f"gate0_tushare_coverage PASS: 518880={len(cov)} AU.SHF={len(cov_au)}")
    return True

def gate0_fred_coverage():
    for name in ["vix", "dfii10"]:
        p = os.path.join(FRED, f"{name}.csv")
        assert os.path.exists(p), f"{name}.csv 不存在,先跑 fetch_fred_macro.py"
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        cov = df[(df["date"] >= "2015-01-01") & (df["date"] <= "2026-06-23")]
        assert len(cov) > 2000, f"{name} 覆盖不足: {len(cov)} < 2000"
        print(f"gate0_fred_coverage PASS: {name}={len(cov)}")
    return True

def gate0_alignment():
    etf = pd.read_parquet(f"{TUSHARE}/fund_daily/ts_code=518880.SH/data.parquet")
    etf["date"] = pd.to_datetime(etf["trade_date"], format="%Y%m%d")
    etf = etf[(etf["date"] >= "2020-01-01") & (etf["date"] <= "2026-06-23")]
    au = pd.read_parquet(f"{TUSHARE}/fut_daily/ts_code=AU.SHF/data.parquet")
    au["date"] = pd.to_datetime(au["trade_date"], format="%Y%m%d")
    au = au[(au["date"] >= "2020-01-01") & (au["date"] <= "2026-06-23")]
    vix = pd.read_csv(os.path.join(FRED, "vix.csv")); vix["date"] = pd.to_datetime(vix["date"])
    vix = vix[(vix["date"] >= "2020-01-01") & (vix["date"] <= "2026-06-23")]
    common = set(etf["date"]) & set(au["date"]) & set(vix["date"])
    assert len(common) >= 1500, f"三方对齐交易日不足: {len(common)} < 1500"
    print(f"gate0_alignment PASS: inner join={len(common)}")
    return True

if __name__ == "__main__":
    try:
        gate0_tushare_coverage(); gate0_fred_coverage(); gate0_alignment()
        print("ALL GATES PASS — 允许回测")
    except AssertionError as e:
        print(f"GATE FAIL: {e}", file=sys.stderr); sys.exit(1)
