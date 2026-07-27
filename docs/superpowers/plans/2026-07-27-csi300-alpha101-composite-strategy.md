# CSI300 Alpha101 复合策略 — 走通量化策略开发环路 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 8 个跨族 alpha101 因子为 CSI300 宇宙建一条月度再平衡做多复合策略，作为 FactorStore 拉 API 的首个真实消费者，并走通"构建→策略→优化→回测→复盘→重构"6 阶段环路 + 阶段结果可视化。

**Architecture:** C# 五层框架算法（CSI300Universe → Alpha101CompositeAlphaModel 拉 8 alpha 截面 z-score 复合 → EqualWeight PCM → MaxDrawdown 风控 → LotSize 执行），复用既有 FactorStore/RParquetAdapter/bayesian_optimizer/hypothesize 全不动，新增 8 个独立文件 + bridge + dashboard。复盘用 proportional-z 每 alpha 归因（和=总 PnL 严格成立）。重构接 hypothesize+on_gate3_pass（strategy-agnostic，纯 config+新 adapter）。

**Tech Stack:** C# .NET 10 / NUnit / pythonnet / Python pytest / InfluxDB line protocol / Grafana InfluxQL / Optuna TPE / LEAN Launcher

**Spec:** `docs/superpowers/specs/2026-07-27-csi300-alpha101-composite-strategy-design.md`

**分支:** `feat/csi300-alpha101-composite` (off `feat/alpha101-factor-zoo`)

**硬约束（贯穿全任务）:**
- 零改既有功能：`FactorStore.cs`/`RParquetAdapter.cs`/`bayesian_optimizer.py`/`reward.py`/`hypothesize.py`/`on_gate3_pass.py`/`gold2.py`/`export_backtest_results_to_influx.py` 全不动。
- LEAN-native：回测/组合/统计全用 LEAN 原生（SetHoldings/Portfolio.TotalPortfolioValue），不自算。
- A 股 ticker 纯数字：`AddEquity("600519")`，market 经 `AShareCSI300UniverseSelectionModel` 解析。
- 不向既有 InfluxDB measurement 写（`lean_backtest_stat`/`soloquant_pipeline_funnel`/`barra_*` 全不动），只用全新 `csi300_alpha101_*` measurement。

**8 个手选 alpha（跨 8 族，2026-07-24 parquet 已验证存在）:**

| id | 族 | 方向 | sign |
|---|---|---|---|
| alpha001 | 极值反转 | 反向 | -1 |
| alpha006 | 量价反转 | 反向 | -1 |
| alpha030 | 趋势条件 | 正向 | +1 |
| alpha040 | 波动率 | 反向 | -1 |
| alpha042 | VWAP反转 | 反向 | -1 |
| alpha055 | 量价相关 | 反向 | -1 |
| alpha058 | 行业中性 | 反向 | -1 |
| alpha101 | 开收结构 | 正向 | +1 |

---

## File Structure

| 文件 | 职责 | 任务 |
|---|---|---|
| `Scripts/factor_zoo/backfill_alpha101_csi300.py` | 历史回填 8 alpha（2024-01→2026-06，CSI300）| Task 1 |
| `Algorithm.CSharp/Models/Alpha/Alpha101CompositeAlphaModel.cs` | 首个 FactorStore 消费者：拉 8 alpha + z-score + 复合 + top10% Insight | Task 2 |
| `Algorithm.CSharp/AShareCSI300Alpha101CompositeStrategy.cs` | 五层算法主体 + GetParameter 读权重 | Task 3 |
| `Launcher/config/config-csi300-alpha101-composite.json` | LEAN 回测 config | Task 3 |
| `Scripts/review/adapters/csi300_alpha101.py` | 复盘 adapter（proportional-z 归因）| Task 5 |
| `Scripts/auto_optimize/strategies/csi300_alpha101_composite/manifest.yaml` | Bayes manifest（8 权重 + review/feedback/inspiration）| Task 6 |
| `Scripts/feedback/adapters/csi300_alpha101.py` | 反馈 adapter（低贡献 alpha 触发 hypothesize）| Task 7 |
| `Scripts/csi300_alpha101/stage_bridge.py` | 阶段结果落 InfluxDB + CSV | Task 8 |
| `monitoring/grafana/dashboards/lean/csi300-alpha101-loop.json` | 可视化 dashboard | Task 9 |

---

## Task 1: 历史回填脚本（Stage 0）

**Files:**
- Create: `Scripts/factor_zoo/backfill_alpha101_csi300.py`
- Test: `Tests/Python/FactorZoo/test_backfill_alpha101_csi300.py`

- [ ] **Step 1: Write the failing test**

`Tests/Python/FactorZoo/test_backfill_alpha101_csi300.py`:
```python
"""Test backfill_alpha101_csi300 drives builder.build_day per trade day with CSI300 ts_codes."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "Scripts" / "factor_zoo"))


def test_backfill_calls_builder_per_trade_day(tmp_path):
    from backfill_alpha101_csi300 import backfill_range

    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    csi300 = ["600519.SH", "000001.SZ", "300750.SZ"]
    with patch("backfill_alpha101_csi300.build_day") as mock_build:
        mock_build.return_value = {"rows": 9, "failed": {}}
        backfill_range(dates, csi300, result_root=str(tmp_path))
    assert mock_build.call_count == 3
    for i, d in enumerate(dates):
        args, kwargs = mock_build.call_args_list[i]
        assert kwargs.get("date_yyyy_mm_dd") == d or args[0] == d
        assert "600519.SH" in (kwargs.get("ts_codes") or args[1])


def test_backfill_8_alpha_list_is_canonical():
    from backfill_alpha101_csi300 import ALPHAS_TO_FILL
    assert ALPHAS_TO_FILL == [
        "alpha001", "alpha006", "alpha030", "alpha040",
        "alpha042", "alpha055", "alpha058", "alpha101",
    ]


def test_backfill_skips_when_csi300_empty(tmp_path):
    from backfill_alpha101_csi300 import backfill_range
    with patch("backfill_alpha101_csi300.build_day") as mock_build:
        backfill_range(["2024-01-02"], [], result_root=str(tmp_path))
    mock_build.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest Tests/Python/FactorZoo/test_backfill_alpha101_csi300.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backfill_alpha101_csi300'`

- [ ] **Step 3: Write minimal implementation**

`Scripts/factor_zoo/backfill_alpha101_csi300.py`:
```python
"""Backfill 8 hand-picked alpha101 factors for CSI300 universe, 2024-01 → 2026-06.

Drives data-source/tushare/alpha101/builder.build_day per trade day. CSI300
ts_codes loaded via BarraCNE5DataLoader.load_index_constituents("000300.SH")
(000905.SH is absent from index_weight — spec §1, CSI300-only degrade).

Usage:
    python backfill_alpha101_csi300.py --start 2024-01-02 --end 2026-06-29
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO), str(_REPO / "data-source" / "tushare"),
           str(_REPO / "Scripts" / "factor_zoo")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from alpha101.builder import build_day  # noqa: E402
from barra_cne5_data_loader import BarraCNE5DataLoader  # noqa: E402

# The 8 hand-picked cross-family alphas (spec §2.1). builder.build_day computes
# ALL 101 alphas per call — we list these 8 to document intent + let tests assert.
ALPHAS_TO_FILL = [
    "alpha001", "alpha006", "alpha030", "alpha040",
    "alpha042", "alpha055", "alpha058", "alpha101",
]

log = logging.getLogger("backfill_alpha101_csi300")


def load_csi300_ts_codes(asof_compact: str) -> list[str]:
    """CSI300 constituents (000300.SH) as of a date."""
    loader = BarraCNE5DataLoader()
    return loader.load_index_constituents(asof_date=asof_compact, index_code="000300.SH")


def backfill_range(dates: list[str], csi300: list[str], result_root: str | None = None) -> None:
    """Call build_day for each date with the given CSI300 ts_codes list."""
    if not csi300:
        log.warning("CSI300 universe empty; skipping backfill.")
        return
    for d in dates:
        try:
            res = build_day(d, csi300, result_root=result_root)
            log.info("backfilled %s: rows=%s failed=%s",
                     d, res.get("rows"), res.get("failed", {}))
        except Exception as exc:  # noqa: BLE001
            log.error("backfill failed for %s: %s: %s", d, type(exc).__name__, exc)


def trade_days_in_range(start: str, end: str) -> list[str]:
    """Trade days (YYYY-MM-DD) between start and end inclusive, from trade_cal."""
    import pandas as pd
    tushare_path = os.environ.get(
        "TUSHARE_DATA_PATH",
        str(_REPO.parent / "tushare-downloader" / "tushare_data_v2"))
    cal = pd.read_parquet(f"{tushare_path}/trade_cal/data.parquet")
    cal = cal[(cal["is_open"] == 1)
              & (cal["cal_date"].astype(str) >= start.replace("-", ""))
              & (cal["cal_date"].astype(str) <= end.replace("-", ""))]
    compact = sorted(cal["cal_date"].astype(str).tolist())
    return [f"{c[:4]}-{c[4:6]}-{c[6:]}" for c in compact]


def _cli() -> int:
    p = argparse.ArgumentParser(description="Backfill 8 alpha101 factors for CSI300")
    p.add_argument("--start", default="2024-01-02")
    p.add_argument("--end", default="2026-06-29")
    p.add_argument("--result-root", default=None)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    dates = trade_days_in_range(args.start, args.end)
    log.info("trade days %s..%s: %d days", args.start, args.end, len(dates))
    if not dates:
        log.error("no trade days in range; aborting")
        return 1

    csi300 = load_csi300_ts_codes(dates[-1].replace("-", ""))
    log.info("CSI300 universe size: %d", len(csi300))

    backfill_range(dates, csi300, result_root=args.result_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest Tests/Python/FactorZoo/test_backfill_alpha101_csi300.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add Scripts/factor_zoo/backfill_alpha101_csi300.py Tests/Python/FactorZoo/test_backfill_alpha101_csi300.py
git commit -m "feat(csi300-alpha101): Task 1 backfill script for 8 alphas over CSI300"
```

---

## Task 2: Alpha101CompositeAlphaModel (C# — 首个 FactorStore 消费者)

**Files:**
- Create: `Algorithm.CSharp/Models/Alpha/Alpha101CompositeAlphaModel.cs`
- Test: `Tests/Common/Algorithm/Models/Alpha/Alpha101CompositeAlphaModelTests.cs`

**关键**: 这是 `FactorStore` 拉 API 的**首个真实算法消费者**。`FactorStore` 在 ctor 构造一次（自动 `RegisterDefaults`）；`RParquetAdapter` 不 forward-fill，缺即 `Quality=Missing`。

- [ ] **Step 1: Write the failing test**

`Tests/Common/Algorithm/Models/Alpha/Alpha101CompositeAlphaModelTests.cs`:
```csharp
using System;
using System.Collections.Generic;
using System.Linq;
using NUnit.Framework;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Common.Algorithm.Models.Alpha
{
    [TestFixture]
    public class Alpha101CompositeAlphaModelTests
    {
        private static FactorStore MakeFakeStore(Dictionary<string, decimal> values)
        {
            var store = new FactorStore();
            foreach (var kv in values)
            {
                store.Register(kv.Key, new RParquetAdapter(
                    factorRoot: $"factor-zoo/{kv.Key}", valueColumn: kv.Key,
                    readScalar: (p, c) => values[c]));
            }
            return store;
        }

        private static List<Symbol> MakeSymbols()
        {
            return new List<Symbol>
            {
                Symbol.Create("600519", SecurityType.Equity, Market.SSE),
                Symbol.Create("600000", SecurityType.Equity, Market.SSE),
                Symbol.Create("000001", SecurityType.Equity, Market.SZSE),
                Symbol.Create("300750", SecurityType.Equity, Market.SZSE),
                Symbol.Create("603799", SecurityType.Equity, Market.SSE),
            };
        }

        [Test]
        public void Update_WithFakeReader_ProducesTopQuantileInsights()
        {
            var vals = new Dictionary<string, decimal>
            {
                {"alpha001", 0.10m}, {"alpha006", 0.20m}, {"alpha030", 0.30m},
                {"alpha040", 0.40m}, {"alpha042", 0.50m}, {"alpha055", 0.60m},
                {"alpha058", 0.70m}, {"alpha101", 0.80m},
            };
            var model = new Alpha101CompositeAlphaModel(rebalanceMonths: 1, topQuantile: 0.20m);
            var alg = new TestAlgorithm();
            var slice = new Slice(DateTime(2026, 1, 5), new List<BaseData>(), alg.TimeKeeper, Time.Front);
            var insights = model.Update(alg, slice).ToList();
            // top 20% of 5 symbols = 1 insight.
            Assert.AreEqual(1, insights.Count, "expected exactly 1 top insight");
            Assert.AreEqual(InsightDirection.Up, insights[0].Direction);
        }

        [Test]
        public void Update_AllMissing_HoldsNotThrows()
        {
            var model = new Alpha101CompositeAlphaModel(rebalanceMonths: 1);
            var alg = new TestAlgorithm();
            var slice = new Slice(new DateTime(1900, 1, 1), new List<BaseData>(), alg.TimeKeeper, Time.Front);
            Assert.DoesNotThrow(() => model.Update(alg, slice));
            var insights = model.Update(alg, slice).ToList();
            Assert.AreEqual(0, insights.Count, "no insights when all Missing");
        }

        // Minimal algorithm stub for IAlphaModel.Update's QCAlgorithm param.
        // Implementer: if Slice ctor / TimeKeeper setup complains, align with
        // Tests/Algorithm/BasicTests.cs canonical minimal-algorithm pattern.
        private class TestAlgorithm : QCAlgorithm
        {
            public TestAlgorithm()
            {
                SetTimeZone(TimeZones.Utc);
                SetStartDate(2026, 1, 1);
                SetEndDate(2026, 12, 31);
            }
        }
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Alpha101CompositeAlphaModelTests" --no-build 2>&1 | tail -20`
Expected: FAIL — `Alpha101CompositeAlphaModel` type not found.

- [ ] **Step 3: Write minimal implementation**

`Algorithm.CSharp/Models/Alpha/Alpha101CompositeAlphaModel.cs`:
```csharp
/*
 * Alpha101CompositeAlphaModel — FIRST real FactorStore (Phase-2 pull) consumer.
 * Monthly rebalance: for each of 8 alpha101 ids, pull cross-sectional values
 * across the active universe, skip Quality==Missing, two-pass z-score, apply
 * per-alpha direction sign, average across available alphas, long-only top N%.
 *
 * FactorStore is constructed ONCE in the ctor (auto RegisterDefaults); Get is
 * read-only and immutable post-ctor. RParquetAdapter does NOT forward-fill
 * (unlike RBarraAdapter) → Missing means "no parquet for this exact date";
 * ResolveFactorDate walks back to the previous trade day (cap 7 days).
 */
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Alpha
{
    public class Alpha101CompositeAlphaModel : IAlphaModel
    {
        private static readonly string[] AlphaIds =
        {
            "alpha001", "alpha006", "alpha030", "alpha040",
            "alpha042", "alpha055", "alpha058", "alpha101",
        };

        // +1 正向 / -1 反向 (sign-flip so high composite → long).
        private static readonly Dictionary<string, int> DirectionSign = new()
        {
            {"alpha001", -1}, {"alpha006", -1}, {"alpha030", +1}, {"alpha040", -1},
            {"alpha042", -1}, {"alpha055", -1}, {"alpha058", -1}, {"alpha101", +1},
        };

        private readonly int _rebalanceMonths;
        private readonly int _insightPeriodDays;
        private readonly decimal _topQuantile;
        private readonly FactorStore _store;   // ctor once

        private int _lastRebalanceYear = -1;
        private int _lastRebalanceMonth = -1;

        public string Name => "Alpha101Composite";

        public Alpha101CompositeAlphaModel(
            int rebalanceMonths = 1,
            int insightPeriodDays = 21,
            decimal topQuantile = 0.10m)
        {
            _rebalanceMonths = Math.Max(1, rebalanceMonths);
            _insightPeriodDays = Math.Max(1, insightPeriodDays);
            _topQuantile = topQuantile;
            _store = new FactorStore();   // ctor once — auto RegisterDefaults.
        }

        public IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            if (!IsRebalanceMonth(algorithm.Time))
                yield break;

            var symbols = algorithm.ActiveSecurities.Keys.ToList();
            if (symbols.Count == 0) yield break;

            var exchange = algorithm.ActiveSecurities.First().Value.Exchange.Hours;
            var asOf = ResolveFactorDate(symbols[0], algorithm.Time, exchange);
            if (asOf == null)
            {
                algorithm.Debug($"[Alpha101Composite] {algorithm.Time:yyyy-MM-dd}: no parquet; holding.");
                yield break;
            }

            var perSymbol = new Dictionary<Symbol, List<double>>();
            int alphasUsed = 0;
            foreach (var aid in AlphaIds)
            {
                var raw = new List<(Symbol sym, double v)>();
                foreach (var sym in symbols)
                {
                    var fr = _store.Get(aid, sym, asOf.Value);
                    if (fr.Quality != FactorDataQuality.Valid) continue;
                    raw.Add((sym, (double)fr.Value));
                }
                if (raw.Count < 10) continue;

                var mean = raw.Average(x => x.v);
                var std = Math.Sqrt(raw.Sum(x => (x.v - mean) * (x.v - mean)) / raw.Count);
                if (std < 1e-9) continue;

                int sign = DirectionSign[aid];
                foreach (var (sym, v) in raw)
                {
                    var z = sign * (v - mean) / std;
                    if (!perSymbol.TryGetValue(sym, out var list))
                        perSymbol[sym] = list = new List<double>();
                    list.Add(z);
                }
                alphasUsed++;
            }

            if (alphasUsed == 0 || perSymbol.Count == 0)
            {
                algorithm.Debug($"[Alpha101Composite] {algorithm.Time:yyyy-MM-dd}: all alphas empty; holding.");
                yield break;
            }

            var composite = perSymbol
                .Select(kv => (sym: kv.Key, score: kv.Value.Average()))
                .ToList();
            var ranked = composite.OrderByDescending(x => x.score).ToList();
            var n = Math.Max(1, (int)Math.Floor(ranked.Count * (double)_topQuantile));
            var selected = ranked.Take(n).ToList();

            var w = 1.0 / selected.Count;
            foreach (var x in selected)
            {
                yield return Insight.Price(
                    x.sym,
                    TimeSpan.FromDays(_insightPeriodDays),
                    InsightDirection.Up,
                    magnitude: x.score,
                    confidence: null,
                    sourceModel: Name,
                    weight: w);
            }

            algorithm.Debug($"[Alpha101Composite] {algorithm.Time:yyyy-MM-dd}: " +
                $"alphas_used={alphasUsed}/{AlphaIds.Length}, eligible={perSymbol.Count}, selected={selected.Count}.");

            _lastRebalanceYear = algorithm.Time.Year;
            _lastRebalanceMonth = algorithm.Time.Month;
        }

        public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes) { }

        /// <summary>Parquet keyed by yyyy-MM-dd folder. Walk back ≤7 trade days.
        /// Disambiguate "whole date missing" (folder absent) vs "per-symbol
        /// Missing" (folder present) via direct directory probe.</summary>
        private DateTime? ResolveFactorDate(Symbol probe, DateTime requested, SecurityExchangeHours hours)
        {
            var d = requested;
            for (int i = 0; i < 7; i++)
            {
                if (DateFolderExists("alpha001", d)) return d;
                d = hours.GetPrevTradingDay(d);
            }
            return null;
        }

        private static bool DateFolderExists(string alphaId, DateTime d)
        {
            var root = Path.Combine(Globals.DataFolder, "..", "result", "factor-zoo", alphaId);
            return Directory.Exists(Path.Combine(root, d.ToString("yyyy-MM-dd")));
        }

        private bool IsRebalanceMonth(DateTime now)
        {
            if (_lastRebalanceYear < 0) return true;
            if (now.Year == _lastRebalanceYear && now.Month == _lastRebalanceMonth) return false;
            var elapsed = (now.Year - _lastRebalanceYear) * 12 + (now.Month - _lastRebalanceMonth);
            return elapsed >= _rebalanceMonths;
        }
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `dotnet build QuantConnect.Lean.sln 2>&1 | tail -5 && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Alpha101CompositeAlphaModelTests" --no-build 2>&1 | tail -15`
Expected: PASS (2 tests). If `TestAlgorithm`/`Slice` ctor fails, align with `Tests/Algorithm/BasicTests.cs` pattern — the assertion intent is what matters; the stub structure is given.

- [ ] **Step 5: Commit**

```bash
git add Algorithm.CSharp/Models/Alpha/Alpha101CompositeAlphaModel.cs Tests/Common/Algorithm/Models/Alpha/Alpha101CompositeAlphaModelTests.cs
git commit -m "feat(csi300-alpha101): Task 2 Alpha101CompositeAlphaModel — first FactorStore consumer"
```

---

## Task 3: 五层策略主体 + LEAN config

**Files:**
- Create: `Algorithm.CSharp/AShareCSI300Alpha101CompositeStrategy.cs`
- Create: `Launcher/config/config-csi300-alpha101-composite.json`
- Test: `Tests/Common/Algorithm/AShareCSI300Alpha101CompositeStrategyTests.cs`

- [ ] **Step 1: Write the failing test**

`Tests/Common/Algorithm/AShareCSI300Alpha101CompositeStrategyTests.cs`:
```csharp
using System.Collections.Generic;
using System.Linq;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp;

namespace QuantConnect.Tests.Common.Algorithm
{
    [TestFixture]
    public class AShareCSI300Alpha101CompositeStrategyTests
    {
        [Test]
        public void Initialize_ReadsAlphaWeightsFromParameters()
        {
            var algo = new AShareCSI300Alpha101CompositeStrategy();
            algo.SetParameters(new Dictionary<string, string>
            {
                {"w_alpha001", "0.20"}, {"w_alpha006", "0.10"}, {"w_alpha030", "0.15"},
                {"w_alpha040", "0.05"}, {"w_alpha042", "0.10"}, {"w_alpha055", "0.10"},
                {"w_alpha058", "0.20"}, {"w_alpha101", "0.10"},
                {"zscore-threshold", "1.0"}, {"rebalance-days", "21"},
            });
            Assert.DoesNotThrow(() => algo.Initialize());
            Assert.AreEqual(1.0, algo.NormalizedWeights.Sum(), 1e-6);
        }

        [Test]
        public void Initialize_AllZeroWeights_FallsBackToEqualWeight()
        {
            var algo = new AShareCSI300Alpha101CompositeStrategy();
            algo.SetParameters(new Dictionary<string, string>
            {
                {"w_alpha001", "0"}, {"w_alpha006", "0"}, {"w_alpha030", "0"},
                {"w_alpha040", "0"}, {"w_alpha042", "0"}, {"w_alpha055", "0"},
                {"w_alpha058", "0"}, {"w_alpha101", "0"},
            });
            algo.Initialize();
            Assert.AreEqual(0.125, algo.NormalizedWeights[0], 1e-6);
            Assert.AreEqual(8, algo.NormalizedWeights.Length);
        }
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~AShareCSI300Alpha101CompositeStrategyTests" --no-build 2>&1 | tail -15`
Expected: FAIL — `AShareCSI300Alpha101CompositeStrategy` not found.

- [ ] **Step 3: Write minimal implementation**

`Algorithm.CSharp/AShareCSI300Alpha101CompositeStrategy.cs`:
```csharp
/*
 * AShareCSI300Alpha101CompositeStrategy — 5-layer A-share composite.
 * CSI300 universe (monthly refresh) → Alpha101CompositeAlphaModel (FactorStore
 * pull 8 alphas, z-score, composite, top 10% long) → EqualWeight PCM (monthly)
 * → MaxDrawdownRiskModel(0.20) → AShareLotSizeExecutionModel.
 *
 * Reads optimizer-injected blend weights via GetParameter("w_alphaNNN", 0.125).
 * Weights normalized to sum=1 in C# (manifest declares raw [0,1] for TPE).
 */
using System;
using System.Linq;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.CSharp.Models.Alpha;
using QuantConnect.Algorithm.CSharp.Universe;
using QuantConnect.Brokerages;

namespace QuantConnect.Algorithm.CSharp
{
    public class AShareCSI300Alpha101CompositeStrategy : QCAlgorithm
    {
        public double[] NormalizedWeights { get; private set; }

        public override void Initialize()
        {
            SetStartDate(2024, 1, 2);
            SetEndDate(2026, 6, 29);
            SetAccountCurrency(Currencies.CNY);
            SetCash(1_000_000);
            SetTimeZone(TimeZones.Shanghai);
            SetBenchmark(_ => 0m);
            Settings.MinimumOrderMarginPortfolioPercentage = 0;

            SetSecurityInitializer(new AShareStockSecurityInitializer());

            var raw = new[]
            {
                GetParameter("w_alpha001", 0.125),
                GetParameter("w_alpha006", 0.125),
                GetParameter("w_alpha030", 0.125),
                GetParameter("w_alpha040", 0.125),
                GetParameter("w_alpha042", 0.125),
                GetParameter("w_alpha055", 0.125),
                GetParameter("w_alpha058", 0.125),
                GetParameter("w_alpha101", 0.125),
            };
            double sum = raw.Sum();
            NormalizedWeights = (sum > 1e-9)
                ? raw.Select(w => w / sum).ToArray()
                : raw.Select(_ => 1.0 / raw.Length).ToArray();

            int rebalanceDays = GetParameter("rebalance-days", 21);

            SetUniverseSelection(new AShareCSI300UniverseSelectionModel());
            SetAlpha(new Alpha101CompositeAlphaModel(
                rebalanceMonths: 1,
                insightPeriodDays: rebalanceDays,
                topQuantile: 0.10m));
            SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel(
                Resolution.Daily));
            SetRiskManagement(new MaximumDrawdownPercentPortfolio(0.20m));
            SetExecution(new AShareLotSizeExecutionModel());
        }
    }
}
```

`Launcher/config/config-csi300-alpha101-composite.json`:
```json
{
  "environment": "backtesting",
  "algorithm-type-name": "AShareCSI300Alpha101CompositeStrategy",
  "algorithm-language": "CSharp",
  "algorithm-location": "QuantConnect.Algorithm.CSharp.dll",
  "data-folder": "../../../Data/",
  "data-provider": "QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider",
  "results-destination-folder": "../../../Results/csi300_alpha101",
  "period-start": "2024-01-02",
  "period-finish": "2026-06-29",
  "cash-amount": 1000000,
  "parameters": {
    "w_alpha001": "0.125",
    "w_alpha006": "0.125",
    "w_alpha030": "0.125",
    "w_alpha040": "0.125",
    "w_alpha042": "0.125",
    "w_alpha055": "0.125",
    "w_alpha058": "0.125",
    "w_alpha101": "0.125",
    "zscore-threshold": "1.0",
    "rebalance-days": "21"
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `dotnet build QuantConnect.Lean.sln 2>&1 | tail -5 && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~AShareCSI300Alpha101CompositeStrategyTests" --no-build 2>&1 | tail -15`
Expected: PASS (2 tests). Implementer: confirm exact namespaces for `AShareStockSecurityInitializer`, `AShareLotSizeExecutionModel`, `AShareCSI300UniverseSelectionModel`, `MaximumDrawdownPercentPortfolio` by grepping `AShareCrowdingFactorZooStrategy.cs` usings and adjust.

- [ ] **Step 5: Commit**

```bash
git add Algorithm.CSharp/AShareCSI300Alpha101CompositeStrategy.cs Launcher/config/config-csi300-alpha101-composite.json Tests/Common/Algorithm/AShareCSI300Alpha101CompositeStrategyTests.cs
git commit -m "feat(csi300-alpha101): Task 3 5-layer strategy + LEAN config"
```

---

## Task 4: Baseline 回测 smoke（Stage 4）

**Files:**
- Run: `dotnet Launcher.dll --config config-csi300-alpha101-composite.json`
- Create: `Results/csi300_alpha101/baseline_smoke.md`

**前置**: Task 1 回填已完成。本任务是手动验证，无新代码文件。

- [ ] **Step 1: 确认回填产物存在**

Run: `ls result/factor-zoo/alpha042/ | head -5 && echo "dates: $(ls result/factor-zoo/alpha042/ | wc -l)"`
Expected: 至少有 2024-01-02 起的目录。若空，先后台跑 `python Scripts/factor_zoo/backfill_alpha101_csi300.py --start 2024-01-02 --end 2026-06-29`（~30-90 分钟）。

- [ ] **Step 2: 缩短区间跑 3 月 smoke**

临时把 config 的 `period-finish` 改为 `2024-03-29`（或用临时 config 副本）。Run:
```bash
cd Launcher/bin/Debug
dotnet QuantConnect.Lean.Launcher.dll --config ../../config/config-csi300-alpha101-composite.json 2>&1 | tail -30
```
Expected: 生成 `Results/csi300_alpha101/AShareCSI300Alpha101CompositeStrategy-summary.json` + `-order-events.json`。summary 里 `Sharpe Ratio` 字段非空。

- [ ] **Step 3: 验 alpha 真实拉取**

在日志里确认 `[Alpha101Composite] alphas_used=8/8, eligible=300, selected=30` 出现过。若 `alphas_used=0`，检查回填目录路径与 `ResolveFactorDate` 的 `result/factor-zoo/...` 对齐。

- [ ] **Step 4: 落结果摘要**

把 baseline 3 月 summary 关键指标抄到 `Results/csi300_alpha101/baseline_smoke.md`（Sharpe / DD / NetProfit / TotalOrders / alphas_used）。这是 Task 8 bridge 落库的输入。

- [ ] **Step 5: Commit**

```bash
git add Results/csi300_alpha101/baseline_smoke.md
git commit -m "chore(csi300-alpha101): Task 4 baseline 3-month smoke results"
```

---

## Task 5: 复盘 Review Adapter（Stage 5 — proportional-z 归因）

**Files:**
- Create: `Scripts/review/adapters/csi300_alpha101.py`
- Test: `Tests/Python/test_review_csi300_alpha101_attribution.py`

**关键**: 归因和必须 = `trade.profit_loss`（`validated_layer_attribution` 会 raise）。proportional-z: `ρ_i = (w_i·z_i)/Σ_j(w_j·z_j)`, `C_i = ρ_i·P`。残差路径等分。

- [ ] **Step 1: Write the failing test**

`Tests/Python/test_review_csi300_alpha101_attribution.py`:
```python
"""Test Csi300Alpha101ReviewAdapter proportional-z attribution sums to profit_loss."""
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Scripts" / "review"))
from adapters.base import TradeRecord, TradeContext  # noqa: E402
from adapters.csi300_alpha101 import Csi300Alpha101ReviewAdapter  # noqa: E402


def _trade(profit_loss: str) -> TradeRecord:
    return TradeRecord(
        symbol="600519.SH", entry_time="2024-01-02T09:30:00",
        entry_price=Decimal("100"), exit_time="2024-02-02T09:30:00",
        exit_price=Decimal("110"), quantity=Decimal("100"), side="long",
        profit_loss=Decimal(profit_loss), fees=Decimal("5"),
        tpv_entry=Decimal("1000000"), order_ids=[],
    )


def test_proportional_z_sums_to_profit_loss():
    ad = Csi300Alpha101ReviewAdapter()
    trade = _trade("1000")
    ctx = TradeContext(
        entry_bar={"alpha_zscores": {"alpha_001": 0.5, "alpha_002": -0.3,
            "alpha_003": 0.2, "alpha_004": -0.1, "alpha_005": 0.4,
            "alpha_006": -0.2, "alpha_007": 0.1, "alpha_008": 0.3},
            "alpha_weights": {a: 0.125 for a in [
                "alpha_001","alpha_002","alpha_003","alpha_004",
                "alpha_005","alpha_006","alpha_007","alpha_008"]}},
        realized_bar={}, entry_signal={}, regime_at_entry={},
    )
    out = ad.layer_attribution(trade, ctx)
    assert set(out.keys()) == set(ad.LAYERS)
    total = sum(out.values())
    assert total == trade.profit_loss, f"sum {total} != {trade.profit_loss}"


def test_residual_equal_split_when_no_state_trace():
    ad = Csi300Alpha101ReviewAdapter()
    trade = _trade("800")
    ctx = TradeContext(entry_bar={}, realized_bar={}, entry_signal={}, regime_at_entry={})
    out = ad.layer_attribution(trade, ctx)
    assert all(v == Decimal("100") for v in out.values())  # 800/8 = 100
    assert sum(out.values()) == trade.profit_loss


def test_zero_blend_denominator_falls_back_to_equal():
    ad = Csi300Alpha101ReviewAdapter()
    trade = _trade("800")
    ctx = TradeContext(
        entry_bar={"alpha_zscores": {a: 0 for a in ad.LAYERS},
                   "alpha_weights": {a: 0.125 for a in ad.LAYERS}},
        realized_bar={}, entry_signal={}, regime_at_entry={})
    out = ad.layer_attribution(trade, ctx)
    assert sum(out.values()) == trade.profit_loss


def test_negative_alpha_gets_negative_share():
    ad = Csi300Alpha101ReviewAdapter()
    trade = _trade("1000")
    ctx = TradeContext(
        entry_bar={"alpha_zscores": {"alpha_001": 0.1, "alpha_002": -2.0,
            "alpha_003": 0.1, "alpha_004": 0.1, "alpha_005": 0.1,
            "alpha_006": 0.1, "alpha_007": 0.1, "alpha_008": 0.1},
            "alpha_weights": {a: 0.125 for a in ad.LAYERS}},
        realized_bar={}, entry_signal={}, regime_at_entry={})
    out = ad.layer_attribution(trade, ctx)
    assert out["alpha_002"] < 0, "alpha_002 voted opposite blend → negative share"
    assert sum(out.values()) == trade.profit_loss


def test_drift_absorbed_to_largest_alpha():
    """Decimal rounding drift absorbed so validated_layer_attribution never raises."""
    ad = Csi300Alpha101ReviewAdapter()
    trade = _trade("333")  # 333/8 not exact → drift.
    ctx = TradeContext(
        entry_bar={"alpha_zscores": {a: 0.5 for a in ad.LAYERS},
                   "alpha_weights": {a: 0.125 for a in ad.LAYERS}},
        realized_bar={}, entry_signal={}, regime_at_entry={})
    out = ad.validated_layer_attribution(trade, ctx)  # should NOT raise.
    assert sum(out.values()) == trade.profit_loss
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest Tests/Python/test_review_csi300_alpha101_attribution.py -v`
Expected: FAIL — `ModuleNotFoundError` for `adapters.csi300_alpha101`.

- [ ] **Step 3: Write minimal implementation**

`Scripts/review/adapters/csi300_alpha101.py`:
```python
"""Csi300Alpha101ReviewAdapter — per-alpha PnL attribution for the 8-alpha
composite strategy. Spec §5.4 (proportional-z), §3.5 (residual).

Attribution method (proportional-z):
  At rebalance t_e for symbol s, composite z = Σ_i (w_i * z_i,t,s).
  P = trade.profit_loss. We attribute:
      ρ_i = (w_i * z_i) / Σ_j (w_j * z_j)   (signed denominator)
      C_i = ρ_i * P
  Σ_i C_i = (Σ_i ρ_i) * P = 1 * P = P  (exact by construction).
  An alpha voting opposite the blend gets negative ρ_i.

Residual paths (sum still = P):
  - no entry_bar           → equal split C_i = P/8
  - zero-blend denominator → equal split
  - partial z (some missing)→ redistribute among present

State_trace contract: entry_bar (ts <= entry_time, no-lookahead) carries
  alpha_zscores {alpha_id: float} + alpha_weights {alpha_id: float} (default 1/8).

NOTE: LAYERS are LAYER ROLE names (alpha_001..alpha_008), NOT literal alpha ids.
This is so a refactor replacement alpha#Y can be attributed under the SAME
inspired_layer key (spec §5.5 critical contract for verify_layer_improvement).
"""
from decimal import Decimal

from .base import StrategyReviewAdapter, TradeRecord, TradeContext


class Csi300Alpha101ReviewAdapter(StrategyReviewAdapter):
    """Per-alpha attribution for the CSI300 8-alpha composite strategy."""

    # MUST match manifest review.layer_names exactly.
    LAYERS = [
        "alpha_001", "alpha_002", "alpha_003", "alpha_004",
        "alpha_005", "alpha_006", "alpha_007", "alpha_008",
    ]
    N_LAYERS = len(LAYERS)
    _EQUAL_WEIGHT = Decimal(1) / Decimal(N_LAYERS)

    @property
    def sum_to_property(self) -> str:
        return "profit_loss"

    def layer_attribution(self, trade: TradeRecord, context: TradeContext) -> dict:
        P = trade.profit_loss
        bar = (context.entry_bar if context else None) or {}

        # Residual 1: no state_trace → equal split (max-entropy prior).
        if not bar:
            equal = P / self.N_LAYERS
            return {ln: equal for ln in self.LAYERS}

        z_scores = bar.get("alpha_zscores") or {}
        weights = bar.get("alpha_weights") or {}
        default_w = self._EQUAL_WEIGHT

        numerators = {}
        for ln in self.LAYERS:
            z = z_scores.get(ln)
            w = weights.get(ln, default_w)
            if z is None:
                continue  # partial → skip, redistribute
            numerators[ln] = Decimal(str(w)) * Decimal(str(z))

        denom = sum(numerators.values())
        if not numerators or denom == 0:
            equal = P / self.N_LAYERS
            return {ln: equal for ln in self.LAYERS}

        out = {}
        for ln in self.LAYERS:
            num = numerators.get(ln)
            out[ln] = (num * P) / denom if num is not None else Decimal("0")

        # Absorb sub-ulp Decimal rounding drift into largest-magnitude alpha.
        drift = P - sum(out.values())
        if drift != 0:
            anchor = max(out, key=lambda k: abs(out[k]))
            out[anchor] += drift
        return out

    def trade_narrative(self, trade: TradeRecord, context: TradeContext) -> dict:
        bar = (context.entry_bar if context else None) or {}
        z_scores = bar.get("alpha_zscores") or {}
        weights = bar.get("alpha_weights") or {}
        composite_z = bar.get("composite_z")
        denom = sum(
            Decimal(str(weights.get(ln, self._EQUAL_WEIGHT))) * Decimal(str(z_scores.get(ln, 0)))
            for ln in self.LAYERS)
        if not bar or denom == 0:
            method = "residual-equal" if not bar else "proportional-z-zero-blend"
        elif not z_scores:
            method = "residual-equal"
        elif len(z_scores) < self.N_LAYERS:
            method = "proportional-z-partial"
        else:
            method = "proportional-z"
        return {
            "entry_signal": {"alpha_zscores": z_scores, "alpha_weights": weights,
                             "composite_z": composite_z},
            "regime_at_entry": (context.regime_at_entry if context else None) or {},
            "attribution_method": method,
            "insight_realized_vs_predicted": {"method": method},
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest Tests/Python/test_review_csi300_alpha101_attribution.py -v`
Expected: PASS (5 tests). If `TradeContext` dataclass fields differ from test kwargs, align with `Scripts/review/adapters/base.py:35-42` actual signature.

- [ ] **Step 5: Commit**

```bash
git add Scripts/review/adapters/csi300_alpha101.py Tests/Python/test_review_csi300_alpha101_attribution.py
git commit -m "feat(csi300-alpha101): Task 5 review adapter — proportional-z per-alpha attribution"
```

---

## Task 6: Bayes 优化 manifest + reward smoke（Stage 3）

**Files:**
- Create: `Scripts/auto_optimize/strategies/csi300_alpha101_composite/manifest.yaml`
- Test: `Tests/Python/AutoOptimize/test_csi300_alpha101_manifest.py`

**关键**: `lean_runner` 把参数 `str(v)` 注入 config `parameters`；C# `GetParameter("w_alphaNNN", 0.125)` double 重载读回。reward 读 `summary.json` 的 `"Sharpe Ratio"`。DSR gate 自动跑。

- [ ] **Step 1: Write the failing test**

`Tests/Python/AutoOptimize/test_csi300_alpha101_manifest.py`:
```python
"""Test csi300_alpha101 manifest parses 8 weight params + reward parses Sharpe."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Scripts" / "auto_optimize"))


def test_manifest_parses_8_weight_params():
    from manifest_loader import load_manifest
    m = load_manifest(str(Path(__file__).resolve().parents[2] /
        "Scripts" / "auto_optimize" / "strategies" / "csi300_alpha101_composite" / "manifest.yaml"))
    weights = [p for p in m.parameter_space if p.name.startswith("w_alpha")]
    assert len(weights) == 8
    for p in weights:
        assert p.type == "float"
        assert p.range == (0.0, 1.0)
        assert p.layer == "L2_Alpha"


def test_reward_parses_sharpe_ratio():
    from reward import compute_stationary_reward
    stats = {"Sharpe Ratio": "1.234", "Total Orders": "30"}
    out = compute_stationary_reward(stats)
    assert abs(out["sharpe"] - 1.234) < 1e-6


def test_dsr_gate_returns_passed_bool():
    from reward import compute_dsr_gate
    stats = {"Sharpe Ratio": "1.5", "Total Orders": "30"}
    out = compute_dsr_gate(stats, n_trials_total=200, baseline_sharpe=0.5)
    assert "passed" in out and isinstance(out["passed"], bool)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest Tests/Python/AutoOptimize/test_csi300_alpha101_manifest.py -v`
Expected: FAIL — manifest file not found.

- [ ] **Step 3: Write minimal implementation**

`Scripts/auto_optimize/strategies/csi300_alpha101_composite/manifest.yaml`:
```yaml
strategy_name: AShareCSI300Alpha101CompositeStrategy
rl_state_completeness: partial
lean_config: Launcher/config/config-csi300-alpha101-composite.json
risk_model_target: MaximumDrawdownPercentPortfolio
parameter_space:
  # 8 alpha blend weights, [0,1]. TPE samples as independent continuous dims.
  # layer=L2_Alpha = per-alpha blend coefficient (metadata only; optimize() never reads layer).
  - {name: w_alpha001, type: float, range: [0.0, 1.0], default: 0.125, layer: L2_Alpha}
  - {name: w_alpha006, type: float, range: [0.0, 1.0], default: 0.125, layer: L2_Alpha}
  - {name: w_alpha030, type: float, range: [0.0, 1.0], default: 0.125, layer: L2_Alpha}
  - {name: w_alpha040, type: float, range: [0.0, 1.0], default: 0.125, layer: L2_Alpha}
  - {name: w_alpha042, type: float, range: [0.0, 1.0], default: 0.125, layer: L2_Alpha}
  - {name: w_alpha055, type: float, range: [0.0, 1.0], default: 0.125, layer: L2_Alpha}
  - {name: w_alpha058, type: float, range: [0.0, 1.0], default: 0.125, layer: L2_Alpha}
  - {name: w_alpha101, type: float, range: [0.0, 1.0], default: 0.125, layer: L2_Alpha}
  # Composite gating / cadence — L3_Portfolio layer.
  - {name: zscore-threshold, type: float, range: [0.5, 2.5], default: 1.0, layer: L3_Portfolio}
  - {name: rebalance-days,    type: int,   range: [1, 21],  default: 21,  layer: L3_Portfolio}
state_schema:
  fields:
    - {name: ts, type: string}
    - {name: strategy, type: string}
    - {name: tpv, type: float}
    - {name: positions, type: array, item_schema: [sym, w, pnl_1d, days_held]}
    - {name: n_open_positions, type: int}
    - {name: alpha_zscores, type: object}
    - {name: alpha_weights, type: object}
    - {name: pnl, type: float}
  dim_hint: 12
reward_config:
  primary: dsr
  shaping:
    - {term: scaled_pnl, weight: 1.0}
    - {term: drawdown_excess_penalty, weight: 2.0}
universe:
  symbols: ["CSI300"]
  timezone: Asia/Shanghai
# Review/feedback/inspiration — wired to NEW adapters (gold2 untouched).
review:
  adapter_module: adapters.csi300_alpha101
  adapter_class: Csi300Alpha101ReviewAdapter
  layer_names: [alpha_001, alpha_002, alpha_003, alpha_004,
                alpha_005, alpha_006, alpha_007, alpha_008]
  review_schedule: on_backtest
  block_deploy: false
  thresholds:
    max_layer_attribution_gap: 0.20
    min_narrative_trades: 30
feedback:
  adapter_module: adapters.csi300_alpha101
  shaping_term_map:
    alpha_001: alpha_001_contrib_penalty
    alpha_002: alpha_002_contrib_penalty
    alpha_003: alpha_003_contrib_penalty
    alpha_004: alpha_004_contrib_penalty
    alpha_005: alpha_005_contrib_penalty
    alpha_006: alpha_006_contrib_penalty
    alpha_007: alpha_007_contrib_penalty
    alpha_008: alpha_008_contrib_penalty
inspiration:
  persistence:
    gap_threshold: 0.20
    min_generations: 3
  llm:
    config_key: glm
    model: glm-5.1
    temperature: 0.7
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest Tests/Python/AutoOptimize/test_csi300_alpha101_manifest.py -v`
Expected: PASS (3 tests). Implementer: confirm `load_manifest`/`compute_stationary_reward`/`compute_dsr_gate` exact signatures by reading `manifest_loader.py:67-93` and `reward.py:41,62`.

- [ ] **Step 5: Commit**

```bash
git add Scripts/auto_optimize/strategies/csi300_alpha101_composite/manifest.yaml Tests/Python/AutoOptimize/test_csi300_alpha101_manifest.py
git commit -m "feat(csi300-alpha101): Task 6 Bayes manifest + reward smoke"
```

---

## Task 7: 反馈 Adapter（Stage 6 — 低贡献 alpha 触发 hypothesize）

**Files:**
- Create: `Scripts/feedback/adapters/csi300_alpha101.py`
- Test: `Tests/Python/test_feedback_csi300_alpha101_trigger.py`

**关键**: `hypothesize.run` 是 strategy-agnostic。本 adapter 只检测"某 alpha 贡献为负持续 N 代"并调 hypothesize。不碰 `gold2.py`。

- [ ] **Step 1: Write the failing test**

`Tests/Python/test_feedback_csi300_alpha101_trigger.py`:
```python
"""Test csi300_alpha101 feedback adapter triggers hypothesize for low-contribution alpha."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Scripts" / "feedback"))


def test_low_contribution_alpha_triggers_hypothesize(tmp_path):
    from adapters.csi300_alpha101 import check_triggers

    review = {"layer_attribution": {
        "alpha_006": {"pnl_pct_of_total": -0.15, "n_trades": 30}}}
    gen_history = [
        {"generation": 1, "layer_gaps": {"alpha_006": {"gap": 0.25}},
         "shaping_overrides": {"alpha_006_contrib_penalty": 2.5}},
        {"generation": 2, "layer_gaps": {"alpha_006": {"gap": 0.22}},
         "shaping_overrides": {"alpha_006_contrib_penalty": 2.8}},
        {"generation": 3, "layer_gaps": {"alpha_006": {"gap": 0.20}},
         "shaping_overrides": {"alpha_006_contrib_penalty": 3.0}},
    ]
    manifest_raw = {"strategy_name": "AShareCSI300Alpha101CompositeStrategy",
                    "inspiration": {"persistence": {"gap_threshold": 0.20, "min_generations": 3},
                                    "llm": {"config_key": "glm", "model": "glm-5.1", "temperature": 0.7}}}
    with patch("adapters.csi300_alpha101.hypothesize_run") as mock_hyp:
        mock_hyp.return_value = str(tmp_path / "hypothesis.md")
        action = check_triggers(review, gen_history, manifest_raw,
                                hypothesis_dir=str(tmp_path))
    assert action is not None
    assert action.trigger == "review_drift"
    assert action.inspired_layer == "alpha_006"
    mock_hyp.assert_called_once()


def test_optimizing_layer_skipped(tmp_path):
    """A layer with gap < threshold does NOT trigger."""
    from adapters.csi300_alpha101 import check_triggers
    review = {"layer_attribution": {
        "alpha_006": {"pnl_pct_of_total": -0.05, "n_trades": 30}}}
    gen_history = [{"generation": 1, "layer_gaps": {"alpha_006": {"gap": 0.10}},
                    "shaping_overrides": {"alpha_006_contrib_penalty": 0.5}}]
    manifest_raw = {"strategy_name": "x",
                    "inspiration": {"persistence": {"gap_threshold": 0.20, "min_generations": 3},
                                    "llm": {"config_key": "glm"}}}
    with patch("adapters.csi300_alpha101.hypothesize_run") as mock_hyp:
        action = check_triggers(review, gen_history, manifest_raw,
                                hypothesis_dir=str(tmp_path))
    assert action is None
    mock_hyp.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest Tests/Python/test_feedback_csi300_alpha101_trigger.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

`Scripts/feedback/adapters/csi300_alpha101.py`:
```python
"""Csi300Alpha101 feedback adapter — detects low-contribution alpha and
triggers hypothesize.run to propose a replacement alpha (Stage 6 refactor).

Mirrors the gold2 feedback trigger pattern but does NOT modify gold2.py
(never-modify-existing-features). hypothesize.run is strategy-agnostic.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "Scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "Scripts"))

from inspiration.hypothesize import run as hypothesize_run  # noqa: E402


@dataclass
class FeedbackAction:
    trigger: str
    inspired_layer: str
    hypothesis_path: str
    trigger_reason: str


def check_triggers(review: dict, gen_history: list, manifest_raw: dict,
                   hypothesis_dir: str = "Results/soloquant/local-strategies"
                   ) -> Optional[FeedbackAction]:
    """Fire hypothesize for any alpha layer with persistent negative contribution
    AND non-convergent shaping weight (gap > threshold for >= min_generations)."""
    inspiration = manifest_raw.get("inspiration", {})
    persistence = inspiration.get("persistence", {})
    gap_threshold = persistence.get("gap_threshold", 0.20)
    min_generations = persistence.get("min_generations", 3)

    layer_attr = review.get("layer_attribution", {})

    for layer, agg in layer_attr.items():
        pnl_pct = float(agg.get("pnl_pct_of_total", 0))
        if pnl_pct >= 0:
            continue

        gaps = [g.get("layer_gaps", {}).get(layer, {}).get("gap", 0)
                for g in gen_history]
        weights = [g.get("shaping_overrides", {}).get(f"{layer}_contrib_penalty", 0)
                   for g in gen_history]
        if len(gaps) < min_generations:
            continue
        recent_gaps = gaps[-min_generations:]
        if not all(g > gap_threshold for g in recent_gaps):
            continue
        if weights and not (weights[-1] >= 3.0 or _trending_up(weights[-min_generations:])):
            continue

        strategy_name = manifest_raw.get("strategy_name", "Csi300Alpha101")
        llm_cfg = inspiration.get("llm", {})
        path = hypothesize_run(
            strategy_name=strategy_name,
            inspired_layer=layer,
            review_doc=review,
            gen_history=gen_history,
            manifest_raw=manifest_raw,
            llm_cfg=llm_cfg,
            hypothesis_dir=hypothesis_dir,
        )
        return FeedbackAction(
            trigger="review_drift",
            inspired_layer=layer,
            hypothesis_path=path,
            trigger_reason=f"{layer} pnl_pct={pnl_pct:.3f} for {min_generations} gens, gap>{gap_threshold}",
        )
    return None


def _trending_up(seq: list) -> bool:
    return len(seq) >= 2 and seq[-1] > seq[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest Tests/Python/test_feedback_csi300_alpha101_trigger.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/feedback/adapters/csi300_alpha101.py Tests/Python/test_feedback_csi300_alpha101_trigger.py
git commit -m "feat(csi300-alpha101): Task 7 feedback adapter — low-contribution alpha → hypothesize"
```

---

## Task 8: 阶段结果 Bridge（§7 — 落 InfluxDB + CSV）

**Files:**
- Create: `Scripts/csi300_alpha101/stage_bridge.py`
- Test: `Tests/Python/test_csi300_alpha101_stage_bridge.py`

**关键**: 复用 `export_backtest_results_to_influx.py` 的 `parse_numeric_value`/`InfluxPoint`（import 不改）。写 2 个新 measurement。绝不写既有 measurement。

- [ ] **Step 1: Write the failing test**

`Tests/Python/test_csi300_alpha101_stage_bridge.py`:
```python
"""Test stage_bridge writes 2 new measurements + CSV, doesn't touch existing."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Scripts"))


def _full_review():
    return {"layer_attribution": {
        "alpha_001": {"pnl_pct_of_total": "0.20", "pnl_abs": "200", "n_trades": 30, "n_wins": 17},
        "alpha_002": {"pnl_pct_of_total": "-0.05", "pnl_abs": "-50", "n_trades": 30, "n_wins": 10},
        "alpha_003": {"pnl_pct_of_total": "0.15", "pnl_abs": "150", "n_trades": 30, "n_wins": 16},
        "alpha_004": {"pnl_pct_of_total": "0.10", "pnl_abs": "100", "n_trades": 30, "n_wins": 15},
        "alpha_005": {"pnl_pct_of_total": "0.12", "pnl_abs": "120", "n_trades": 30, "n_wins": 15},
        "alpha_006": {"pnl_pct_of_total": "0.08", "pnl_abs": "80", "n_trades": 30, "n_wins": 14},
        "alpha_007": {"pnl_pct_of_total": "0.20", "pnl_abs": "200", "n_trades": 30, "n_wins": 18},
        "alpha_008": {"pnl_pct_of_total": "0.20", "pnl_abs": "200", "n_trades": 30, "n_wins": 17}}}


def test_bridge_builds_stage_and_attribution_points():
    from csi300_alpha101.stage_bridge import build_points
    summary = {"statistics": {"Sharpe Ratio": "1.2", "Drawdown": "10%",
        "Net Profit": "15%", "Total Orders": "30", "Win Rate": "55%",
        "Sortino Ratio": "1.5", "Compounding Annual Return": "8%"}}
    stage_meta = {"strategy_id": "csi300_alpha101_composite", "stage_type": "baseline",
                  "variant_id": "baseline", "generation": 0}
    params = {f"w_alpha{n}": "0.125" for n in
              ["alpha001","alpha006","alpha030","alpha040",
               "alpha042","alpha055","alpha058","alpha101"]}
    points = build_points(summary, _full_review(), stage_meta, params, ts_ns=1234)
    stage_pts = [p for p in points if p.measurement == "csi300_alpha101_stage"]
    attr_pts = [p for p in points if p.measurement == "csi300_alpha101_alpha_attribution"]
    assert len(stage_pts) == 1
    assert stage_pts[0].fields["sharpe"] == 1.2
    assert stage_pts[0].tags["stage_type"] == "baseline"
    assert len(attr_pts) == 8
    assert attr_pts[0].tags["alpha_id"].startswith("alpha_")


def test_bridge_appends_csv_row(tmp_path):
    from csi300_alpha101.stage_bridge import append_csv
    csv_path = tmp_path / "stage_results.csv"
    row = {"stage_type": "baseline", "generation": 0, "variant_id": "baseline",
           "sharpe": 1.2, "drawdown": 10.0}
    append_csv(csv_path, row)
    append_csv(csv_path, row)
    lines = csv_path.read_text().strip().split("\n")
    assert len(lines) == 3  # header + 2 rows
    assert "stage_type" in lines[0]


def test_bridge_never_writes_existing_measurements():
    from csi300_alpha101.stage_bridge import build_points
    summary = {"statistics": {"Sharpe Ratio": "1.0", "Total Orders": "1"}}
    points = build_points(summary, _full_review(),
        {"strategy_id": "x", "stage_type": "baseline", "variant_id": "b", "generation": 0},
        {}, ts_ns=1)
    measurements = {p.measurement for p in points}
    assert measurements == {"csi300_alpha101_stage", "csi300_alpha101_alpha_attribution"}
    assert "lean_backtest_stat" not in measurements
    assert "soloquant_pipeline_funnel" not in measurements
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest Tests/Python/test_csi300_alpha101_stage_bridge.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

`Scripts/csi300_alpha101/stage_bridge.py`:
```python
"""stage_bridge — write per-stage backtest results to 2 NEW InfluxDB measurements
+ append to CSV, so optimization/refactor gains are visible in Grafana.

Reuses export_backtest_results_to_influx helpers (parse_numeric_value,
InfluxPoint) WITHOUT modifying that file. Writes ONLY csi300_alpha101_*
measurements — never lean_backtest_stat / soloquant_pipeline_funnel / barra_*.
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "Scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "Scripts"))

import yaml  # noqa: E402
from export_backtest_results_to_influx import parse_numeric_value, InfluxPoint  # noqa: E402

INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "admin-token-leansystem")

STAGE_MEASUREMENT = "csi300_alpha101_stage"
ATTR_MEASUREMENT = "csi300_alpha101_alpha_attribution"
CSV_PATH = _REPO / "Results" / "csi300_alpha101" / "stage_results.csv"

# Layer role names (alpha_001..alpha_008) → actual alpha ids for weight lookup.
LAYER_TO_ALPHA = {
    "alpha_001": "alpha001", "alpha_002": "alpha006", "alpha_003": "alpha030",
    "alpha_004": "alpha040", "alpha_005": "alpha042", "alpha_006": "alpha055",
    "alpha_007": "alpha058", "alpha_008": "alpha101",
}


def build_points(summary: dict, review: dict, stage_meta: dict,
                 params: dict, ts_ns: int) -> list[InfluxPoint]:
    """Build InfluxPoints for 1 stage row + 8 alpha attribution rows."""
    stats = summary.get("statistics") or summary.get("Statistics") or {}
    strategy_id = stage_meta["strategy_id"]
    stage_type = stage_meta["stage_type"]
    variant_id = stage_meta["variant_id"]
    generation = str(stage_meta["generation"])

    stage_fields = {
        "sharpe": parse_numeric_value(stats.get("Sharpe Ratio")) or 0.0,
        "sortino": parse_numeric_value(stats.get("Sortino Ratio")) or 0.0,
        "drawdown": parse_numeric_value(stats.get("Drawdown")) or 0.0,
        "net_profit": parse_numeric_value(stats.get("Net Profit")) or 0.0,
        "compounding_annual_return": parse_numeric_value(stats.get("Compounding Annual Return")) or 0.0,
        "total_orders": parse_numeric_value(stats.get("Total Orders")) or 0.0,
        "win_rate": parse_numeric_value(stats.get("Win Rate")) or 0.0,
    }
    for layer, alpha_id in LAYER_TO_ALPHA.items():
        w = parse_numeric_value(params.get(f"w_{alpha_id}"))
        stage_fields[f"weight_{alpha_id}"] = w or 0.0
    if stage_type == "optimize_champion":
        stage_fields["dsr_passed"] = float(bool(stage_meta.get("dsr_passed", 0)))

    stage_tags = {"strategy_id": strategy_id, "stage_type": stage_type,
                  "variant_id": variant_id, "generation": generation}
    points = [InfluxPoint(measurement=STAGE_MEASUREMENT, tags=stage_tags,
                          fields=stage_fields, timestamp_ns=ts_ns)]

    layer_attr = review.get("layer_attribution", {})
    for layer, alpha_id in LAYER_TO_ALPHA.items():
        agg = layer_attr.get(layer, {})
        w = parse_numeric_value(params.get(f"w_{alpha_id}")) or 0.0
        attr_fields = {
            "pnl_pct_of_total": parse_numeric_value(agg.get("pnl_pct_of_total")) or 0.0,
            "pnl_abs": parse_numeric_value(agg.get("pnl_abs")) or 0.0,
            "n_trades": parse_numeric_value(agg.get("n_trades")) or 0.0,
            "n_wins": parse_numeric_value(agg.get("n_wins")) or 0.0,
            "weight": w,
        }
        attr_tags = {"strategy_id": strategy_id, "alpha_id": layer,
                     "stage_type": stage_type, "variant_id": variant_id,
                     "generation": generation}
        points.append(InfluxPoint(measurement=ATTR_MEASUREMENT, tags=attr_tags,
                                  fields=attr_fields, timestamp_ns=ts_ns))
    return points


def append_csv(csv_path: Path, row: dict) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    exists = csv_path.exists()
    cols = ["stage_type", "generation", "variant_id", "sharpe", "sortino",
            "drawdown", "net_profit", "total_orders", "win_rate", "dsr_passed",
            "alpha_001_contrib_pct", "alpha_002_contrib_pct", "alpha_003_contrib_pct",
            "alpha_004_contrib_pct", "alpha_005_contrib_pct", "alpha_006_contrib_pct",
            "alpha_007_contrib_pct", "alpha_008_contrib_pct",
            "w_alpha001", "w_alpha006", "w_alpha030", "w_alpha040",
            "w_alpha042", "w_alpha055", "w_alpha058", "w_alpha101"]
    with open(csv_path, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(cols)
        w.writerow([row.get(c, "") for c in cols])


def write_influx(points: list[InfluxPoint]) -> int:
    from urllib import parse, request
    lines = []
    for p in points:
        tag_str = ",".join(f"{k}={parse.quote(str(v))}" for k, v in p.tags.items())
        field_str = ",".join(f"{k}={v}" if isinstance(v, float) else f'{k}="{v}"'
                              for k, v in p.fields.items())
        lines.append(f"{p.measurement},{tag_str} {field_str} {p.timestamp_ns}")
    body = "\n".join(lines).encode()
    req = request.Request(
        f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}",
        data=body, method="POST",
        headers={"Authorization": f"Token {INFLUX_TOKEN}",
                 "Content-Type": "text/plain; charset=utf-8"})
    try:
        request.urlopen(req, timeout=10)
        return len(lines)
    except Exception:
        return 0


def run(summary_path: Path, review_path: Path, manifest_path: Path,
        stage_meta: dict) -> int:
    """Read summary + review + manifest params, build points, write Influx + CSV."""
    import json
    from datetime import datetime, timezone
    summary = json.loads(summary_path.read_text())
    review = json.loads(review_path.read_text())
    manifest = yaml.safe_load(manifest_path.read_text()) if manifest_path.exists() else {}
    params = manifest.get("parameters", {})

    ts_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    points = build_points(summary, review, stage_meta, params, ts_ns)
    written = write_influx(points)

    row = {"stage_type": stage_meta["stage_type"], "generation": stage_meta["generation"],
           "variant_id": stage_meta["variant_id"]}
    s = points[0].fields
    row.update({"sharpe": s.get("sharpe", ""), "sortino": s.get("sortino", ""),
                "drawdown": s.get("drawdown", ""), "net_profit": s.get("net_profit", ""),
                "total_orders": s.get("total_orders", ""), "win_rate": s.get("win_rate", ""),
                "dsr_passed": int(s.get("dsr_passed", 0))})
    for i, p in enumerate(points[1:]):
        row[f"alpha_{i+1:03d}_contrib_pct"] = p.fields.get("pnl_pct_of_total", "")
    for layer, alpha_id in LAYER_TO_ALPHA.items():
        row[f"w_{alpha_id}"] = params.get(f"w_{alpha_id}", "")
    append_csv(CSV_PATH, row)
    return written


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--summary", required=True)
    p.add_argument("--review", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--stage-type", required=True)
    p.add_argument("--variant-id", required=True)
    p.add_argument("--generation", type=int, default=0)
    args = p.parse_args()
    n = run(Path(args.summary), Path(args.review), Path(args.manifest),
            {"strategy_id": "csi300_alpha101_composite", "stage_type": args.stage_type,
             "variant_id": args.variant_id, "generation": args.generation})
    print(f"wrote {n} points")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest Tests/Python/test_csi300_alpha101_stage_bridge.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add Scripts/csi300_alpha101/stage_bridge.py Tests/Python/test_csi300_alpha101_stage_bridge.py
git commit -m "feat(csi300-alpha101): Task 8 stage_bridge — 2 new measurements + CSV"
```

---

## Task 9: Grafana Dashboard + E2E 验证

**Files:**
- Create: `monitoring/grafana/dashboards/lean/csi300-alpha101-loop.json`
- Run: E2E manual verify

- [ ] **Step 1: Write the dashboard JSON**

`monitoring/grafana/dashboards/lean/csi300-alpha101-loop.json`. The implementer should copy `__inputs`/`__requires`/`templating` scaffolding from `monitoring/grafana/dashboards/lean/ashare-multi-family.json` (same InfluxDB InfluxQL datasource), then set these 5 panels:

```json
{
  "title": "CSI300 Alpha101 策略开发环路",
  "schemaVersion": 39,
  "time": {"from": "now-7d", "to": "now"},
  "panels": [
    {
      "id": 1, "title": "阶段 Sharpe 演化", "type": "graph",
      "datasource": {"type": "influxdb"},
      "targets": [{
        "query": "SELECT mean(\"sharpe\") FROM \"csi300_alpha101_stage\" WHERE $timeFilter GROUP BY \"stage_type\", \"variant_id\" fill(linear)",
        "rawQuery": true
      }],
      "xaxis": {"mode": "time"}, "yaxis": [{"format": "short"}]
    },
    {
      "id": 2, "title": "阶段回撤/净收益", "type": "graph",
      "datasource": {"type": "influxdb"},
      "targets": [
        {"query": "SELECT mean(\"drawdown\") FROM \"csi300_alpha101_stage\" WHERE $timeFilter GROUP BY \"variant_id\" fill(linear)", "rawQuery": true},
        {"query": "SELECT mean(\"net_profit\") FROM \"csi300_alpha101_stage\" WHERE $timeFilter GROUP BY \"variant_id\" fill(linear)", "rawQuery": true}
      ]
    },
    {
      "id": 3, "title": "每 alpha 贡献热力图", "type": "heatmap",
      "datasource": {"type": "influxdb"},
      "targets": [{
        "query": "SELECT mean(\"pnl_pct_of_total\") FROM \"csi300_alpha101_alpha_attribution\" WHERE $timeFilter GROUP BY \"alpha_id\", \"variant_id\"",
        "rawQuery": true
      }]
    },
    {
      "id": 4, "title": "8 alpha 权重演化", "type": "graph",
      "datasource": {"type": "influxdb"},
      "targets": [
        {"query": "SELECT mean(\"weight_alpha001\") FROM \"csi300_alpha101_stage\" WHERE $timeFilter GROUP BY \"variant_id\" fill(linear)", "rawQuery": true},
        {"query": "SELECT mean(\"weight_alpha006\") FROM \"csi300_alpha101_stage\" WHERE $timeFilter GROUP BY \"variant_id\" fill(linear)", "rawQuery": true},
        {"query": "SELECT mean(\"weight_alpha030\") FROM \"csi300_alpha101_stage\" WHERE $timeFilter GROUP BY \"variant_id\" fill(linear)", "rawQuery": true},
        {"query": "SELECT mean(\"weight_alpha040\") FROM \"csi300_alpha101_stage\" WHERE $timeFilter GROUP BY \"variant_id\" fill(linear)", "rawQuery": true},
        {"query": "SELECT mean(\"weight_alpha042\") FROM \"csi300_alpha101_stage\" WHERE $timeFilter GROUP BY \"variant_id\" fill(linear)", "rawQuery": true},
        {"query": "SELECT mean(\"weight_alpha055\") FROM \"csi300_alpha101_stage\" WHERE $timeFilter GROUP BY \"variant_id\" fill(linear)", "rawQuery": true},
        {"query": "SELECT mean(\"weight_alpha058\") FROM \"csi300_alpha101_stage\" WHERE $timeFilter GROUP BY \"variant_id\" fill(linear)", "rawQuery": true},
        {"query": "SELECT mean(\"weight_alpha101\") FROM \"csi300_alpha101_stage\" WHERE $timeFilter GROUP BY \"variant_id\" fill(linear)", "rawQuery": true}
      ],
      "stack": {"enabled": true, "mode": "percent"}
    },
    {
      "id": 5, "title": "DSR gate 状态", "type": "stat",
      "datasource": {"type": "influxdb"},
      "targets": [{
        "query": "SELECT last(\"dsr_passed\") FROM \"csi300_alpha101_stage\" WHERE \"stage_type\"='optimize_champion' AND $timeFilter",
        "rawQuery": true
      }]
    }
  ]
}
```

- [ ] **Step 2: E2E — baseline 3 月回测 + 复盘 + bridge**

Prerequisite: Task 1 回填已跑。Run:
```bash
cd Launcher/bin/Debug
dotnet QuantConnect.Lean.Launcher.dll --config ../../config/config-csi300-alpha101-composite.json 2>&1 | tail -20
cd ../../..
python Scripts/review/run_review.py --manifest Scripts/auto_optimize/strategies/csi300_alpha101_composite/manifest.yaml --results-dir Results/csi300_alpha101 2>&1 | tail -10
python Scripts/csi300_alpha101/stage_bridge.py \
  --summary Results/csi300_alpha101/AShareCSI300Alpha101CompositeStrategy-summary.json \
  --review Results/csi300_alpha101/review/review.json \
  --manifest Scripts/auto_optimize/strategies/csi300_alpha101_composite/manifest.yaml \
  --stage-type baseline --variant-id baseline --generation 0
```
Expected: bridge prints `wrote 9 points`（1 stage + 8 attribution）。review.json 8 alpha 各有非空 `pnl_pct_of_total`，和≈1。

- [ ] **Step 3: 验 InfluxDB + CSV**

Run:
```bash
curl -s -G "http://127.0.0.1:8086/query" \
  --data-urlencode "u=admin" --data-urlencode "p=admin" \
  --data-urlencode "q=SELECT count(*) FROM quant..csi300_alpha101_stage" 2>&1 | tail -3
tail -3 Results/csi300_alpha101/stage_results.csv
```
Expected: stage measurement count ≥1。CSV 有 header + ≥1 行。

- [ ] **Step 4: 验 dashboard 打开**

在 Grafana UI 导入/刷新 `csi300-alpha101-loop` dashboard，确认 5 面板非空（baseline 已落库）。

- [ ] **Step 5: Commit + 总结**

```bash
git add monitoring/grafana/dashboards/lean/csi300-alpha101-loop.json
git commit -m "feat(csi300-alpha101): Task 9 Grafana dashboard + e2e verify"
```

---

## Self-Review

**1. Spec coverage:**
- §1 目标（首个 FactorStore 消费者 + 6 阶段环路）→ Task 2（消费）+ Tasks 1-9（环路）✓
- §2 8 alpha 精选 + 复合公式 → Task 2 Alpha101CompositeAlphaModel ✓
- §3 组件清单 8 文件 → Tasks 1,2,3,5,6,7,8,9 全覆盖 ✓
- §4 数据流 6 阶段 → Task 1(回填) / 2-3(策略) / 6(优化) / 4(回测) / 5(复盘) / 7(重构) ✓
- §5 关键正确性点 → Task 2(ctor一次/Missing/日期对齐) / 3(权重注入) / 5(和不变量) / 7(重构契约) ✓
- §6 测试计划 → 每任务 TDD ✓
- §7 可视化 → Task 8(bridge) + Task 9(dashboard) ✓
- §8 验收 9 条 → Task 4(build/test) / 2-3(C# tests) / 1+5+6+7+8(Python tests) / 4(real disk) ✓
- §9 风险 → 各任务 Step 备注对策 ✓

**2. Placeholder scan:** 无 TBD/TODO。C# test 的 `TestAlgorithm` stub 与 namespace 备注是必要实现提示（stub 结构已给，非 placeholder）。`AShareStockSecurityInitializer`/`AShareLotSizeExecutionModel` namespace 备注让 implementer 对齐 `AShareCrowdingFactorZooStrategy` ——必要因跨文件无法保证 namespace 精确。

**3. Type consistency:** `Alpha101CompositeAlphaModel` ctor（rebalanceMonths/insightPeriodDays/topQuantile）Task 2 定义、Task 3 调用一致。`NormalizedWeights` Task 3 定义+测试一致。`build_points`/`append_csv`/`write_influx`/`run` Task 8 定义+测试一致。`Csi300Alpha101ReviewAdapter.LAYERS`（alpha_001..alpha_008）Task 5 定义、Task 6 manifest `layer_names`、Task 8 `LAYER_TO_ALPHA` 三处一致。`check_triggers`/`FeedbackAction` Task 7 定义+测试一致。

**已修正:** Task 1 `trade_days_in_range` 去除重复 return。Task 8 顶部补 `import yaml`。

Plan complete.
