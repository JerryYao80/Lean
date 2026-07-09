#!/usr/bin/env python3
"""gold2 三曲线对比脚本(plan Task 12, spec §7.2)。

对比 baseline / vol_only / full 三条净值曲线,主指标为最大回撤改善。
- baseline: 满仓持有 518880(取 tushare fund_daily 518880.SH close,与回测同日历)
- vol_only: config-gold2-volonly.json 回测(trend-floor=1.0,趋势层禁用 → 纯波动目标)
- full:     config-gold2-beta-vol-target-backtest.json 回测(完整模型)

判定线(spec §7.2 有效性 Gate):
    full 最大回撤 < baseline×0.85(改善 ≥ 15%)→ EFFECTIVE
    否则 → EFFECTIVENESS_FAIL

输出:
- 打印对比表到 stdout
- 写 Results/gold2-betavol/comparison.csv(模式 | 年化收益 | 最大回撤 | 回撤持续 | Sharpe | 调仓次数/年)
- 打印 verdict 行: VERDICT: EFFECTIVE | full_max_dd=.. | baseline_max_dd=.. | improvement=..%

LEAN result JSON 结构(已核实 Results/gold2-betavol/*.json):
  charts."Strategy Equity".series.Equity.values = [[unix_ts, o, h, l, close], ...]
  日度采样(close 即当日 equity)。部分 series(如 Benchmark)为 [ts, value] 二元组,loader 兼容。
  statistics."Total Orders" 给调仓次数。
"""
from __future__ import annotations
import os, sys, json
from typing import Optional, Tuple
import numpy as np
import pandas as pd

RESULTS = "/home/project/hope/Lean/Results"
TUSHARE_518880 = "/home/project/tushare-downloader/tushare_data_v2/fund_daily/ts_code=518880.SH/data.parquet"
BACKTEST_START = "2020-01-01"
BACKTEST_END = "2026-06-23"
TRADING_DAYS = 252
IMPROVEMENT_THRESHOLD = 0.15  # spec §7.2: full < baseline×0.85 → 改善 ≥ 15%


def compute_stats(eq: pd.Series) -> dict:
    """计算年化收益、最大回撤、回撤持续(天)、Sharpe。

    eq: 日度 equity 序列(单调递增时间索引,值=组合净值)。
    返回 dict(ann_ret, max_dd, dd_duration, sharpe)。max_dd 为负小数(如 -0.165)。
    """
    eq = eq.astype(float).dropna()
    if len(eq) < 2:
        return dict(ann_ret=0.0, max_dd=0.0, dd_duration=0, sharpe=0.0)
    rets = eq.pct_change().dropna()
    peak = eq.cummax()
    dd = (eq - peak) / peak  # 负小数
    max_dd = float(dd.min()) if len(dd) else 0.0
    # 回撤持续: 连续处于水下(dd<0)的最长天数(逐 bar 计数,0 表示从未水下)
    in_dd = (dd < 0).values
    dur = cur = 0
    for v in in_dd:
        cur = cur + 1 if v else 0
        dur = max(dur, cur)
    n = len(eq)
    ann_ret = float((eq.iloc[-1] / eq.iloc[0]) ** (TRADING_DAYS / n) - 1) if eq.iloc[0] > 0 else 0.0
    sharpe = float(rets.mean() / rets.std() * np.sqrt(TRADING_DAYS)) if rets.std() > 0 else 0.0
    return dict(ann_ret=ann_ret, max_dd=max_dd, dd_duration=int(dur), sharpe=sharpe)


def verdict(baseline_max_dd: float, full_max_dd: float) -> str:
    """spec §7.2 有效性 Gate。

    baseline_max_dd/full_max_dd 均为负小数(如 -0.20)。回撤越小(绝对值越小)越好。
    full < baseline×0.85 ⟺ |full| < |baseline|×0.85 ⟺ 改善比例 ≥ 15%。
    边界(恰好 15%)算 EFFECTIVE(包含)。
    """
    if baseline_max_dd >= 0:
        # baseline 无回撤:full 只要非正即视为不劣化(effectively pass-through)
        return "EFFECTIVE" if full_max_dd <= 0 else "EFFECTIVENESS_FAIL"
    improvement = (abs(baseline_max_dd) - abs(full_max_dd)) / abs(baseline_max_dd)
    return "EFFECTIVE" if improvement >= IMPROVEMENT_THRESHOLD else "EFFECTIVENESS_FAIL"


def load_equity_series(folder: str) -> Optional[pd.Series]:
    """从 LEAN 结果目录读 Strategy Equity 日度 close 序列。

    folder: Results 下的子目录(如 gold2-betavol)。扫 *.json 找含 "Strategy Equity"
    chart 的文件,取 Equity series 的 close(5 元 candle 的最后一个元素;2 元点取第二个)。
    返回 pd.Series(index=DateTime, values=close);无数据返回 None。
    """
    folder_path = os.path.join(RESULTS, folder) if not os.path.isabs(folder) else folder
    if not os.path.isdir(folder_path):
        return None
    import datetime as _dt
    for fname in sorted(os.listdir(folder_path)):
        if not fname.endswith(".json"):
            continue
        # 跳过 order-events / summary / data-monitor(无 charts 或结构不同)
        if "order-events" in fname or "summary" in fname or "data-monitor" in fname:
            continue
        fpath = os.path.join(folder_path, fname)
        try:
            with open(fpath) as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        charts = d.get("charts") if isinstance(d, dict) else None
        if not charts:
            continue
        se = charts.get("Strategy Equity")
        if not se:
            continue
        series = se.get("series", {})
        eq_series = series.get("Equity")
        if not eq_series:
            continue
        vals = eq_series.get("values", [])
        if not vals:
            continue
        times, closes = [], []
        for pt in vals:
            if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                continue
            ts = pt[0]
            close = pt[-1]  # 5 元 candle [ts,o,h,l,close] → close;2 元 [ts,v] → v
            try:
                dt = _dt.datetime.utcfromtimestamp(int(ts)).date()
            except (ValueError, OSError, TypeError):
                continue
            times.append(pd.Timestamp(dt))
            closes.append(float(close))
        if not closes:
            return None
        s = pd.Series(closes, index=pd.DatetimeIndex(times))
        # 去重(取最后一条),按时间排序
        s = s[~s.index.duplicated(keep="last")].sort_index()
        return s
    return None


def load_total_orders(folder: str) -> int:
    """从 summary.json 读 Total Orders(调仓次数)。失败返回 0。"""
    folder_path = os.path.join(RESULTS, folder) if not os.path.isabs(folder) else folder
    if not os.path.isdir(folder_path):
        return 0
    for fname in sorted(os.listdir(folder_path)):
        if "summary" not in fname or not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder_path, fname)) as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        stats = d.get("statistics", {}) if isinstance(d, dict) else {}
        v = stats.get("Total Orders", "0")
        try:
            return int(str(v).replace(",", ""))
        except ValueError:
            return 0
    return 0


def load_baseline_equity() -> Optional[pd.Series]:
    """baseline = 满仓持有 518880。从 tushare fund_daily 518880.SH 取 close,
    归一化为 1_000_000 起始净值,与回测日历对齐(2020-01-01..2026-06-23)。

    spec §7.2: baseline 不单独跑回测,直接用标的累计净值。
    """
    if not os.path.exists(TUSHARE_518880):
        print(f"WARN: baseline tushare parquet 不存在: {TUSHARE_518880}", file=sys.stderr)
        return None
    try:
        df = pd.read_parquet(TUSHARE_518880)
    except Exception as e:
        print(f"WARN: 读 baseline parquet 失败: {e}", file=sys.stderr)
        return None
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df = df[(df["date"] >= BACKTEST_START) & (df["date"] <= BACKTEST_END)]
    df = df.sort_values("date").set_index("date")
    close = df["close"].astype(float)
    if len(close) < 2:
        return None
    # 归一化: 起始 1_000_000,与 strategy equity 起点一致
    baseline = close / close.iloc[0] * 1_000_000.0
    return baseline


def main() -> int:
    baseline = load_baseline_equity()
    volonly = load_equity_series("gold2-volonly")
    full = load_equity_series("gold2-betavol")

    rows = []
    if baseline is not None:
        s = compute_stats(baseline)
        s["mode"] = "baseline"
        s["rebalances_per_yr"] = "-"
        rows.append(s)
    else:
        print("baseline: 无 518880 数据")
    if volonly is not None:
        s = compute_stats(volonly)
        s["mode"] = "vol_only"
        n_orders = load_total_orders("gold2-volonly")
        years = max(1, len(volonly) / TRADING_DAYS)
        s["rebalances_per_yr"] = round(n_orders / years, 1)
        rows.append(s)
    else:
        print("vol_only: 无 equity 数据(先跑 config-gold2-volonly.json)")
    if full is not None:
        s = compute_stats(full)
        s["mode"] = "full"
        n_orders = load_total_orders("gold2-betavol")
        years = max(1, len(full) / TRADING_DAYS)
        s["rebalances_per_yr"] = round(n_orders / years, 1)
        rows.append(s)
    else:
        print("full: 无 equity 数据(先跑 config-gold2-beta-vol-target-backtest.json)")

    if not rows:
        print("ERROR: 无任何曲线可对比", file=sys.stderr)
        return 1

    out = pd.DataFrame(rows)[["mode", "ann_ret", "max_dd", "dd_duration", "sharpe", "rebalances_per_yr"]]
    # 格式化百分比
    disp = out.copy()
    for col in ("ann_ret", "max_dd"):
        disp[col] = (disp[col].astype(float) * 100).round(2).astype(str) + "%"
    for col in ("sharpe",):
        disp[col] = disp[col].astype(float).round(3)
    print(disp.to_string(index=False))

    # 写 comparison.csv(spec §7.2 要求落地)
    os.makedirs(os.path.join(RESULTS, "gold2-betavol"), exist_ok=True)
    out.to_csv(os.path.join(RESULTS, "gold2-betavol", "comparison.csv"), index=False)
    print(f"\ncomparison.csv -> {os.path.join(RESULTS, 'gold2-betavol', 'comparison.csv')}")

    # verdict
    if baseline is not None and full is not None:
        b_dd = out.loc[out["mode"] == "baseline", "max_dd"].iloc[0]
        f_dd = out.loc[out["mode"] == "full", "max_dd"].iloc[0]
        v = verdict(b_dd, f_dd)
        if b_dd < 0:
            improvement = (abs(b_dd) - abs(f_dd)) / abs(b_dd) * 100
        else:
            improvement = 0.0
        print(f"\nVERDICT: {v} | baseline_max_dd={b_dd*100:.2f}% | full_max_dd={f_dd*100:.2f}% | "
              f"improvement={improvement:.1f}% (threshold 15%)")
        if v == "EFFECTIVENESS_FAIL":
            print("EFFECTIVENESS_FAIL: full 最大回撤未达 baseline×0.85,记录诊断(spec §7.2 不强制 kill)。")
    else:
        print("\nVERDICT: INCONCLUSIVE (baseline 或 full 数据缺失,无法判定)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
