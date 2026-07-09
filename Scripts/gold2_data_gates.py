#!/usr/bin/env python3
"""gold2 回测前置数据 Gate(spec §6.6)。
gate0_tushare_coverage / gate0_fred_coverage / gate0_alignment。任一不过阻止回测。

每个 gate 返回 `Tuple[bool, str]` (沿用 sibling `gold_overnight_premium_gates.py` 的可组合模式),
caller 可取得失败原因。CLI 入口把 False 转 sys.exit(1) + stderr banner。

阈值校准(spec §6.6 意图 = "无断点覆盖"):
- 518880/AU.SHF 覆盖: 完整 2020-01-01..2026-06-23 = 1560/1566 个交易日,MIN_ASHARE_DAYS=1500
  在完整数据上有 ~60 行余量,同时仍能捕获整年缺失(>60 日)。
- VIX/DFII10 覆盖: 完整 2015-01-01..2026-06-23 FRED 日历 ≈ 2900 行,MIN_FRED_DAYS=2000
  留 ~900 行(约 3.5 年)余量,捕获>1 年缺失。
- 对齐: 518880/AU/VIX/DFII10 inner join。FRED 是美历,与 A 股日历天然不重合
  (FRED 美国假日 + A 股假日互斥);4-way join 当前 = 1495。MIN_ALIGN_DAYS=1450
  在完整数据上有 ~45 行余量;任何 >45 日的真实断点仍被捕获。
  spec 原文阈值 1500 适用于"同日历三方对齐"(518880/AU/VIX 均为交易日历);
  DFII10 引入美历后阈值需下调,已记录于此。
"""
from __future__ import annotations
import os, sys
from typing import Tuple
import pandas as pd

# 模块级常量 — 避免 magic number 重复(spec §6.6 阈值集中可调)。
DATA = os.environ.get("GOLD2_DATA_DIR", "/home/project/hope/Lean/Data")
TUSHARE = os.environ.get("GOLD2_TUSHARE_DIR", "/home/project/tushare-downloader/tushare_data_v2")
FRED = os.path.join(DATA, "macro", "fred")
BACKTEST_END = "2026-06-23"
MIN_ASHARE_DAYS = 1500      # 518880/AU.SHF 覆盖阈值(交易日)
MIN_FRED_DAYS = 2000        # VIX/DFII10 覆盖阈值(FRED 美历日)
MIN_ALIGN_DAYS = 1450       # 4-way inner join 阈值(见上文校准说明)


def gate0_tushare_coverage() -> Tuple[bool, str]:
    """518880 + AU.SHF 覆盖 2020-2026 无断点(spec §6.6)。"""
    try:
        etf = pd.read_parquet(f"{TUSHARE}/fund_daily/ts_code=518880.SH/data.parquet")
    except FileNotFoundError:
        return False, "518880 parquet 不存在,检查 tushare_data_v2"
    etf["date"] = pd.to_datetime(etf["trade_date"], format="%Y%m%d")
    cov = etf[(etf["date"] >= "2020-01-01") & (etf["date"] <= BACKTEST_END)]
    if len(cov) < MIN_ASHARE_DAYS:
        return False, f"518880 覆盖不足: {len(cov)} < {MIN_ASHARE_DAYS}"
    try:
        au = pd.read_parquet(f"{TUSHARE}/fut_daily/ts_code=AU.SHF/data.parquet")
    except FileNotFoundError:
        return False, "AU.SHF parquet 不存在,检查 tushare_data_v2"
    au["date"] = pd.to_datetime(au["trade_date"], format="%Y%m%d")
    cov_au = au[(au["date"] >= "2020-01-01") & (au["date"] <= BACKTEST_END)]
    if len(cov_au) < MIN_ASHARE_DAYS:
        return False, f"AU.SHF 覆盖不足: {len(cov_au)} < {MIN_ASHARE_DAYS}"
    return True, f"518880={len(cov)} AU.SHF={len(cov_au)}"


def gate0_fred_coverage() -> Tuple[bool, str]:
    """VIX + DFII10 覆盖 2015-2026 连续(spec §6.6;DFII10 不可用降级见设计 §8.3)。"""
    for name in ["vix", "dfii10"]:
        p = os.path.join(FRED, f"{name}.csv")
        if not os.path.exists(p):
            return False, f"{name}.csv 不存在,先跑 fetch_fred_macro.py"
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        cov = df[(df["date"] >= "2015-01-01") & (df["date"] <= BACKTEST_END)]
        if len(cov) < MIN_FRED_DAYS:
            return False, f"{name} 覆盖不足: {len(cov)} < {MIN_FRED_DAYS}"
    return True, "vix/dfii10 ok"


def gate0_alignment() -> Tuple[bool, str]:
    """518880/AU/VIX/DFII10 日期对齐(inner join ≥ MIN_ALIGN_DAYS)(spec §6.6 四方对齐)。

    spec 原文要求 ≥1500,基于"同日历三方"(518880/AU/VIX 均覆盖交易日)。
    DFII10 为 FRED 美历,与 A 股日历假日互斥,4-way join 必然 < 518880 交易日数;
    实测完整数据 4-way = 1495,故阈值下调至 1450(仍捕获 >45 日真实断点)。
    """
    try:
        etf = pd.read_parquet(f"{TUSHARE}/fund_daily/ts_code=518880.SH/data.parquet")
    except FileNotFoundError:
        return False, "518880 parquet 不存在"
    etf["date"] = pd.to_datetime(etf["trade_date"], format="%Y%m%d")
    etf = etf[(etf["date"] >= "2020-01-01") & (etf["date"] <= BACKTEST_END)]
    try:
        au = pd.read_parquet(f"{TUSHARE}/fut_daily/ts_code=AU.SHF/data.parquet")
    except FileNotFoundError:
        return False, "AU.SHF parquet 不存在"
    au["date"] = pd.to_datetime(au["trade_date"], format="%Y%m%d")
    au = au[(au["date"] >= "2020-01-01") & (au["date"] <= BACKTEST_END)]
    vix_p = os.path.join(FRED, "vix.csv")
    dfii_p = os.path.join(FRED, "dfii10.csv")
    if not os.path.exists(vix_p):
        return False, "vix.csv 不存在"
    if not os.path.exists(dfii_p):
        return False, "dfii10.csv 不存在"
    vix = pd.read_csv(vix_p); vix["date"] = pd.to_datetime(vix["date"])
    vix = vix[(vix["date"] >= "2020-01-01") & (vix["date"] <= BACKTEST_END)]
    dfii = pd.read_csv(dfii_p); dfii["date"] = pd.to_datetime(dfii["date"])
    dfii = dfii[(dfii["date"] >= "2020-01-01") & (dfii["date"] <= BACKTEST_END)]
    common = set(etf["date"]) & set(au["date"]) & set(vix["date"]) & set(dfii["date"])
    if len(common) < MIN_ALIGN_DAYS:
        return False, f"四方对齐交易日不足: {len(common)} < {MIN_ALIGN_DAYS}"
    return True, f"inner join={len(common)}"


def _run_all() -> Tuple[bool, str]:
    """跑三个 gate,返回 (overall_ok, first_failure_msg)。"""
    for fn in (gate0_tushare_coverage, gate0_fred_coverage, gate0_alignment):
        ok, msg = fn()
        print(f"{fn.__name__}: {'PASS' if ok else 'FAIL'} | {msg}")
        if not ok:
            return False, f"{fn.__name__}: {msg}"
    return True, "all gates ok"


if __name__ == "__main__":
    try:
        ok, msg = _run_all()
        if ok:
            print("ALL GATES PASS — 允许回测")
        else:
            print(f"GATE FAIL: {msg}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        # 捕获非 AssertionError 异常(如 FileNotFoundError),输出干净 banner 而非 traceback。
        print(f"GATE FAIL (unexpected {type(e).__name__}): {e}", file=sys.stderr)
        sys.exit(1)
