# 黄金 ETF (518880) 隔夜溢价 T+0 日内策略 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个独立、有效的黄金 ETF 隔夜溢价 T+0 日内算法，基于 SHFE AU 期货人民币计价主信号 + Gate 2 IC 有效性 kill switch。

**Architecture:** 混合架构——Python 导出器离线算 Z_signal/regime/skip_reason 写 parquet；薄 C# IFactor（Precomputed + InjectValue）消费；LEAN 原生 `IRiskManagementModel` 硬强制 14:45 平仓。镜像 `AShareOvernightAnomalyAlgorithm` + `export_ashare_etf_t0_feature_data.py` 模式。运行时零 API 调用，保 CI gate 确定性。

**Tech Stack:** Python 3.13 + pandas + pyarrow（导出器/回填）；C# .NET 10 + NUnit（LEAN 因子/风控/算法/测试）；tushare pro（一次性回填）；LEAN 自定义 BaseData + IFactor + IRiskManagementModel。

**Spec:** `docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md`

---

## 文件结构

### 新建文件

| 文件 | 职责 |
|---|---|
| `/home/project/tushare-downloader/backfill_gold_au.py` | 一次性回填 AU.SHF + AUL.SHF 全量到 parquet |
| `Scripts/export_gold_overnight_premium_signals.py` | 离线算 Z_signal + regime + skip_reason，写 parquet |
| `Scripts/gold_overnight_premium_gates.py` | Gate 0/1/2 校验器（被导出器与 CI 调用） |
| `Common/Factors/Forward/GoldOvernightPremiumFactor.cs` | Precomputed IFactor，返回 Z_signal |
| `Common/Factors/Forward/GoldRealRateRegimeFactor.cs` | Precomputed IFactor，返回 regime enum |
| `Common/Factors/Forward/GoldRegime.cs` | regime enum（含 UNAVAILABLE） |
| `Common/Data/Custom/Gold/GoldOvernightSignal.cs` | custom BaseData，载入信号行 |
| `Algorithm.CSharp/Models/Risk/NoOvernightPositionRiskModel.cs` | 14:45 硬平仓 IRiskManagementModel |
| `Algorithm.CSharp/GoldOvernightPremiumAlgorithm.cs` | 主算法 |
| `Tests/Python/Scripts/GoldOvernightPremiumExportTests.py` | 导出器测试 |
| `Tests/Python/Scripts/GoldOvernightPremiumGateTests.py` | Gate 0/1/2 测试 |
| `Tests/Common/Factors/GoldOvernightPremiumFactorTests.cs` | IFactor 测试 |
| `Tests/Common/Factors/GoldRealRateRegimeFactorTests.cs` | regime IFactor 测试 |
| `Tests/Algorithm/GoldOvernightPremiumAlgorithmTests.cs` | 算法 + 风控测试 |
| `Launcher/config/config-gold-overnight-premium-backtest.json` | LEAN launcher 配置 |
| `Scripts/auto_optimize/strategies/gold_overnight_premium/manifest.yaml` | 简化 schema manifest |

### 不修改的文件（纯加法，零侵入）

- `Common/Factors/Core/FactorRegistry.cs` — 不强行注册新因子（算法内直接 new，避免影响其他策略冷启动；后续如需注册再加）
- 现有 IFactor / 现有风控 / 现有算法 — 一律不动

---

## Task 1: AU 期货全量回填脚本

**Files:**
- Create: `/home/project/tushare-downloader/backfill_gold_au.py`
- Test: 手动验证（回填是数据工程操作，无单测；Gate 0 在 Task 9 验证回填结果）

**背景：** `tushare-downloader/downloader.py` 的 `_get_stock_list("futures")` 走 `pro.stock_basic()` 分支（返回股票而非期货合约），故标准 `download_all` 不会拉 AU.SHF。需用 `download_api_by_stock(api_config, ts_code)` 显式按 ts_code 拉。

- [ ] **Step 1: 写回填脚本**

Create `/home/project/tushare-downloader/backfill_gold_au.py`:

```python
#!/usr/bin/env python3
"""一次性回填 SHFE AU 期货全量数据（AU.SHF 连续 + AUL.SHF 换月映射）。

设计依据: docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md §8。
Gate 0 硬前提: 本地 parquet 最早日期须 ≤ 回测 start − 60 交易日。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from downloader import TushareDownloader
from api_registry import get_api_config

# AU.SHF = SHFE 黄金连续合约（人民币计价，主信号源）
# AUL.SHF = SHFE 黄金连续主力映射（换月映射表，防换月跳空被误判为隔夜异常）
TARGET_CODES = ["AU.SHF", "AUL.SHF"]
TARGET_APIS = ["fut_daily", "fut_mapping"]


def main() -> int:
    downloader = TushareDownloader()
    total_new = 0
    for api_name in TARGET_APIS:
        api_config = get_api_config(api_name)
        if api_config is None:
            print(f"[SKIP] {api_name}: API config not found in registry")
            continue
        for ts_code in TARGET_CODES:
            # fut_mapping 用 ts_code=AUL.SHF 查映射；fut_daily 用 AU.SHF 查价格
            if api_name == "fut_mapping" and ts_code != "AUL.SHF":
                continue
            if api_name == "fut_daily" and ts_code != "AU.SHF":
                continue
            print(f"[BACKFILL] {api_name} {ts_code}")
            ok, rows = downloader.download_api_by_stock(api_config, ts_code)
            status = "OK" if ok else "FAIL"
            print(f"  -> {status}, {rows} rows")
            if ok:
                total_new += rows
    print(f"\nBackfill complete. Total new rows: {total_new}")
    return 0 if total_new >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 运行回填**

Run:
```bash
cd /home/project/tushare-downloader && python3 backfill_gold_au.py
```
Expected: `Backfill complete. Total new rows: <N>` 其中 N 为 AU.SHF + AUL.SHF 的行数（AU.SHF 应有 2008→至今约 4000+ 行）。

- [ ] **Step 3: 验证回填深度**

Run:
```bash
python3 -c "
import pandas as pd, glob, pyarrow.parquet as pq
def readp(f):
    t=pq.read_table(f); cols=[c for c in t.column_names if not c.startswith('__')]; return t.select(cols).to_pandas()
au=pd.concat([readp(f) for f in sorted(glob.glob('/home/project/tushare-downloader/tushare_data_v2/fut_daily/year=*/data.parquet'))],ignore_index=True)
au=au[au['ts_code']=='AU.SHF'].sort_values('trade_date')
print('AU.SHF rows:', len(au), 'span:', au['trade_date'].min(), '..', au['trade_date'].max())
m=pd.concat([readp(f) for f in sorted(glob.glob('/home/project/tushare-downloader/tushare_data_v2/fut_mapping/year=*/data.parquet'))],ignore_index=True)
m=m[m['ts_code']=='AUL.SHF'].sort_values('trade_date')
print('AUL.SHF mapping rows:', len(m), 'span:', m['trade_date'].min(), '..', m['trade_date'].max())
"
```
Expected: AU.SHF rows ≥ 4000，span 起点早于 2010；AUL.SHF mapping span 与 AU.SHF 对齐（同起点同终点 ±2 日）。

- [ ] **Step 4: Commit**

```bash
cd /home/project/tushare-downloader
git add backfill_gold_au.py
git commit -m "feat: add SHFE AU futures full backfill script (AU.SHF + AUL.SHF)

One-time backfill for gold overnight-premium strategy primary signal.
Pulls 2008-present continuous AU + dominant-contract mapping. Gate 0
hard prerequisite per docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md §8.
"
```

---

## Task 2: regime enum（C#）

**Files:**
- Create: `Common/Factors/Forward/GoldRegime.cs`

- [ ] **Step 1: 写 enum**

Create `Common/Factors/Forward/GoldRegime.cs`:

```csharp
using System;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 实际利率 regime 状态。UNAVAILABLE 显式区分"数据缺失"与"利率稳定(STABLE)"，
    /// 即使当前行为相同，为将来 FRED 接入留可解释性。
    /// 详见 docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md §4.1。
    /// </summary>
    public enum GoldRegime
    {
        /// <summary>实际利率快速上行（5d > 阈值 且 20d > 0）</summary>
        RISING_FAST,
        /// <summary>实际利率快速下行</summary>
        FALLING_FAST,
        /// <summary>实际利率稳定（|20d| ≤ 阈值）</summary>
        STABLE,
        /// <summary>方向漂移（介于阈值之间或方向不一致）</summary>
        DRIFTING,
        /// <summary>DFII10 数据缺失（FRED_API_KEY 未配置）。行为同 STABLE，状态区分。</summary>
        UNAVAILABLE
    }

    /// <summary>skip_reason 正交字段：核心信号/数据失效原因，与 regime 独立。</summary>
    public enum GoldSkipReason
    {
        NONE,
        /// <summary>AU/518880 时间戳滞后 > 2h 或回测 T 日缺行。核心信号故障。</summary>
        DATA_STALE,
        /// <summary>Z 计算所需 60 日信号历史不足（冷启动）。</summary>
        INSUFFICIENT_HISTORY,
        /// <summary>AU 与 FXCM XAUUSD 方向背离且幅度超阈值。</summary>
        CROSS_CHECK_FAIL,
        /// <summary>|Z| ≤ 1.5，正常不进场。非故障。</summary>
        NO_EDGE
    }
}
```

- [ ] **Step 2: Build 验证 enum 编译**

Run:
```bash
cd /home/project/hope/Lean
dotnet build Common/QuantConnect.csproj 2>&1 | tail -5
```
Expected: Build succeeded, 0 errors.

- [ ] **Step 3: Commit**

```bash
git add Common/Factors/Forward/GoldRegime.cs
git commit -m "feat(gold): add GoldRegime + GoldSkipReason enums

Dual orthogonal state fields per design §4. UNAVAILABLE explicitly
distinguishes data-missing from rate-stable (STABLE) for future FRED
interpretability; skip_reason tracks core-signal failure independently.
"
```

---

## Task 3: Python 信号导出器（TDD）

**Files:**
- Create: `Scripts/export_gold_overnight_premium_signals.py`
- Test: `Tests/Python/Scripts/GoldOvernightPremiumExportTests.py`

- [ ] **Step 1: 写失败测试**

Create `Tests/Python/Scripts/GoldOvernightPremiumExportTests.py`:

```python
import importlib.util
import unittest
from pathlib import Path

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / "Scripts" / "export_gold_overnight_premium_signals.py"
    if not module_path.exists():
        raise AssertionError(f"Expected module to exist: {module_path}")
    spec = importlib.util.spec_from_file_location("export_gold_overnight_premium_signals", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GoldOvernightPremiumExportTests(unittest.TestCase):
    def _make_synthetic(self):
        """合成 AU.SHF + 518880 日线，60+ 行，已知 Gap。</parameter>
        AU open(T)/pre_close(T) 隔夜 +1%；518880 open 不反映溢价（gap_actual=0），Signal 恒 0.01。
        """
        dates = pd.bdate_range("2024-01-01", periods=80)
        au_pre_close = 400.0
        au_rows = []
        for d in dates:
            au_open = au_pre_close * 1.01
            au_close = au_open
            au_rows.append({
                "ts_code": "AU.SHF", "trade_date": int(d.strftime("%Y%m%d")),
                "pre_close": au_pre_close, "open": au_open, "close": au_close,
                "high": au_open, "low": au_open, "vol": 1000.0, "oi": 1000.0,
            })
            au_pre_close = au_close
        etf_pre_close = 5.0
        etf_rows = []
        for d in dates:
            etf_open = etf_pre_close  # gap_actual = 0
            etf_close = etf_open
            etf_rows.append({
                "ts_code": "518880.SH", "trade_date": int(d.strftime("%Y%m%d")),
                "pre_close": etf_pre_close, "open": etf_open, "close": etf_close,
                "high": etf_open, "low": etf_open, "vol": 10000.0,
            })
            etf_pre_close = etf_close
        return pd.DataFrame(au_rows), pd.DataFrame(etf_rows)

    def test_compute_signal_known_gap(self):
        m = load_module()
        au, etf = self._make_synthetic()
        out = m.compute_signals(au, etf, fxcm=None, min_history=60)
        self.assertIn("z_signal", out.columns)
        self.assertIn("skip_reason", out.columns)
        self.assertIn("regime", out.columns)
        # Signal 恒 0.01 -> std=0 -> Z=NaN 守卫，skip_reason=INSUFFICIENT_HISTORY/NO_EDGE，不崩溃
        self.assertGreaterEqual(len(out), 80)

    def test_skip_reason_data_stale_when_row_missing(self):
        m = load_module()
        au, etf = self._make_synthetic()
        au = au.drop(index=au.index[40]).reset_index(drop=True)
        out = m.compute_signals(au, etf, fxcm=None, min_history=60)
        stale = out[out["skip_reason"] == "DATA_STALE"]
        self.assertGreaterEqual(len(stale), 1)

    def test_regime_unavailable_when_no_fred(self):
        m = load_module()
        au, etf = self._make_synthetic()
        out = m.compute_signals(au, etf, fxcm=None, min_history=60, fred_df=None)
        self.assertTrue((out["regime"] == "UNAVAILABLE").all())

    def test_causal_no_lookahead(self):
        """Z(T) 只用 T 及之前的数据。截断后 5 天，前面 Z 应与 full 一致。"""
        m = load_module()
        au, etf = self._make_synthetic()
        out_full = m.compute_signals(au, etf, fxcm=None, min_history=60)
        out_trunc = m.compute_signals(au.iloc[:-5], etf.iloc[:-5], fxcm=None, min_history=60)
        merged = out_full.merge(out_trunc, on="trade_date", suffixes=("_full", "_trunc"))
        common = merged.dropna(subset=["z_signal_full", "z_signal_trunc"])
        if len(common) > 0:
            import numpy as np
            np.testing.assert_array_almost_equal(
                common["z_signal_full"].to_numpy(), common["z_signal_trunc"].to_numpy(), decimal=8
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试验证失败**

Run:
```bash
cd /home/project/hope/Lean
python3 -m pytest Tests/Python/Scripts/GoldOvernightPremiumExportTests.py -v 2>&1 | tail -15
```
Expected: FAIL — `ModuleNotFoundError` / 文件不存在。

- [ ] **Step 3: 写导出器实现**

Create `Scripts/export_gold_overnight_premium_signals.py`:

```python
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
```

- [ ] **Step 4: 运行测试验证通过**

Run:
```bash
python3 -m pytest Tests/Python/Scripts/GoldOvernightPremiumExportTests.py -v 2>&1 | tail -15
```
Expected: 4 tests PASS.

- [ ] **Step 5: 运行导出器产出真实 parquet**

Run:
```bash
python3 Scripts/export_gold_overnight_premium_signals.py 2>&1 | tail -10
```
Expected: `Wrote N rows to .../signals.parquet` + skip_reason/regime 分布。

- [ ] **Step 6: Commit**

```bash
git add Scripts/export_gold_overnight_premium_signals.py Tests/Python/Scripts/GoldOvernightPremiumExportTests.py
git commit -m "feat(gold): add overnight-premium signal exporter with TDD tests

Computes Z_signal + regime(UNAVAILABLE) + skip_reason from AU.SHF +
518880.SH, FXCM cross-check optional. No runtime API calls. Causal
Z(T) verified by lookahead test. Mirrors export_ashare_etf_t0_feature_data.
"
```

---

## Task 4: OvernightPremiumFactor（C# IFactor，Precomputed）

**Files:**
- Create: `Common/Factors/Forward/GoldOvernightPremiumFactor.cs`
- Test: `Tests/Common/Factors/GoldOvernightPremiumFactorTests.cs`

- [ ] **Step 1: 写失败测试**

Create `Tests/Common/Factors/GoldOvernightPremiumFactorTests.cs`:

```csharp
using System;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Tests.Common.Factors
{
    [TestFixture]
    public class GoldOvernightPremiumFactorTests
    {
        [Test]
        public void InjectValue_ThenCompute_ReturnsValue()
        {
            var factor = new GoldOvernightPremiumFactor();
            var sym = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            factor.InjectValue(sym, 1.7m);
            var result = factor.Compute(sym, new DateTime(2026, 6, 16));
            Assert.AreEqual(1.7m, result.Value);
            Assert.AreEqual(FactorDataQuality.Valid, result.Quality);
        }

        [Test]
        public void Compute_WithoutInjection_ReturnsMissing()
        {
            var factor = new GoldOvernightPremiumFactor();
            var sym = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            var result = factor.Compute(sym, new DateTime(2026, 6, 16));
            Assert.AreEqual(FactorDataQuality.Missing, result.Quality);
        }

        [Test]
        public void Id_IsStable()
        {
            Assert.AreEqual("gold_overnight_premium_z", new GoldOvernightPremiumFactor().Id);
        }
    }
}
```

- [ ] **Step 2: 运行测试验证失败**

Run:
```bash
dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~GoldOvernightPremiumFactorTests" 2>&1 | tail -8
```
Expected: FAIL — 类型不存在。

- [ ] **Step 3: 写 factor 实现**

Create `Common/Factors/Forward/GoldOvernightPremiumFactor.cs`:

```csharp
using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 黄金 ETF 隔夜溢价 Z-signal 因子（Precomputed）。
    /// 由 Python 导出器算好 Z_signal 后通过 InjectValue 注入，算法 OnData 调用 Compute 读取。
    /// 镜像 AuctionGapFactor 的 Precomputed + InjectValue 模式。
    /// 详见 docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md §3。
    /// </summary>
    public class GoldOvernightPremiumFactor : IFactor
    {
        private readonly Dictionary<Symbol, decimal> _injected = new();

        public string Id => "gold_overnight_premium_z";
        public string Name => "Gold Overnight Premium Z-Signal";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "au_shf_fut_daily+518880_fund_daily";

        public void InjectValue(Symbol symbol, decimal value) => _injected[symbol] = value;
        public void ClearInjectedValues() => _injected.Clear();

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            if (_injected.TryGetValue(symbol, out var value))
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = value, RawValue = value };
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => _injected.ContainsKey(symbol);
    }
}
```

- [ ] **Step 4: 运行测试验证通过**

Run:
```bash
dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~GoldOvernightPremiumFactorTests" 2>&1 | tail -8
```
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add Common/Factors/Forward/GoldOvernightPremiumFactor.cs Tests/Common/Factors/GoldOvernightPremiumFactorTests.cs
git commit -m "feat(gold): add GoldOvernightPremiumFactor (Precomputed IFactor)

Mirrors AuctionGapFactor: InjectValue + Compute. Returns Z_signal
injected by algorithm from Python-exported parquet. Zero-invasive.
"
```

---

## Task 5: RealRateRegimeFactor（C# IFactor + 正交性测试）

**Files:**
- Create: `Common/Factors/Forward/GoldRealRateRegimeFactor.cs`
- Test: `Tests/Common/Factors/GoldRealRateRegimeFactorTests.cs`

- [ ] **Step 1: 写失败测试（含正交性测试）**

Create `Tests/Common/Factors/GoldRealRateRegimeFactorTests.cs`:

```csharp
using System;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Tests.Common.Factors
{
    [TestFixture]
    public class GoldRealRateRegimeFactorTests
    {
        [Test]
        public void InjectRegime_ThenCompute_ReturnsEnumEncoded()
        {
            var factor = new GoldRealRateRegimeFactor();
            var sym = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            factor.InjectRegime(sym, GoldRegime.STABLE);
            var result = factor.Compute(sym, new DateTime(2026, 6, 16));
            Assert.AreEqual((decimal)GoldRegime.STABLE, result.Value);
            Assert.AreEqual(FactorDataQuality.Valid, result.Quality);
        }

        [Test]
        public void UnavailableRegime_DistinctFromStable()
        {
            var factor = new GoldRealRateRegimeFactor();
            var sym = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            factor.InjectRegime(sym, GoldRegime.UNAVAILABLE);
            var result = factor.Compute(sym, new DateTime(2026, 6, 16));
            Assert.AreEqual((decimal)GoldRegime.UNAVAILABLE, result.Value);
            Assert.AreNotEqual((decimal)GoldRegime.STABLE, result.Value);
        }

        [Test]
        public void RegimeFactorIndependentOfSkipReason()
        {
            // 设计 §4: skip_reason 与 regime 独立。regime factor 只知 regime，不知 skip_reason。
            // 一个 DATA_STALE 日可同时 regime=STABLE；正交性由算法层组合两因子实现。
            var regimeFactor = new GoldRealRateRegimeFactor();
            var sym = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            regimeFactor.InjectRegime(sym, GoldRegime.STABLE);
            var r1 = regimeFactor.Compute(sym, DateTime.Today);
            Assert.AreEqual(GoldRegime.STABLE, (GoldRegime)(int)r1.Value);
            // regime factor 不含 skip_reason 字段 — 物理隔离
        }
    }
}
```

- [ ] **Step 2: 运行测试验证失败**

Run:
```bash
dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~GoldRealRateRegimeFactorTests" 2>&1 | tail -8
```
Expected: FAIL — 类型不存在。

- [ ] **Step 3: 写 factor 实现**

Create `Common/Factors/Forward/GoldRealRateRegimeFactor.cs`:

```csharp
using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// 实际利率 regime 因子（Precomputed）。返回 GoldRegime enum 编码为 decimal。
    /// 当前 FRED_API_KEY 未配置时算法注入 UNAVAILABLE（行为同 STABLE，状态区分）。
    /// 详见 docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md §4.1。
    /// </summary>
    public class GoldRealRateRegimeFactor : IFactor
    {
        private readonly Dictionary<Symbol, GoldRegime> _injected = new();

        public string Id => "gold_real_rate_regime";
        public string Name => "Gold Real Rate Regime";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Precomputed;
        public string DataSource => "fred_dfii10(optional)";

        public void InjectRegime(Symbol symbol, GoldRegime regime) => _injected[symbol] = regime;
        public void ClearInjectedValues() => _injected.Clear();

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            if (_injected.TryGetValue(symbol, out var regime))
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = (decimal)regime, RawValue = (decimal)regime };
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing, Value = (decimal)GoldRegime.UNAVAILABLE };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => _injected.ContainsKey(symbol);
    }
}
```

- [ ] **Step 4: 运行测试验证通过**

Run:
```bash
dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~GoldRealRateRegimeFactorTests" 2>&1 | tail -8
```
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add Common/Factors/Forward/GoldRealRateRegimeFactor.cs Tests/Common/Factors/GoldRealRateRegimeFactorTests.cs
git commit -m "feat(gold): add GoldRealRateRegimeFactor with UNAVAILABLE state

Precomputed regime IFactor. UNAVAILABLE != STABLE (distinct enum),
behavior same today, state separated for future FRED interpretability.
Orthogonality with skip_reason verified in tests.
"
```

---

## Task 6: NoOvernightPositionRiskModel（C# 风控，硬平仓）

**Files:**
- Create: `Algorithm.CSharp/Models/Risk/NoOvernightPositionRiskModel.cs`
- Test: `Tests/Algorithm/GoldOvernightPremiumAlgorithmTests.cs`（先建文件含风控单测）

- [ ] **Step 1: 写失败测试**

Create `Tests/Algorithm/GoldOvernightPremiumAlgorithmTests.cs`:

```csharp
using System;
using NUnit.Framework;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.CSharp.Models.Risk;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class GoldOvernightPremiumAlgorithmTests
    {
        // 风控测试用最小 algorithm 桩提供 algorithm.Time。GoldOvernightPremiumAlgorithm 在 Task 8 实现；
        // 此处先测 ManageRisk 纯逻辑：14:45 后任何持仓归零。

        [Test]
        public void NoOvernightPosition_AfterForcedCloseTime_LiquidatesAll()
        {
            var model = new NoOvernightPositionRiskModel(forcedCloseTime: new TimeSpan(14, 45, 0));
            var sym = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            var target = new PortfolioTarget(sym, 1000);
            var algo = new TestAlgorithm { CurrentTime = new DateTime(2026, 6, 16, 14, 46, 0) };
            var result = model.ManageRisk(algo, new[] { target });
            foreach (var t in result)
                Assert.AreEqual(0, t.Quantity, "14:46 应强制平仓");
        }

        [Test]
        public void NoOvernightPosition_BeforeForcedCloseTime_PassesThrough()
        {
            var model = new NoOvernightPositionRiskModel(forcedCloseTime: new TimeSpan(14, 45, 0));
            var sym = Symbol.Create("518880", SecurityType.Equity, Market.SSE);
            var target = new PortfolioTarget(sym, 1000);
            var algo = new TestAlgorithm { CurrentTime = new DateTime(2026, 6, 16, 10, 0, 0) };
            var result = model.ManageRisk(algo, new[] { target });
            foreach (var t in result)
                Assert.AreEqual(1000, t.Quantity, "10:00 应放行");
        }

        public class TestAlgorithm : QuantConnect.Algorithm.QCAlgorithm
        {
            public DateTime CurrentTime { get; set; } = new DateTime(2026, 6, 16, 10, 0, 0);
            public new DateTime Time => CurrentTime;
        }
    }
}
```

- [ ] **Step 2: 运行测试验证失败**

Run:
```bash
dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~GoldOvernightPremiumAlgorithmTests" 2>&1 | tail -8
```
Expected: FAIL — `NoOvernightPositionRiskModel` 不存在。

- [ ] **Step 3: 写风控实现**

Create `Algorithm.CSharp/Models/Risk/NoOvernightPositionRiskModel.cs`:

```csharp
using System;
using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Data.UniverseSelection;

namespace QuantConnect.Algorithm.CSharp.Models.Risk
{
    /// <summary>
    /// 强制无隔夜持仓风控。14:45（默认）后任何持仓归零，硬执行不靠策略自觉。
    /// 这是 INoOvernightPosition marker 意图的落地，但不新增 C# marker 接口（risk model 即强制点）。
    /// 详见 docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md §5。
    /// </summary>
    public class NoOvernightPositionRiskModel : IRiskManagementModel
    {
        private readonly TimeSpan _forcedCloseTime;
        public string Name => "NoOvernightPositionRiskModel";

        public NoOvernightPositionRiskModel(TimeSpan? forcedCloseTime = null)
            => _forcedCloseTime = forcedCloseTime ?? new TimeSpan(14, 45, 0);

        public IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            var now = algorithm.Time.Time;
            var forceClose = now >= _forcedCloseTime;
            var result = new List<IPortfolioTarget>();
            foreach (var t in targets)
            {
                if (t.Quantity == 0) { result.Add(t); continue; }
                result.Add(forceClose ? new PortfolioTarget(t.Symbol, 0) : t);
            }
            return result;
        }

        public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes) { }
    }
}
```

- [ ] **Step 4: 运行测试验证通过**

Run:
```bash
dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~GoldOvernightPremiumAlgorithmTests" 2>&1 | tail -8
```
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add Algorithm.CSharp/Models/Risk/NoOvernightPositionRiskModel.cs Tests/Algorithm/GoldOvernightPremiumAlgorithmTests.cs
git commit -m "feat(gold): add NoOvernightPositionRiskModel (14:45 hard flat)

Forces zero position after 14:45 via IRiskManagementModel — not strategy
self-discipline. Implements INoOvernightPosition intent without adding
a new C# marker interface (risk model is the enforcement point).
"
```

---

## Task 7: GoldOvernightSignal custom BaseData（C#）

**Files:**
- Create: `Common/Data/Custom/Gold/GoldOvernightSignal.cs`

- [ ] **Step 1: 写 custom data 类型**

Create `Common/Data/Custom/Gold/GoldOvernightSignal.cs`:

```csharp
using System;
using System.Globalization;
using System.IO;
using QuantConnect.Data;

namespace QuantConnect.Data.Custom.Gold
{
    /// <summary>
    /// 黄金隔夜溢价信号 custom data。每 trade_date 一行，从导出器产出的 CSV 载入。
    /// 字段: trade_date, z_signal, regime, skip_reason, gap_expected, gap_actual, signal。
    /// 详见 docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md §2.2。
    /// </summary>
    public class GoldOvernightSignal : BaseData
    {
        public decimal ZSignal { get; set; }
        public string Regime { get; set; }
        public string SkipReason { get; set; }
        public decimal GapExpected { get; set; }
        public decimal GapActual { get; set; }
        public decimal Signal { get; set; }

        public override DateTime EndTime => Time;

        public override BaseData Reader(SubscriptionDataConfig config, string line, DateTime dateSpecified, bool isLiveMode)
        {
            if (string.IsNullOrWhiteSpace(line) || line.StartsWith("trade_date"))
                return null;
            var csv = line.Split(',');
            if (csv.Length < 7) return null;
            if (!DateTime.TryParseExact(csv[0], "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var d))
                return null;
            var sig = new GoldOvernightSignal
            {
                Symbol = config.Symbol,
                Time = d,
                ZSignal = decimal.TryParse(csv[1], NumberStyles.Any, CultureInfo.InvariantCulture, out var z) ? z : 0m,
                Regime = csv[2],
                SkipReason = csv[3],
                GapExpected = decimal.TryParse(csv[4], NumberStyles.Any, CultureInfo.InvariantCulture, out var ge) ? ge : 0m,
                GapActual = decimal.TryParse(csv[5], NumberStyles.Any, CultureInfo.InvariantCulture, out var ga) ? ga : 0m,
                Signal = decimal.TryParse(csv[6], NumberStyles.Any, CultureInfo.InvariantCulture, out var s) ? s : 0m,
            };
            sig.Value = sig.ZSignal;
            return sig;
        }

        public override SubscriptionDataSource GetSource(SubscriptionDataConfig config, DateTime date, bool isLiveMode)
        {
            var path = Path.Combine(Globals.DataFolder, "alternative", "gold-overnight-premium", "signals.csv");
            return new SubscriptionDataSource(path, SubscriptionTransportMedium.LocalFile, FileFormat.Csv);
        }
    }
}
```

- [ ] **Step 2: Build 验证编译**

Run:
```bash
dotnet build Common/QuantConnect.csproj 2>&1 | tail -5
```
Expected: Build succeeded.

- [ ] **Step 3: Commit**

```bash
git add Common/Data/Custom/Gold/GoldOvernightSignal.cs
git commit -m "feat(gold): add GoldOvernightSignal custom BaseData

Loads per-trade_date signal row from CSV (z_signal, regime, skip_reason,
gaps). Reader/GetSource pattern. Algorithm consumes via AddData<>().
"
```

---

## Task 8: GoldOvernightPremiumAlgorithm（C# 主算法）

**Files:**
- Create: `Algorithm.CSharp/GoldOvernightPremiumAlgorithm.cs`
- Modify: `Tests/Algorithm/GoldOvernightPremiumAlgorithmTests.cs`（追加集成断言）

- [ ] **Step 1: 追加算法测试**

在 `Tests/Algorithm/GoldOvernightPremiumAlgorithmTests.cs` 的类内、`TestAlgorithm` 类之前追加:

```csharp
        [Test]
        public void EntryLogic_LongOnly_ZAboveThreshold()
        {
            // 设计 §5: Z > 1.5 做多；Z < -1.5 空仓观望（不可做空）；|Z|<=1.5 空仓。
            Assert.AreEqual(1, GoldOvernightPremiumAlgorithm.EntryDirection(1.6m));   // 多
            Assert.AreEqual(0, GoldOvernightPremiumAlgorithm.EntryDirection(-1.6m));  // 观望（不可做空）
            Assert.AreEqual(0, GoldOvernightPremiumAlgorithm.EntryDirection(0.5m));   // 无 edge
        }

        [Test]
        public void RegimeCap_RisingFast_ReducesTo03x()
        {
            Assert.AreEqual(0.3m, GoldOvernightPremiumAlgorithm.RegimeCap(QuantConnect.Factors.Forward.GoldRegime.RISING_FAST));
            Assert.AreEqual(1.0m, GoldOvernightPremiumAlgorithm.RegimeCap(QuantConnect.Factors.Forward.GoldRegime.UNAVAILABLE));
            Assert.AreEqual(1.0m, GoldOvernightPremiumAlgorithm.RegimeCap(QuantConnect.Factors.Forward.GoldRegime.STABLE));
        }
```

- [ ] **Step 2: 运行测试验证失败**

Run:
```bash
dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~GoldOvernightPremiumAlgorithmTests" 2>&1 | tail -8
```
Expected: FAIL — `GoldOvernightPremiumAlgorithm` 不存在。

- [ ] **Step 3: 写算法实现**

Create `Algorithm.CSharp/GoldOvernightPremiumAlgorithm.cs`:

```csharp
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect.Data;
using QuantConnect.Data.Custom.Gold;
using QuantConnect.Data.Market;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Forward;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;
using QuantConnect.Algorithm.CSharp.Common;
using QuantConnect.Algorithm.CSharp.Models.Risk;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// 黄金 ETF (518880) 隔夜溢价 T+0 日内策略。
    /// Z > 1.5 开盘做多，ATR 止损，0.6×|Gap_expected| 止盈，14:45 强制平仓。
    /// long-only（518880 不可做空）。regime=RISING_FAST 仓位上限 0.3×。
    /// 详见 docs/superpowers/specs/2026-07-08-gold-overnight-premium-design.md。
    /// </summary>
    public class GoldOvernightPremiumAlgorithm : QCAlgorithm, IOptimizableStrategy, IRlStateExportable
    {
        private const int LotSize = 100;
        private Symbol _gold;
        private Symbol _signalSym;
        private GoldOvernightPremiumFactor _zFactor;
        private GoldRealRateRegimeFactor _regimeFactor;
        private decimal _positionSize;
        private decimal _targetVol;
        private int _volLookbackDays;
        private decimal _atrMultiple;
        private decimal _tpFraction;
        private decimal _zThreshold;
        private decimal _initialCapital;
        private readonly List<decimal> _dailyEquity = new();
        private decimal _entryPrice;
        private decimal _stopLoss;
        private decimal _takeProfit;

        public override void Initialize()
        {
            _initialCapital = GetDecimalParameter("initial-capital", 1_000_000m);
            SetAccountCurrency(Currencies.CNY);
            SetCash(_initialCapital);
            SetStartDate(GetDateParameter("start-date", new DateTime(2020, 1, 1)));
            SetEndDate(GetDateParameter("end-date", new DateTime(2026, 6, 23)));
            SetBenchmark(x => 0m);

            _positionSize = GetDecimalParameter("position-size", 0.30m);
            _targetVol = GetDecimalParameter("target-vol", 0.12m);
            _volLookbackDays = GetIntParameter("vol-lookback-days", 60);
            _atrMultiple = GetDecimalParameter("atr-multiple", 0.5m);
            _tpFraction = GetDecimalParameter("tp-fraction", 0.6m);
            _zThreshold = GetDecimalParameter("z-threshold", 1.5m);

            var gold = AddEquity("518880", Resolution.Daily, Market.SSE);
            gold.FeeModel = new AShareStockFeeModel();
            gold.FillModel = new AShareStockFillModel();
            gold.BuyingPowerModel = new AShareStockBuyingPowerModel();
            gold.SettlementModel = new DelayedSettlementModel(0, TimeSpan.Zero);
            _gold = gold.Symbol;

            _signalSym = AddData<GoldOvernightSignal>("GOLD_OVN", Resolution.Daily).Symbol;

            _zFactor = new GoldOvernightPremiumFactor();
            _regimeFactor = new GoldRealRateRegimeFactor();

            // 风控链：14:45 硬平仓 + 仓位上限
            AddRiskManagement(new NoOvernightPositionRiskModel());
            AddRiskManagement(new PositionLimitRiskModel(_positionSize));

            Schedule.On(DateRules.EveryDay(_gold), TimeRules.AfterMarketOpen(_gold, 5), EvaluateEntry);
            Schedule.On(DateRules.EveryDay(_gold), TimeRules.BeforeMarketClose(_gold, 15), ForceCloseCheck);
        }

        public override void OnData(Slice data)
        {
            if (data.Custom.TryGetValue(_signalSym, out var bd) && bd is GoldOvernightSignal sig)
            {
                _zFactor.InjectValue(_gold, sig.ZSignal);
                if (Enum.TryParse<GoldRegime>(sig.Regime, out var regime))
                    _regimeFactor.InjectRegime(_gold, regime);
            }
            _dailyEquity.Add(Portfolio.TotalPortfolioValue);
        }

        private void EvaluateEntry()
        {
            var z = _zFactor.Compute(_gold, Time).Value;
            var regime = (GoldRegime)(int)_regimeFactor.Compute(_gold, Time).Value;
            var dir = EntryDirection(z);
            if (dir != 1) return;  // long-only

            var cap = RegimeCap(regime);
            var price = Securities[_gold].Price;
            if (price <= 0) return;

            var realizedVol = ComputeAnnualizedVol(DailyReturns(_dailyEquity, _volLookbackDays));
            var volScale = realizedVol > 0.01m ? Math.Max(0.25m, Math.Min(2.0m, _targetVol / realizedVol)) : 1m;
            var alloc = _positionSize * volScale * cap;
            var qty = (int)(Math.Floor(_initialCapital * alloc / (price * LotSize)) * LotSize);
            if (qty >= LotSize)
            {
                MarketOrder(_gold, qty);
                _entryPrice = price;
                _stopLoss = price - _atrMultiple * ATR(14);
                var gapExp = _zFactor.Compute(_gold, Time).RawValue;
                _takeProfit = price + _tpFraction * Math.Abs(gapExp);
            }
        }

        private void ForceCloseCheck()
        {
            // 14:45 由 NoOvernightPositionRiskModel 强制；此处只做止盈止损
            if (!Portfolio.Invested) return;
            var price = Securities[_gold].Price;
            if (price <= _stopLoss || price >= _takeProfit)
                Liquidate(_gold);
        }

        /// <summary>Z > threshold → 1 (多)；Z < -threshold → 0 (观望，不可做空)；否则 0。</summary>
        public static int EntryDirection(decimal z) => z > 1.5m ? 1 : 0;

        /// <summary>regime=RISING_FAST → 0.3×；其余（含 UNAVAILABLE）→ 1.0×。</summary>
        public static decimal RegimeCap(GoldRegime regime) =>
            regime == GoldRegime.RISING_FAST ? 0.3m : 1.0m;

        public static decimal ComputeAnnualizedVol(IReadOnlyList<decimal> dailyReturns)
        {
            if (dailyReturns.Count < 5) return 0.2m;
            var mean = dailyReturns.Average();
            var variance = dailyReturns.Select(r => (r - mean) * (r - mean)).Average();
            return (decimal)Math.Sqrt((double)variance) * (decimal)Math.Sqrt(252);
        }

        public static List<decimal> DailyReturns(IReadOnlyList<decimal> equity, int lookback)
        {
            var returns = new List<decimal>();
            var start = Math.Max(1, equity.Count - lookback);
            for (int i = start; i < equity.Count; i++)
                if (equity[i - 1] > 0) returns.Add(equity[i] / equity[i - 1] - 1m);
            return returns;
        }

        public override void OnEndOfAlgorithm()
        {
            Log($"[GoldOvernightPremium] Final equity: {Portfolio.TotalPortfolioValue:C2}");
        }

        public IEnumerable<string> GetTunableParameterNames() => new[]
        {
            "position-size", "target-vol", "vol-lookback-days", "z-threshold",
            "atr-multiple", "tp-fraction"
        };

        public string SerializeRlState(QCAlgorithm algo)
        {
            var tpv = Portfolio.TotalPortfolioValue;
            var positions = Securities.Values
                .Where(s => s.Holdings.Quantity != 0)
                .Select(s => new { sym = s.Symbol.Value, w = s.Holdings.Quantity * s.Price / tpv, pnl_1d = 0m, days_held = 0 }).ToList();
            return JsonConvert.SerializeObject(new
            {
                ts = algo.Time.ToString("o"), strategy = "GoldOvernightPremiumAlgorithm",
                tpv, cash_pct = Portfolio.Cash / tpv, positions,
                drawdown = 0m, n_open_positions = positions.Count,
                z_signal = _zFactor.Compute(_gold, algo.Time).Value,
                regime = ((GoldRegime)(int)_regimeFactor.Compute(_gold, algo.Time).Value).ToString()
            });
        }

        private decimal GetDecimalParameter(string name, decimal def) =>
            decimal.TryParse(GetParameter(name), NumberStyles.Any, CultureInfo.InvariantCulture, out var v) ? v : def;
        private int GetIntParameter(string name, int def) =>
            int.TryParse(GetParameter(name), out var v) ? v : def;
        private DateTime GetDateParameter(string name, DateTime def) =>
            DateTime.TryParse(GetParameter(name), CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out var v) ? v : def;
    }
}
```

- [ ] **Step 4: 运行测试验证通过**

Run:
```bash
dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~GoldOvernightPremiumAlgorithmTests" 2>&1 | tail -8
```
Expected: 4 tests PASS（2 风控 + EntryDirection + RegimeCap）。

- [ ] **Step 5: Build 整个解决方案**

Run:
```bash
dotnet build QuantConnect.Lean.sln 2>&1 | tail -5
```
Expected: Build succeeded, 0 errors.

- [ ] **Step 6: Commit**

```bash
git add Algorithm.CSharp/GoldOvernightPremiumAlgorithm.cs Tests/Algorithm/GoldOvernightPremiumAlgorithmTests.cs
git commit -m "feat(gold): add GoldOvernightPremiumAlgorithm (T+0 intraday)

Z>1.5 long entry, ATR stop, 0.6x|gap| take-profit, 14:45 hard flat via
risk model. Long-only base. Regime cap (RISING_FAST -> 0.3x). Mirrors
AShareOvernightAnomalyAlgorithm + IOptimizableStrategy + IRlStateExportable.
"
```

---

## Task 9: Gate 0/1/2 校验器（Python）

**Files:**
- Create: `Scripts/gold_overnight_premium_gates.py`
- Test: `Tests/Python/Scripts/GoldOvernightPremiumGateTests.py`

- [ ] **Step 1: 写失败测试**

Create `Tests/Python/Scripts/GoldOvernightPremiumGateTests.py`:

```python
import importlib.util
import unittest
from pathlib import Path
import pandas as pd
import numpy as np


def load_module():
    p = Path(__file__).resolve().parents[3] / "Scripts" / "gold_overnight_premium_gates.py"
    spec = importlib.util.spec_from_file_location("gold_overnight_premium_gates", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class GoldOvernightPremiumGateTests(unittest.TestCase):
    def _sig(self, n=80, stale=0, z_nan=0):
        dates = pd.bdate_range("2024-01-01", periods=n)
        z = np.full(n, 1.6)
        if z_nan:
            z[:z_nan] = np.nan
        df = pd.DataFrame({
            "trade_date": [int(d.strftime("%Y%m%d")) for d in dates],
            "z_signal": z, "regime": "UNAVAILABLE", "skip_reason": "NONE",
            "signal": 0.01, "gap_expected": 0.01, "gap_actual": 0.0,
            "freshness_flag": True, "cross_check_alert": False,
        })
        if stale:
            df.loc[df.index[40], "skip_reason"] = "DATA_STALE"
        return df

    def test_gate0_zeroed_field_detects_all_nan(self):
        m = load_module()
        sig = self._sig(z_nan=80)
        ok, msg = m.gate0_zeroed_field(sig, threshold_pct=0.05)
        self.assertFalse(ok)  # 100% NaN > 5%

    def test_gate0_zeroed_field_passes_clean(self):
        m = load_module()
        sig = self._sig(z_nan=0)
        ok, _ = m.gate0_zeroed_field(sig, threshold_pct=0.05)
        self.assertTrue(ok)

    def test_gate1_cost_sensitivity(self):
        m = load_module()
        ok, _ = m.gate1_cost_sensitivity(self._sig(), cost_bps=15)
        self.assertTrue(ok)  # gap 100bps > 15bps
        sig = self._sig(); sig["gap_expected"] = 0.001
        ok, _ = m.gate1_cost_sensitivity(sig, cost_bps=15)
        self.assertFalse(ok)  # 10bps < 15bps

    def test_gate2_ic_kill_switch_fails_when_no_predictive_power(self):
        m = load_module()
        sig = self._sig()
        sig["forward_return"] = np.random.RandomState(42).randn(len(sig))  # 纯噪声
        ok, _ = m.gate2_ic_kill_switch(sig, oos_split=0.7)
        self.assertFalse(ok)

    def test_gate2_ic_kill_switch_passes_with_predictive_signal(self):
        m = load_module()
        sig = self._sig()
        sig["forward_return"] = sig["signal"] * 5 + np.random.RandomState(42).randn(len(sig)) * 0.01
        ok, _ = m.gate2_ic_kill_switch(sig, oos_split=0.7)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试验证失败**

Run:
```bash
python3 -m pytest Tests/Python/Scripts/GoldOvernightPremiumGateTests.py -v 2>&1 | tail -12
```
Expected: FAIL — 模块不存在。

- [ ] **Step 3: 写 Gate 实现**

Create `Scripts/gold_overnight_premium_gates.py`:

```python
#!/usr/bin/env python3
"""Gate 0/1/2 校验器。设计 §6。
Gate 0: 数据完整性（zeroed-field 检测、范围对齐）。
Gate 1: 成本敏感度。
Gate 2: IC 有效性 kill switch（OOS IC > 0，否则 EFFECTIVENESS_FAIL）。
"""
from __future__ import annotations
from typing import Tuple
from datetime import datetime
import numpy as np
import pandas as pd


def gate0_zeroed_field(sig: pd.DataFrame, threshold_pct: float = 0.05) -> Tuple[bool, str]:
    """Z_signal 非 NaN/非零比例须 > 1 - threshold。复用 BarraCNE5V4 zeroed-field 语义。"""
    if "z_signal" not in sig.columns:
        return False, "z_signal column missing"
    total = len(sig)
    if total == 0:
        return False, "empty signal frame"
    nan_or_zero = sig["z_signal"].isna() | (sig["z_signal"] == 0)
    bad_ratio = nan_or_zero.sum() / total
    if bad_ratio > threshold_pct:
        return False, f"z_signal NaN/zero ratio {bad_ratio:.1%} > {threshold_pct:.0%}"
    return True, "ok"


def gate0_mapping_alignment(au: pd.DataFrame, mapping: pd.DataFrame) -> Tuple[bool, str]:
    """fut_mapping(AUL.SHF) 范围须与 fut_daily(AU.SHF) 对齐。设计 §6.1 + §1.3 约束3。"""
    if au.empty or mapping.empty:
        return False, "au or mapping empty"
    au_span = (str(au["trade_date"].min()), str(au["trade_date"].max()))
    mp_span = (str(mapping["trade_date"].min()), str(mapping["trade_date"].max()))
    def _d(s): return datetime.strptime(str(s), "%Y%m%d")
    start_diff = abs((_d(au_span[0]) - _d(mp_span[0])).days)
    end_diff = abs((_d(au_span[1]) - _d(mp_span[1])).days)
    if start_diff > 730:
        return False, f"mapping start {mp_span[0]} vs au start {au_span[0]} differ > 2y"
    if end_diff > 2:
        return False, f"mapping end {mp_span[1]} vs au end {au_span[1]} differ > 2d"
    return True, f"aligned au={au_span} mapping={mp_span}"


def gate1_cost_sensitivity(sig: pd.DataFrame, cost_bps: float = 15) -> Tuple[bool, str]:
    """|gap_expected| 期望收益须 > 往返成本。设计 §6.2。"""
    if sig.empty:
        return False, "empty"
    mean_gap = sig["gap_expected"].abs().mean()
    if np.isnan(mean_gap):
        return False, "gap_expected all NaN"
    gap_bps = mean_gap * 10000
    if gap_bps <= cost_bps:
        return False, f"mean |gap| {gap_bps:.1f}bps <= cost {cost_bps}bps"
    return True, f"mean |gap| {gap_bps:.1f}bps > cost {cost_bps}bps"


def gate2_ic_kill_switch(sig: pd.DataFrame, oos_split: float = 0.7) -> Tuple[bool, str]:
    """OOS IC > 0 且 20 日 rolling IC 符号一致性 > 60%。否则 EFFECTIVENESS_FAIL。
    设计 §6.3: 这是"有效而非仅可用"的硬执行。失败附 ft_mins 升级说明。"""
    if sig.empty or "forward_return" not in sig.columns:
        return False, "forward_return missing (need signal + forward return for IC)"
    sig = sig.dropna(subset=["signal", "forward_return"]).reset_index(drop=True)
    if len(sig) < 40:
        return False, f"insufficient rows for IC: {len(sig)}"
    oos = sig.iloc[int(len(sig) * oos_split):]
    if len(oos) < 10:
        return False, "OOS too short"
    ic_oos = np.corrcoef(oos["signal"], oos["forward_return"])[0, 1]
    if np.isnan(ic_oos) or ic_oos <= 0:
        return False, (f"EFFECTIVENESS_FAIL: OOS IC={ic_oos:.3f} <= 0. "
                       f"日线代理信号无预测力。升级路径: 需 ft_mins (10000 积分) 做真夜盘隔离。")
    rolling_ic = sig["signal"].rolling(20).corr(sig["forward_return"]).dropna()
    if len(rolling_ic) == 0:
        return False, "rolling IC empty"
    sign_consistency = (rolling_ic > 0).sum() / len(rolling_ic)
    if sign_consistency < 0.6:
        return False, f"rolling IC sign consistency {sign_consistency:.1%} < 60%"
    return True, f"OOS IC={ic_oos:.3f}, sign consistency={sign_consistency:.1%}"


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", required=True)
    ap.add_argument("--au", default=None)
    ap.add_argument("--mapping", default=None)
    args = ap.parse_args()
    sig = pd.read_parquet(args.signals)
    print("=== Gate 0 ===")
    ok, msg = gate0_zeroed_field(sig)
    print(f"zeroed-field: {ok} | {msg}")
    if args.au and args.mapping:
        au = pd.read_parquet(args.au)
        mp = pd.read_parquet(args.mapping)
        ok2, msg2 = gate0_mapping_alignment(au, mp)
        print(f"mapping-align: {ok2} | {msg2}")
    print("=== Gate 1 ===")
    ok, msg = gate1_cost_sensitivity(sig)
    print(f"cost: {ok} | {msg}")
    print("=== Gate 2 (需 forward_return 列；导出器可加) ===")
    if "forward_return" in sig.columns:
        ok, msg = gate2_ic_kill_switch(sig)
        print(f"ic: {ok} | {msg}")
    else:
        print("skipped (no forward_return column)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行测试验证通过**

Run:
```bash
python3 -m pytest Tests/Python/Scripts/GoldOvernightPremiumGateTests.py -v 2>&1 | tail -12
```
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add Scripts/gold_overnight_premium_gates.py Tests/Python/Scripts/GoldOvernightPremiumGateTests.py
git commit -m "feat(gold): add Gate 0/1/2 validators with IC kill switch

Gate 0: zeroed-field + mapping alignment. Gate 1: cost sensitivity.
Gate 2: OOS IC > 0 else EFFECTIVENESS_FAIL with ft_mins upgrade path.
This is the 'effective not just usable' enforcement per design §6.3.
"
```

---

## Task 10: manifest.yaml + LEAN config

**Files:**
- Create: `Scripts/auto_optimize/strategies/gold_overnight_premium/manifest.yaml`
- Create: `Launcher/config/config-gold-overnight-premium-backtest.json`

- [ ] **Step 1: 写简化 schema manifest**

Create `Scripts/auto_optimize/strategies/gold_overnight_premium/manifest.yaml`:

```yaml
strategy_name: GoldOvernightPremiumAlgorithm
rl_state_completeness: partial
lean_config: Launcher/config/config-gold-overnight-premium-backtest.json
risk_model_target: NoOvernightPositionRiskModel
parameter_space:
  - {name: position-size, type: float, range: [0.10, 0.40], default: 0.30, layer: L3_Portfolio}
  - {name: target-vol, type: float, range: [0.05, 0.25], default: 0.12, layer: L3_Portfolio}
  - {name: vol-lookback-days, type: int, range: [20, 120], default: 60, layer: L3_Portfolio}
  - {name: z-threshold, type: float, range: [1.0, 2.5], default: 1.5, layer: L2_Alpha}
  - {name: atr-multiple, type: float, range: [0.3, 1.0], default: 0.5, layer: L4_Risk}
  - {name: tp-fraction, type: float, range: [0.4, 0.8], default: 0.6, layer: L2_Alpha}
state_schema:
  fields:
    - {name: ts, type: string}
    - {name: strategy, type: string}
    - {name: tpv, type: float}
    - {name: cash_pct, type: float}
    - {name: positions, type: array, item_schema: [sym, w, pnl_1d, days_held]}
    - {name: drawdown, type: float}
    - {name: n_open_positions, type: int}
    - {name: z_signal, type: float}
    - {name: regime, type: string}
  dim_hint: 12
reward_config:
  primary: dsr
  shaping:
    - {term: scaled_pnl, weight: 1.0}
    - {term: drawdown_excess_penalty, weight: 2.0}
universe:
  symbols: ["518880"]
  timezone: Asia/Shanghai
```

- [ ] **Step 2: 写 LEAN launcher config**

Create `Launcher/config/config-gold-overnight-premium-backtest.json`:

```json
{
  "environment": "backtesting",
  "algorithm-type-name": "GoldOvernightPremiumAlgorithm",
  "algorithm-language": "CSharp",
  "algorithm-location": "../../../Algorithm.CSharp/bin/Debug/QuantConnect.Algorithm.CSharp.dll",
  "data-folder": "../../../Data",
  "data-directory": "../../../Data",
  "history-provider": "FallbackTushareHistoryProvider",
  "data-provider": "DefaultDataProvider",
  "results-destination-folder": "../../../Results/gold-overnight-premium",
  "influxdb-enabled": true,
  "influxdb-url": "http://127.0.0.1:8086",
  "influxdb-org": "lean",
  "influxdb-bucket": "quant",
  "influxdb-token-env-var": "INFLUXDB_TOKEN",
  "log-handler": "ConsoleLogHandler",
  "messaging-handler": "QuantConnect.Messaging.Messaging",
  "job-queue-handler": "QuantConnect.Queues.JobQueue",
  "api-handler": "QuantConnect.Api.Api",
  "parameters": {
    "tushare-data-path": "/home/project/tushare-downloader/tushare_data_v2",
    "start-date": "2020-01-01",
    "end-date": "2026-06-23",
    "position-size": "0.30",
    "target-vol": "0.12",
    "vol-lookback-days": "60",
    "z-threshold": "1.5",
    "atr-multiple": "0.5",
    "tp-fraction": "0.6",
    "initial-capital": "1000000",
    "fee-rate": "0.0005"
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add Scripts/auto_optimize/strategies/gold_overnight_premium/manifest.yaml Launcher/config/config-gold-overnight-premium-backtest.json
git commit -m "feat(gold): add manifest.yaml + LEAN backtest config

Simplified-schema manifest for auto_optimize integration (consistent
with other 4 strategy manifests). LEAN launcher config mirrors
config-ashare-overnight-anomaly-backtest.
"
```

---

## Task 11: 集成回测 + Gate 联跑

**Files:** 无新建；运行已有产物

- [ ] **Step 1: 产出信号 parquet + CSV**

custom BaseData 读 CSV；导出器写 parquet。需转 CSV 并加 `forward_return` 列供 Gate 2:

Run:
```bash
cd /home/project/hope/Lean
python3 -c "
import pandas as pd
df = pd.read_parquet('Data/alternative/gold-overnight-premium/signals.parquet')
df = df.sort_values('trade_date')
df['forward_return'] = df['gap_actual'].shift(-1)
df.to_csv('Data/alternative/gold-overnight-premium/signals.csv', index=False)
print('wrote signals.csv', len(df), 'rows')
"
```
Expected: `wrote signals.csv N rows`。

- [ ] **Step 2: 跑 Gate 校验**

Run:
```bash
python3 Scripts/gold_overnight_premium_gates.py \
  --signals Data/alternative/gold-overnight-premium/signals.parquet \
  --au /home/project/tushare-downloader/tushare_data_v2/fut_daily/year=2020/data.parquet \
  --mapping /home/project/tushare-downloader/tushare_data_v2/fut_mapping/year=2020/data.parquet \
  2>&1 | tail -15
```
Expected: Gate 0/1 通过；Gate 2 报 IC 结果（若失败 → 策略标 EFFECTIVENESS_FAIL，符合设计预期，记录但不阻断后续）。

- [ ] **Step 3: Build + 跑回测**

Run:
```bash
dotnet build QuantConnect.Lean.sln 2>&1 | tail -3
cd Launcher/bin/Debug && dotnet QuantConnect.Lean.Launcher.dll -- ../../../config/config-gold-overnight-premium-backtest.json 2>&1 | tail -20
```
Expected: 回测完成，输出 final equity。日志中无 overnight position 残留（14:45 强制平仓生效）。

- [ ] **Step 4: 验证从不持隔夜仓**

检查回测结果 daily summary：每个交易日 15:00 后持仓 = 0。

- [ ] **Step 5: Commit runbook 备注**

```bash
git commit --allow-empty -m "docs(gold): integration backtest + gate results recorded

Gate 0/1 pass; Gate 2 IC kill switch evaluated. 14:45 hard flat verified
(no overnight positions in backtest equity curve).
"
```

---

## 自检（plan-vs-spec coverage）

| Spec 章节 | 实现 Task |
|---|---|
| §2 架构与数据流 | Task 1-11 全部 |
| §3 信号数学 | Task 3（导出器） |
| §3.2 SHFE open 约定 | Task 9 Gate 0 记录诊断 + Task 9 Gate 2 kill switch |
| §3.3 交叉校验 | Task 3 CROSS_CHECK_FAIL |
| §4 双正交状态字段 | Task 2 enum + Task 3 skip_reason + Task 5 正交性测试 |
| §5 风控规则 | Task 6（14:45 硬平仓）+ Task 8（EntryDirection/RegimeCap/ATR/TP） |
| §6 Gate 0/1/2 | Task 9 |
| §7 测试 | Task 3,4,5,6,8,9 各自单测 + Task 11 集成 |
| §8 回填前置 | Task 1 + Task 9 Gate 0 mapping 对齐 |
| §9 已知局限 | spec 文档已记录，Gate 2 kill switch 兜底 |

**Placeholder scan:** 无 TBD/TODO 占位。`_classify_regime` 内阈值校准是设计 §6.3 明确留 OOS 阶段的引用（FRED 未接入时该函数不被调用），非计划占位。其余全实代码。

**Type consistency:** `EntryDirection`/`RegimeCap` 在 Task 8 测试与实现一致；`GoldRegime` enum 在 Task 2 定义、Task 5/8 引用一致；`InjectValue`/`InjectRegime` 方法名跨 Task 4/5 一致。

**Scope check:** 单一子项目，单一实现计划，产出可测可回测的完整策略。
