# Gold2 Beta+波动率目标策略实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个完整五层 Framework 的 gold2 beta 暴露+波动率目标策略,对齐 spec `docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md`。

**Architecture:** Factor Zoo(Common/Factors/Forward/) 四个 `IFactor` 实现:`Gold2TrendFactor`(MA20/120+AU 确认,Runtime)、`Gold2VolRegimeFactor`(EWMA σ,有状态 Update)、`Gold2ExtremeRiskFactor`(VIX P95 OR RVol P95)、`Gold2RealRateCapFactor`(包装已有 `GoldRealRateRegimeFactor`)。Model Zoo(Algorithm.CSharp/Models/Gold2/)四个 Model:`Gold2TrendAlphaModel`(L2)、`Gold2VolTargetPortfolioModel`(L3 dead-zone)、`Gold2ExtremeRiskModel`+`Gold2RealRateCapModel`(L4 硬 cap 串联)。Strategy(Algorithm.CSharp/) `Gold2BetaVolTargetStrategy` 拼装五层。FRED 宏观走 `FredMacroData` custom BaseData;AU.SHF 走 `AuShfDailyBar` custom BaseData(回填 CSV)。三个脚本:fetch_fred_macro.py、backfill_au_lean.py、gold2_data_gates.py。

**Tech Stack:** C# .NET 10(LEAN)、NUnit、Python 3(pandas/requests/pyarrow)、FRED API、tushare parquet。

**关键 API 事实(已核实):**
- `IFactor.Compute(Symbol, DateTime, IEnumerable<BaseData> history = null)` 返回 `FactorResult` struct(`Value`/`RawValue` 为 decimal,`Quality` 为 `FactorDataQuality`);**无 `FactorValue` 类型**。
- `FactorResult` 无构造函数,用 `new FactorResult { ... }` 对象初始化器。
- `FactorCategory`:`Trend/Value/Volatility/Quality/Sentiment/Liquidity/Chip/Forward`;`FactorComputeMode`:`Precomputed/Runtime`。
- `AlphaModel.Update(QCAlgorithm, Slice)` 返回 `IEnumerable<Insight>`;`Insight` 构造:`new Insight(symbol, TimeSpan period, InsightType type, InsightDirection direction, double? magnitude, double? confidence, string sourceModel, double? weight, string tag)`。
- `PortfolioConstructionModel.CreateTargets(QCAlgorithm, Insight[])` 返回 `IEnumerable<IPortfolioTarget>`;`PortfolioTarget.Percent(IAlgorithm, Symbol, decimal percent)` 返回 `IPortfolioTarget`(自动算 quantity)。
- `IRiskManagementModel.ManageRisk(QCAlgorithm, IPortfolioTarget[] targets)` 返回 `IEnumerable<IPortfolioTarget>`(注意是数组)。
- `ManualUniverseSelectionModel(IEnumerable<Symbol>)` 构造时传 symbols。
- AShare 模型命名空间:`AShareStockFeeModel`→`QuantConnect.Orders.Fees`、`AShareStockFillModel`→`QuantConnect.Orders.Fills`、`AShareStockBuyingPowerModel`/`DelayedSettlementModel`→`QuantConnect.Securities`、`AShareLotSizeExecutionModel`→`QuantConnect.Algorithm.Framework.Execution`。
- `IOptimizableStrategy.GetTunableParameterNames()` 与 `IRlStateExportable.SerializeRlState(QCAlgorithm)` 在 `QuantConnect.Algorithm.CSharp.Common`。
- `RollingWindow<T>` 与 `SimpleMovingAverage` 在 `QuantConnect.Indicators`。

---

## Task 1: FRED 宏观数据下载脚本 + FredMacroData custom BaseData

**Files:**
- Create: `Scripts/fetch_fred_macro.py`
- Create: `Common/Data/Custom/Gold/FredMacroData.cs`
- Create: `Data/macro/fred/vix.csv`(脚本生成)
- Create: `Data/macro/fred/dfii10.csv`(脚本生成)

- [ ] **Step 1: 写 fetch_fred_macro.py**

```python
#!/usr/bin/env python3
"""下载 FRED VIX(VIXCLS) 与 10Y TIPS 实际利率(DFII10)到 Data/macro/fred/。
挂 cron 每日 06:00 刷新。tushare 仍是价格主体,FRED 补宏观。"""
import os, sys, requests, pandas as pd

FRED_KEY = os.environ.get("FRED_API_KEY")
if not FRED_KEY:
    print("ERROR: FRED_API_KEY not set", file=sys.stderr); sys.exit(1)
SERIES = {"VIX": "VIXCLS", "DFII10": "DFII10"}
OUT = os.path.join(os.path.dirname(__file__), "..", "Data", "macro", "fred")
os.makedirs(OUT, exist_ok=True)
for name, sid in SERIES.items():
    url = (f"https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={sid}&api_key={FRED_KEY}&file_type=json&observation_start=2015-01-01")
    r = requests.get(url, timeout=60).json()
    df = pd.DataFrame(r["observations"])[["date", "value"]]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna()
    df.to_csv(os.path.join(OUT, f"{name.lower()}.csv"), index=False)
    print(f"{name}: {len(df)} rows, {df['date'].min()} -> {df['date'].max()}")
```

- [ ] **Step 2: 运行脚本生成 CSV**

Run: `cd /home/project/hope/Lean && FRED_API_KEY=6ecfdca59519da091e9dc0223dd674cb python3 Scripts/fetch_fred_macro.py`
Expected: 打印 `VIX: N rows` 与 `DFII10: N rows`,生成 `Data/macro/fred/vix.csv` 和 `dfii10.csv`,首行 `date,value`。

- [ ] **Step 3: 写 FredMacroData.cs(镜像 GoldOvernightSignal 的 Reader/GetSource 模式)**

```csharp
using System;
using System.Globalization;
using System.IO;
using QuantConnect.Data;

namespace QuantConnect.Data.Custom.Gold
{
    /// <summary>
    /// FRED 宏观序列 custom data。Symbol.Value 决定文件: "VIX"->vix.csv, "DFII10"->dfii10.csv。
    /// 列: date,value。时区 UTC 日级对齐。
    /// 详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §6.1。
    /// </summary>
    public class FredMacroData : BaseData
    {
        public decimal MacroValue { get; set; }
        public override DateTime EndTime => Time;

        public override BaseData Reader(SubscriptionDataConfig config, string line, DateTime dateSpecified, bool isLiveMode)
        {
            if (string.IsNullOrWhiteSpace(line) || line.StartsWith("date")) return null;
            var csv = line.Split(',');
            if (csv.Length < 2) return null;
            if (!DateTime.TryParseExact(csv[0], "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var d))
                return null;
            if (!decimal.TryParse(csv[1], NumberStyles.Any, CultureInfo.InvariantCulture, out var v)) return null;
            return new FredMacroData { Symbol = config.Symbol, Time = d, MacroValue = v, Value = v };
        }

        public override SubscriptionDataSource GetSource(SubscriptionDataConfig config, DateTime date, bool isLiveMode)
        {
            var fname = config.Symbol.Value.ToLower() + ".csv";
            var path = Path.Combine(Globals.DataFolder, "macro", "fred", fname);
            return new SubscriptionDataSource(path, SubscriptionTransportMedium.LocalFile, FileFormat.Csv);
        }
    }
}
```

- [ ] **Step 4: 构建确认编译通过**

Run: `cd /home/project/hope/Lean && dotnet build QuantConnect.Lean.sln 2>&1 | tail -5`
Expected: Build succeeded(0 errors)。若 Common.csproj 未自动包含新文件,检查 sdk-style csproj 是否 glob `**/*.cs`(默认应包含)。

- [ ] **Step 5: 提交**

```bash
cd /home/project/hope/Lean
git add Scripts/fetch_fred_macro.py Common/Data/Custom/Gold/FredMacroData.cs Data/macro/fred/vix.csv Data/macro/fred/dfii10.csv
git commit -m "feat(gold2): FRED macro fetch script + FredMacroData custom BaseData

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: AU.SHF 回填脚本 + AuShfDailyBar custom BaseData

**Files:**
- Create: `Scripts/backfill_au_lean.py`
- Create: `Common/Data/Custom/Gold/AuShfDailyBar.cs`
- Create: `Data/future/shf/daily/AU.SHF.csv`(脚本生成)

- [ ] **Step 1: 写 backfill_au_lean.py**

```python
#!/usr/bin/env python3
"""把 tushare fut_daily AU.SHF parquet 转成 LEAN 可读 CSV: Data/future/shf/daily/AU.SHF.csv
列: date,open,high,low,close,volume,open_interest。因子只读不交易。"""
import os, pandas as pd

SRC = "/home/project/tushare-downloader/tushare_data_v2/fut_daily/ts_code=AU.SHF/data.parquet"
OUT = os.path.join(os.path.dirname(__file__), "..", "Data", "future", "shf", "daily", "AU.SHF.csv")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
df = pd.read_parquet(SRC)
df["date"] = df["trade_date"].astype(str)
cols = ["date", "open", "high", "low", "close", "vol"]
if "oi" in df.columns: cols.append("oi")
out = df[cols].copy()
rename = {"vol": "volume"}
if "oi" in df.columns: rename["oi"] = "open_interest"
out = out.rename(columns=rename)
out = out.sort_values("date")
out.to_csv(OUT, index=False)
print(f"AU.SHF: {len(out)} rows -> {OUT}, {out['date'].min()} -> {out['date'].max()}")
```

- [ ] **Step 2: 运行脚本生成 CSV**

Run: `cd /home/project/hope/Lean && python3 Scripts/backfill_au_lean.py`
Expected: 打印 `AU.SHF: ~4492 rows`,生成 `Data/future/shf/daily/AU.SHF.csv`。

- [ ] **Step 3: 写 AuShfDailyBar.cs**

```csharp
using System;
using System.Globalization;
using System.IO;
using QuantConnect.Data;

namespace QuantConnect.Data.Custom.Gold
{
    /// <summary>
    /// AU.SHF 期货日线 custom data(辅助趋势确认,不交易)。
    /// 列: date,open,high,low,close,volume[,open_interest]。yyyy-MM-dd 格式。
    /// 详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §6.4。
    /// </summary>
    public class AuShfDailyBar : BaseData
    {
        public decimal Open { get; set; }
        public decimal High { get; set; }
        public decimal Low { get; set; }
        public decimal Close { get; set; }
        public decimal Volume { get; set; }
        public override DateTime EndTime => Time;

        public override BaseData Reader(SubscriptionDataConfig config, string line, DateTime dateSpecified, bool isLiveMode)
        {
            if (string.IsNullOrWhiteSpace(line) || line.StartsWith("date")) return null;
            var csv = line.Split(',');
            if (csv.Length < 5) return null;
            if (!DateTime.TryParseExact(csv[0], "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var d))
                return null;
            var bar = new AuShfDailyBar { Symbol = config.Symbol, Time = d };
            bar.Open = decimal.TryParse(csv[1], NumberStyles.Any, CultureInfo.InvariantCulture, out var o) ? o : 0m;
            bar.High = decimal.TryParse(csv[2], NumberStyles.Any, CultureInfo.InvariantCulture, out var h) ? h : 0m;
            bar.Low = decimal.TryParse(csv[3], NumberStyles.Any, CultureInfo.InvariantCulture, out var l) ? l : 0m;
            bar.Close = decimal.TryParse(csv[4], NumberStyles.Any, CultureInfo.InvariantCulture, out var c) ? c : 0m;
            if (csv.Length > 5) bar.Volume = decimal.TryParse(csv[5], NumberStyles.Any, CultureInfo.InvariantCulture, out var v) ? v : 0m;
            bar.Value = bar.Close;
            return bar;
        }

        public override SubscriptionDataSource GetSource(SubscriptionDataConfig config, DateTime date, bool isLiveMode)
        {
            var path = Path.Combine(Globals.DataFolder, "future", "shf", "daily", "AU.SHF.csv");
            return new SubscriptionDataSource(path, SubscriptionTransportMedium.LocalFile, FileFormat.Csv);
        }
    }
}
```

- [ ] **Step 4: 构建确认**

Run: `cd /home/project/hope/Lean && dotnet build QuantConnect.Lean.sln 2>&1 | tail -3`
Expected: Build succeeded。

- [ ] **Step 5: 提交**

```bash
cd /home/project/hope/Lean
git add Scripts/backfill_au_lean.py Common/Data/Custom/Gold/AuShfDailyBar.cs Data/future/shf/daily/AU.SHF.csv
git commit -m "feat(gold2): AU.SHF backfill script + AuShfDailyBar custom BaseData

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: Gold2TrendFactor(MA20/120 + AU 同向确认)

**Files:**
- Create: `Common/Factors/Forward/Gold2TrendFactor.cs`
- Test: `Tests/Common/Factors/Gold2TrendFactorTests.cs`

- [ ] **Step 1: 写失败测试**

`Tests/Common/Factors/Gold2TrendFactorTests.cs`:
```csharp
using System;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Tests.Common.Factors
{
    [TestFixture]
    public class Gold2TrendFactorTests
    {
        // 设计 §4.1: TrendSignal = sign(MA20-MA120); Confirm=同号?1:0.5
        [Test]
        public void Trend_BothUp_Confirmed_ReturnsPosDirConfirm1()
        {
            var f = new Gold2TrendFactor(20, 120);
            for (int i = 0; i < 130; i++) f.Update518880(100m + i, new DateTime(2020,1,2).AddDays(i));
            for (int i = 0; i < 130; i++) f.UpdateAu(100m + i, new DateTime(2020,1,2).AddDays(i));
            var r = f.Compute(Symbol.Empty, new DateTime(2020,6,15));
            Assert.AreEqual(1m, r.Value, "518880 多头方向 +1");
            Assert.AreEqual(1m, r.RawValue, "AU 同向 confirm=1");
        }

        [Test]
        public void Trend_Divergent_ConfirmHalved()
        {
            var f = new Gold2TrendFactor(20, 120);
            for (int i = 0; i < 130; i++) f.Update518880(100m + i, new DateTime(2020,1,2).AddDays(i));
            for (int i = 0; i < 130; i++) f.UpdateAu(230m - i, new DateTime(2020,1,2).AddDays(i));
            var r = f.Compute(Symbol.Empty, new DateTime(2020,6,15));
            Assert.AreEqual(1m, r.Value, "518880 仍多头");
            Assert.AreEqual(0.5m, r.RawValue, "AU 反向 confirm=0.5");
        }

        [Test]
        public void Trend_Bearish_ReturnsNegDir()
        {
            var f = new Gold2TrendFactor(20, 120);
            for (int i = 0; i < 130; i++) f.Update518880(230m - i, new DateTime(2020,1,2).AddDays(i));
            for (int i = 0; i < 130; i++) f.UpdateAu(230m - i, new DateTime(2020,1,2).AddDays(i));
            var r = f.Compute(Symbol.Empty, new DateTime(2020,6,15));
            Assert.AreEqual(-1m, r.Value, "空头方向 -1");
        }

        [Test]
        public void Trend_Warmup_ReturnsZero()
        {
            var f = new Gold2TrendFactor(20, 120);
            for (int i = 0; i < 10; i++) f.Update518880(100m + i, new DateTime(2020,1,2).AddDays(i));
            var r = f.Compute(Symbol.Empty, new DateTime(2020,1,12));
            Assert.AreEqual(0m, r.Value, "MA120 未满 TrendSignal=0");
        }

        [Test]
        public void NoLookahead_MA_OnlyUsesPastClose()
        {
            var f = new Gold2TrendFactor(20, 120);
            for (int i = 0; i < 130; i++) f.Update518880(100m + i, new DateTime(2020,1,2).AddDays(i));
            var r1 = f.Compute(Symbol.Empty, new DateTime(2020,6,15));
            f.Update518880(9999m, new DateTime(2020,6,16));
            var r2 = f.Compute(Symbol.Empty, new DateTime(2020,6,15));
            Assert.AreEqual(r1.Value, r2.Value, "Compute(t) 不读 t+1");
        }
    }
}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2TrendFactorTests" 2>&1 | tail -8`
Expected: FAIL — `Gold2TrendFactor` 类型不存在。

- [ ] **Step 3: 写实现**

`Common/Factors/Forward/Gold2TrendFactor.cs`:
```csharp
using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Indicators;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// gold2 趋势方向因子(Runtime)。MA20/120 sign + AU.SHF 同向确认。
    /// Update518880/UpdateAu 由 Strategy OnData 喂 close;Compute 查询当前 SMA。
    /// 详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §4.1。
    /// </summary>
    public class Gold2TrendFactor : IFactor
    {
        private readonly SimpleMovingAverage _ma518880Short;
        private readonly SimpleMovingAverage _ma518880Long;
        private readonly SimpleMovingAverage _maAuShort;
        private readonly SimpleMovingAverage _maAuLong;

        public string Id => "gold2_trend";
        public string Name => "Gold2 Trend (MA20/120 + AU confirm)";
        public FactorCategory Category => FactorCategory.Trend;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
        public string DataSource => "518880_fund_daily+au_shf_fut_daily";

        public Gold2TrendFactor(int shortPeriod = 20, int longPeriod = 120)
        {
            _ma518880Short = new SimpleMovingAverage(shortPeriod);
            _ma518880Long = new SimpleMovingAverage(longPeriod);
            _maAuShort = new SimpleMovingAverage(shortPeriod);
            _maAuLong = new SimpleMovingAverage(longPeriod);
        }

        public void Update518880(decimal close, DateTime time)
        {
            _ma518880Short.Update(time, close);
            _ma518880Long.Update(time, close);
        }

        public void UpdateAu(decimal close, DateTime time)
        {
            _maAuShort.Update(time, close);
            _maAuLong.Update(time, close);
        }

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            if (!_ma518880Long.IsReady || !_maAuLong.IsReady)
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing, Value = 0m };
            decimal trend518880 = Math.Sign(_ma518880Short.Current.Value - _ma518880Long.Current.Value);
            decimal trendAu = Math.Sign(_maAuShort.Current.Value - _maAuLong.Current.Value);
            decimal confirm = (trend518880 == trendAu && trend518880 != 0m) ? 1m : 0.5m;
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = trend518880, RawValue = confirm };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => _ma518880Long.IsReady && _maAuLong.IsReady;
    }
}
```

注意:`SimpleMovingAverage.Update(DateTime, decimal)` 是 LEAN Indicator 常见签名之一。**实现时先 Read `Indicators/SimpleMovingAverage.cs` 确认 Update 签名**(可能是 `Update(IndicatorDataPoint)` 或 `Update(time, value)`),按实际签名调整这两个 Update 方法。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2TrendFactorTests" 2>&1 | tail -8`
Expected: 5/5 PASS。若 SMA Update 签名不匹配,修正调用后重试。

- [ ] **Step 5: 提交**

```bash
cd /home/project/hope/Lean
git add Common/Factors/Forward/Gold2TrendFactor.cs Tests/Common/Factors/Gold2TrendFactorTests.cs
git commit -m "feat(gold2): Gold2TrendFactor MA20/120+AU confirm with tests

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: Gold2VolRegimeFactor(EWMA σ + 平滑 w_smooth)

**Files:**
- Create: `Common/Factors/Forward/Gold2VolRegimeFactor.cs`
- Test: `Tests/Common/Factors/Gold2VolRegimeFactorTests.cs`

- [ ] **Step 1: 写失败测试**

```csharp
using System;
using System.Collections.Generic;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Tests.Common.Factors
{
    [TestFixture]
    public class Gold2VolRegimeFactorTests
    {
        // 设计 §4.2: EWMA σ(λ=0.94), w=min(1, σ_target/σ_ann), w_smooth=α·w+(1-α)·w_prev
        [Test]
        public void Warmup_ReturnsZeroWeight()
        {
            var f = new Gold2VolRegimeFactor(0.94m, 0.11m, 60, 0.25m);
            for (int i = 0; i < 10; i++) f.Update(100m + i, new DateTime(2020,1,2).AddDays(i));
            var r = f.Compute(Symbol.Empty, new DateTime(2020,1,12));
            Assert.AreEqual(0m, r.Value, "前 60 日 w=0");
        }

        [Test]
        public void SteadyLowVol_ConvergesToFullWeight()
        {
            var f = new Gold2VolRegimeFactor(0.94m, 0.11m, 60, 0.25m);
            for (int i = 0; i < 200; i++) f.Update(100m * (decimal)Math.Pow(1.001, i), new DateTime(2020,1,2).AddDays(i));
            var r = f.Compute(Symbol.Empty, new DateTime(2020,7,20));
            Assert.AreEqual(1.0m, r.Value, 0.01m, "低波动 → w≈1.0");
        }

        [Test]
        public void VolSpike_HalvesWeight()
        {
            var f = new Gold2VolRegimeFactor(0.94m, 0.22m, 60, 1.0m);
            for (int i = 0; i < 60; i++) f.Update(100m, new DateTime(2020,1,2).AddDays(i));
            var baseR = f.Compute(Symbol.Empty, new DateTime(2020,3,5));
            for (int i = 0; i < 60; i++) f.Update(100m * (i % 2 == 0 ? 1.05m : 0.95m), new DateTime(2020,3,6).AddDays(i));
            var spikeR = f.Compute(Symbol.Empty, new DateTime(2020,6,5));
            Assert.Greater(spikeR.RawValue, baseR.RawValue, "波动率上升 σ_ann 增大");
        }

        [Test]
        public void NoLookahead_EWMA_UsesPrevReturn()
        {
            var f = new Gold2VolRegimeFactor(0.94m, 0.11m, 60, 0.25m);
            for (int i = 0; i < 100; i++) f.Update(100m + i, new DateTime(2020,1,2).AddDays(i));
            var r1 = f.Compute(Symbol.Empty, new DateTime(2020,4,11));
            f.Update(9999m, new DateTime(2020,4,12));
            var r2 = f.Compute(Symbol.Empty, new DateTime(2020,4,11));
            Assert.AreEqual(r1.Value, r2.Value, "Compute(t) 不读 t+1 收益");
        }
    }
}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2VolRegimeFactorTests" 2>&1 | tail -8`
Expected: FAIL — 类型不存在。

- [ ] **Step 3: 写实现**

`Common/Factors/Forward/Gold2VolRegimeFactor.cs`:
```csharp
using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// gold2 波动率目标仓位因子(Runtime,有状态 EWMA)。
    /// r=ln(P_t/P_{t-1}); σ²_t=λσ²_{t-1}+(1-λ)r²_{t-1}; w=min(1,σ_target/σ_ann); w_smooth=αw+(1-α)w_prev。
    /// Update 喂 close;Compute 查询 w_smooth(Value) 与 σ_ann(RawValue)。
    /// 详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md §4.2。
    /// </summary>
    public class Gold2VolRegimeFactor : IFactor
    {
        private readonly decimal _lambda, _volTarget, _alpha;
        private readonly int _warmup;
        private readonly Queue<decimal> _warmupReturns = new();
        private decimal _sigma2Prev, _wSmoothPrev, _prevClose;
        private bool _ewmaReady;

        public string Id => "gold2_vol_regime";
        public string Name => "Gold2 Vol-Target (EWMA λ=0.94)";
        public FactorCategory Category => FactorCategory.Volatility;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
        public string DataSource => "518880_fund_daily";

        public Gold2VolRegimeFactor(decimal lambda, decimal volTarget, int warmup, decimal alpha)
        {
            _lambda = lambda; _volTarget = volTarget; _warmup = warmup; _alpha = alpha;
        }

        public void Update(decimal close, DateTime time)
        {
            if (_prevClose > 0)
            {
                var r = (decimal)Math.Log((double)(close / _prevClose));
                _warmupReturns.Enqueue(r);
                if (!_ewmaReady)
                {
                    if (_warmupReturns.Count >= _warmup)
                    {
                        _sigma2Prev = Variance(_warmupReturns);
                        _ewmaReady = true;
                    }
                }
                else
                {
                    _sigma2Prev = _lambda * _sigma2Prev + (1m - _lambda) * r * r;
                    var sigmaAnn = (decimal)Math.Sqrt((double)_sigma2Prev) * (decimal)Math.Sqrt(252);
                    var w = sigmaAnn > 0 ? Math.Min(1.0m, _volTarget / sigmaAnn) : 1.0m;
                    _wSmoothPrev = _alpha * w + (1m - _alpha) * _wSmoothPrev;
                }
            }
            _prevClose = close;
        }

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            if (!_ewmaReady)
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing, Value = 0m };
            var sigmaAnn = (decimal)Math.Sqrt((double)_sigma2Prev) * (decimal)Math.Sqrt(252);
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = _wSmoothPrev, RawValue = sigmaAnn };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => _ewmaReady;

        public static decimal Variance(IEnumerable<decimal> xs)
        {
            var list = new List<decimal>(xs);
            if (list.Count < 2) return 0m;
            var mean = 0m; foreach (var x in list) mean += x; mean /= list.Count;
            var v = 0m; foreach (var x in list) v += (x - mean) * (x - mean);
            return v / list.Count;
        }
    }
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2VolRegimeFactorTests" 2>&1 | tail -8`
Expected: 4/4 PASS。

- [ ] **Step 5: 提交**

```bash
cd /home/project/hope/Lean
git add Common/Factors/Forward/Gold2VolRegimeFactor.cs Tests/Common/Factors/Gold2VolRegimeFactorTests.cs
git commit -m "feat(gold2): Gold2VolRegimeFactor EWMA+smoothing with tests

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: Gold2ExtremeRiskFactor(VIX P95 OR RVol P95)

**Files:**
- Create: `Common/Factors/Forward/Gold2ExtremeRiskFactor.cs`
- Test: `Tests/Common/Factors/Gold2ExtremeRiskFactorTests.cs`

- [ ] **Step 1: 写失败测试**

```csharp
using System;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Tests.Common.Factors
{
    [TestFixture]
    public class Gold2ExtremeRiskFactorTests
    {
        // 设计 §4.3: Trigger = VIX_t > VIX_P95 OR RVol_60d > RVol_P95
        [Test]
        public void Warmup_ReturnsNotTriggered()
        {
            var f = new Gold2ExtremeRiskFactor(minWindow: 252);
            var r = f.Compute(Symbol.Empty, new DateTime(2020,1,2));
            Assert.AreEqual(0m, r.Value, "窗口未满不触发");
        }

        [Test]
        public void VixAboveP95_Triggers()
        {
            var f = new Gold2ExtremeRiskFactor(minWindow: 100);
            var t0 = new DateTime(2020,1,2);
            for (int i = 0; i < 100; i++) f.UpdateVix(15m, t0.AddDays(i));
            f.UpdateVix(50m, t0.AddDays(100));
            var r = f.Compute(Symbol.Empty, t0.AddDays(100));
            Assert.AreEqual(1m, r.Value, "VIX>P95 触发");
        }

        [Test]
        public void NormalVix_NoTrigger()
        {
            var f = new Gold2ExtremeRiskFactor(minWindow: 100);
            var t0 = new DateTime(2020,1,2);
            for (int i = 0; i < 100; i++) f.UpdateVix(15m + (i % 3), t0.AddDays(i));
            var r = f.Compute(Symbol.Empty, t0.AddDays(100));
            Assert.AreEqual(0m, r.Value, "正常区间不触发");
        }

        [Test]
        public void RVolAboveP95_TriggersEvenWithNormalVix()
        {
            var f = new Gold2ExtremeRiskFactor(minWindow: 100);
            var t0 = new DateTime(2020,1,2);
            for (int i = 0; i < 100; i++) { f.UpdateVix(15m, t0.AddDays(i)); f.UpdateRvol60(0.10m, t0.AddDays(i)); }
            f.UpdateRvol60(0.90m, t0.AddDays(100));
            f.UpdateVix(15m, t0.AddDays(100));
            var r = f.Compute(Symbol.Empty, t0.AddDays(100));
            Assert.AreEqual(1m, r.Value, "RVol>P95 触发(GPR 代理)");
        }
    }
}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2ExtremeRiskFactorTests" 2>&1 | tail -8`
Expected: FAIL — 类型不存在。

- [ ] **Step 3: 写实现**

`Common/Factors/Forward/Gold2ExtremeRiskFactor.cs`:
```csharp
using System;
using System.Collections.Generic;
using System.Linq;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Indicators;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// gold2 极端风险开关因子(Runtime)。Trigger=VIX_t>VIX_P95 OR RVol_60d>RVol_P95。
    /// 窗口满 minWindow(默认252)才计算 P95。详见 spec §4.3。
    /// </summary>
    public class Gold2ExtremeRiskFactor : IFactor
    {
        private readonly RollingWindow<decimal> _vixHist;
        private readonly RollingWindow<decimal> _rvolHist;
        private readonly int _minWindow;
        private decimal _todayVix, _todayRvol;

        public string Id => "gold2_extreme_risk";
        public string Name => "Gold2 Extreme Risk (VIX/RVol P95)";
        public FactorCategory Category => FactorCategory.Volatility;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
        public string DataSource => "fred_vix+518880_realized_vol";

        public Gold2ExtremeRiskFactor(int vixWindow = 1260, int minWindow = 252)
        {
            _vixHist = new RollingWindow<decimal>(vixWindow);
            _rvolHist = new RollingWindow<decimal>(vixWindow);
            _minWindow = minWindow;
        }

        public void UpdateVix(decimal vix, DateTime time)
        {
            _vixHist.Add(vix);
            _todayVix = vix;
        }

        public void UpdateRvol60(decimal rvolAnn, DateTime time)
        {
            _rvolHist.Add(rvolAnn);
            _todayRvol = rvolAnn;
        }

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            if (_vixHist.Count < _minWindow && _rvolHist.Count < _minWindow)
                return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Missing, Value = 0m, RawValue = _todayRvol };

            bool triggered = false;
            if (_vixHist.Count >= _minWindow)
            {
                var p95 = Percentile(_vixHist, 0.95);
                if (_todayVix > p95) triggered = true;
            }
            if (!triggered && _rvolHist.Count >= _minWindow)
            {
                var p95 = Percentile(_rvolHist, 0.95);
                if (_todayRvol > p95) triggered = true;
            }
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = triggered ? 1m : 0m, RawValue = _todayRvol };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => _vixHist.Count >= _minWindow || _rvolHist.Count >= _minWindow;

        public static decimal Percentile(IEnumerable<decimal> xs, double q)
        {
            var sorted = xs.OrderBy(x => x).ToList();
            if (sorted.Count == 0) return decimal.MaxValue;
            int idx = (int)Math.Ceiling(q * sorted.Count) - 1;
            if (idx < 0) idx = 0;
            if (idx >= sorted.Count) idx = sorted.Count - 1;
            return sorted[idx];
        }
    }
}
```

注意:`RollingWindow<T>` 构造参数为容量,`.Add(item)` 入队,`.Count` 当前元素数。**实现前 Read `Common/Indicators/RollingWindow.cs` 确认 API**。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2ExtremeRiskFactorTests" 2>&1 | tail -8`
Expected: 4/4 PASS。

- [ ] **Step 5: 提交**

```bash
cd /home/project/hope/Lean
git add Common/Factors/Forward/Gold2ExtremeRiskFactor.cs Tests/Common/Factors/Gold2ExtremeRiskFactorTests.cs
git commit -m "feat(gold2): Gold2ExtremeRiskFactor VIX/RVol P95 trigger with tests

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: Gold2RealRateCapFactor(包装 GoldRealRateRegimeFactor)

**Files:**
- Create: `Common/Factors/Forward/Gold2RealRateCapFactor.cs`
- Test: `Tests/Common/Factors/Gold2RealRateCapFactorTests.cs`

- [ ] **Step 1: 写失败测试**

```csharp
using System;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Tests.Common.Factors
{
    [TestFixture]
    public class Gold2RealRateCapFactorTests
    {
        // 设计 §4.4: Cap = RISING_FAST ? 0.6 : 1.0; UNAVAILABLE→1.0
        [Test]
        public void RisingFast_CapsTo06()
        {
            var inner = new GoldRealRateRegimeFactor();
            inner.InjectRegime(Symbol.Empty, GoldRegime.RISING_FAST);
            var f = new Gold2RealRateCapFactor(inner, 0.6m);
            var r = f.Compute(Symbol.Empty, new DateTime(2020,1,2));
            Assert.AreEqual(0.6m, r.Value);
        }

        [Test]
        public void Stable_NoCap()
        {
            var inner = new GoldRealRateRegimeFactor();
            inner.InjectRegime(Symbol.Empty, GoldRegime.STABLE);
            var f = new Gold2RealRateCapFactor(inner, 0.6m);
            var r = f.Compute(Symbol.Empty, new DateTime(2020,1,2));
            Assert.AreEqual(1.0m, r.Value);
        }

        [Test]
        public void Unavailable_NoCapNotBlocked()
        {
            var inner = new GoldRealRateRegimeFactor();
            var f = new Gold2RealRateCapFactor(inner, 0.6m);
            var r = f.Compute(Symbol.Empty, new DateTime(2020,1,2));
            Assert.AreEqual(1.0m, r.Value, "DFII10 缺失不阻断 cap=1.0");
        }
    }
}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2RealRateCapFactorTests" 2>&1 | tail -8`
Expected: FAIL — 类型不存在。

- [ ] **Step 3: 写实现**

`Common/Factors/Forward/Gold2RealRateCapFactor.cs`:
```csharp
using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Forward
{
    /// <summary>
    /// gold2 实际利率帽子因子。包装 GoldRealRateRegimeFactor,RISING_FAST→cap(0.6),其余→1.0。
    /// 与 ExtremeRisk 串联在 L4 取 min。详见 spec §4.4。
    /// </summary>
    public class Gold2RealRateCapFactor : IFactor
    {
        private readonly GoldRealRateRegimeFactor _inner;
        private readonly decimal _risingFastCap;

        public string Id => "gold2_real_rate_cap";
        public string Name => "Gold2 Real-Rate Cap";
        public FactorCategory Category => FactorCategory.Forward;
        public FactorScope Scope => FactorScope.TimeSeries;
        public FactorComputeMode ComputeMode => FactorComputeMode.Runtime;
        public string DataSource => "fred_dfii10";

        public Gold2RealRateCapFactor(GoldRealRateRegimeFactor inner, decimal risingFastCap = 0.6m)
        {
            _inner = inner; _risingFastCap = risingFastCap;
        }

        public FactorResult Compute(Symbol symbol, DateTime time, IEnumerable<BaseData> history = null)
        {
            var inner = _inner.Compute(symbol, time, history);
            var regime = (GoldRegime)(decimal)inner.Value;
            var cap = regime == GoldRegime.RISING_FAST ? _risingFastCap : 1.0m;
            return new FactorResult { Symbol = symbol, Time = time, FactorId = Id, Quality = FactorDataQuality.Valid, Value = cap, RawValue = (decimal)regime };
        }

        public FactorRankResult ComputeRank(IEnumerable<Symbol> symbols, DateTime time) => new FactorRankResult { Time = time, FactorId = Id };
        public bool IsAvailable(Symbol symbol, DateTime time) => true;
    }
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2RealRateCapFactorTests" 2>&1 | tail -8`
Expected: 3/3 PASS。

- [ ] **Step 5: 提交**

```bash
cd /home/project/hope/Lean
git add Common/Factors/Forward/Gold2RealRateCapFactor.cs Tests/Common/Factors/Gold2RealRateCapFactorTests.cs
git commit -m "feat(gold2): Gold2RealRateCapFactor wrapping RealRateRegime with tests

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: L1 Universe + L2 TrendAlphaModel

**Files:**
- Create: `Algorithm.CSharp/Models/Gold2/Gold2UniverseSelectionModel.cs`
- Create: `Algorithm.CSharp/Models/Gold2/Gold2TrendAlphaModel.cs`
- Test: `Tests/Algorithm/Gold2TrendAlphaModelTests.cs`

- [ ] **Step 1: 写失败测试**

```csharp
using System;
using NUnit.Framework;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.CSharp.Models.Gold2;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class Gold2TrendAlphaModelTests
    {
        private static readonly Symbol _gold = Symbol.Create("518880", SecurityType.Equity, Market.SSE);

        // 设计 §5.2: dir>0 → Up weight=confirm; 否则 Flat weight=floor
        [Test]
        public void TrendUp_Confirmed_UpInsightWeight1()
        {
            var trend = new Gold2TrendFactor(20, 120);
            var model = new Gold2TrendAlphaModel(trend, _gold, 0.2m);
            for (int i = 0; i < 130; i++) trend.Update518880(100m + i, new DateTime(2020,1,2).AddDays(i));
            for (int i = 0; i < 130; i++) trend.UpdateAu(100m + i, new DateTime(2020,1,2).AddDays(i));
            var algo = new TestAlgo();
            algo.SetDateTime(new DateTime(2020,6,15));
            var insights = model.Update(algo, null);
            foreach (var ins in insights)
            {
                Assert.AreEqual(InsightDirection.Up, ins.Direction);
                Assert.AreEqual(1.0, ins.Weight, 0.001);
            }
        }

        [Test]
        public void TrendBearish_FlatInsightWeightFloor()
        {
            var trend = new Gold2TrendFactor(20, 120);
            var model = new Gold2TrendAlphaModel(trend, _gold, 0.2m);
            for (int i = 0; i < 130; i++) trend.Update518880(230m - i, new DateTime(2020,1,2).AddDays(i));
            for (int i = 0; i < 130; i++) trend.UpdateAu(230m - i, new DateTime(2020,1,2).AddDays(i));
            var algo = new TestAlgo(); algo.SetDateTime(new DateTime(2020,6,15));
            var insights = model.Update(algo, null);
            foreach (var ins in insights)
            {
                Assert.AreEqual(InsightDirection.Flat, ins.Direction);
                Assert.AreEqual(0.2, ins.Weight, 0.001);
            }
        }

        public class TestAlgo : QuantConnect.Algorithm.QCAlgorithm { }
    }
}
```

注意:`AlphaModel.Update(algo, null)` 传 null Slice —— 实现里不读 Slice(因子状态由 Strategy OnData 推进),所以 null 安全。若 LEAN 框架在真实调用路径要求非 null,改用空 Slice stub。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2TrendAlphaModelTests" 2>&1 | tail -8`
Expected: FAIL — 类型不存在。

- [ ] **Step 3: 写实现**

`Algorithm.CSharp/Models/Gold2/Gold2UniverseSelectionModel.cs`:
```csharp
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2
{
    /// <summary>gold2 L1 Universe: 固定 518880 SSE。AU.SHF 不入 universe(因子内取值)。</summary>
    public class Gold2UniverseSelectionModel : ManualUniverseSelectionModel
    {
        public Gold2UniverseSelectionModel() : base(new[]
        {
            Symbol.Create("518880", SecurityType.Equity, Market.SSE)
        }) { }
    }
}
```

`Algorithm.CSharp/Models/Gold2/Gold2TrendAlphaModel.cs`:
```csharp
using System;
using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Factors.Forward;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2
{
    /// <summary>
    /// gold2 L2 Alpha: 消费 Gold2TrendFactor。dir>0→Up weight=confirm;否则 Flat weight=floor。
    /// long-only(518880 不可做空)。详见 spec §5.2。
    /// </summary>
    public class Gold2TrendAlphaModel : AlphaModel
    {
        private readonly Gold2TrendFactor _trend;
        private readonly Symbol _gold;
        private readonly decimal _floor;
        public override string Name => "Gold2TrendAlphaModel";

        public Gold2TrendAlphaModel(Gold2TrendFactor trend, Symbol gold, decimal floor)
        {
            _trend = trend; _gold = gold; _floor = floor;
        }

        public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            var v = _trend.Compute(_gold, algorithm.Time);
            int dir = (int)v.Value;
            decimal confirm = v.RawValue;

            InsightDirection dir2;
            decimal weight;
            if (dir > 0)      { dir2 = InsightDirection.Up;   weight = 1.0m * confirm; }
            else if (dir < 0) { dir2 = InsightDirection.Flat; weight = _floor; }
            else              { dir2 = InsightDirection.Flat; weight = _floor; }

            yield return new Insight(_gold, TimeSpan.FromDays(1), InsightType.Price,
                dir2, null, null, "Gold2Trend", (double)weight, "");
        }
    }
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2TrendAlphaModelTests" 2>&1 | tail -8`
Expected: 2/2 PASS。

- [ ] **Step 5: 提交**

```bash
cd /home/project/hope/Lean
git add Algorithm.CSharp/Models/Gold2/Gold2UniverseSelectionModel.cs Algorithm.CSharp/Models/Gold2/Gold2TrendAlphaModel.cs Tests/Algorithm/Gold2TrendAlphaModelTests.cs
git commit -m "feat(gold2): L1 Universe + L2 TrendAlphaModel with tests

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 8: L3 VolTargetPortfolioModel(dead-zone)

**Files:**
- Create: `Algorithm.CSharp/Models/Gold2/Gold2VolTargetPortfolioModel.cs`
- Test: `Tests/Algorithm/Gold2VolTargetPortfolioModelTests.cs`

- [ ] **Step 1: 写失败测试**

```csharp
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp.Models.Gold2;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class Gold2VolTargetPortfolioModelTests
    {
        // 设计 §5.3: target = w_smooth × dirCoef; |Δ|<threshold 维持 last
        [Test]
        public void DeadZone_BelowThreshold_HoldsLast()
            => Assert.AreEqual(0.80m, Gold2VolTargetPortfolioModel.ApplyDeadZone(0.80m, 0.82m, 0.05m));

        [Test]
        public void DeadZone_AboveThreshold_Updates()
            => Assert.AreEqual(0.90m, Gold2VolTargetPortfolioModel.ApplyDeadZone(0.80m, 0.90m, 0.05m));
    }
}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2VolTargetPortfolioModelTests" 2>&1 | tail -8`
Expected: FAIL — 类型/方法不存在。

- [ ] **Step 3: 写实现**

`Algorithm.CSharp/Models/Gold2/Gold2VolTargetPortfolioModel.cs`:
```csharp
using System;
using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Factors.Forward;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2
{
    /// <summary>
    /// gold2 L3 Portfolio: target = w_smooth × dirCoef(insight.Weight) + dead-zone。
    /// 详见 spec §5.3。
    /// </summary>
    public class Gold2VolTargetPortfolioModel : PortfolioConstructionModel
    {
        private readonly Gold2VolRegimeFactor _vol;
        private readonly Symbol _gold;
        private readonly decimal _threshold;
        private decimal _lastActualWeight;
        public override string Name => "Gold2VolTargetPortfolioModel";

        public Gold2VolTargetPortfolioModel(Gold2VolRegimeFactor vol, Symbol gold, decimal threshold)
        {
            _vol = vol; _gold = gold; _threshold = threshold;
        }

        public override IEnumerable<IPortfolioTarget> CreateTargets(QCAlgorithm algorithm, Insight[] insights)
        {
            if (insights.Length == 0) yield break;
            var ins = insights[0];
            decimal wSmooth = _vol.Compute(_gold, algorithm.Time).Value;
            decimal dirCoef = (decimal)(ins.Weight ?? 0);
            decimal target = wSmooth * dirCoef;
            target = ApplyDeadZone(_lastActualWeight, target, _threshold);
            _lastActualWeight = target;
            yield return PortfolioTarget.Percent(algorithm, _gold, target);
        }

        /// <summary>dead-zone 纯逻辑: |target-last| < threshold → 维持 last,否则更新。</summary>
        public static decimal ApplyDeadZone(decimal lastActual, decimal target, decimal threshold)
            => Math.Abs(target - lastActual) < threshold ? lastActual : target;
    }
}
```

注意:`Insight.Weight` 是 `double?`,用 `?? 0` 处理 null。`PortfolioConstructionModel` 基类 + `PortfolioTarget.Percent(IAlgorithm, Symbol, decimal)` 已核实。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2VolTargetPortfolioModelTests" 2>&1 | tail -8`
Expected: 2/2 PASS。

- [ ] **Step 5: 提交**

```bash
cd /home/project/hope/Lean
git add Algorithm.CSharp/Models/Gold2/Gold2VolTargetPortfolioModel.cs Tests/Algorithm/Gold2VolTargetPortfolioModelTests.cs
git commit -m "feat(gold2): L3 VolTargetPortfolioModel with dead-zone logic

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 9: L4 ExtremeRiskModel + RealRateCapModel(硬 cap 串联)

**Files:**
- Create: `Algorithm.CSharp/Models/Gold2/Gold2ExtremeRiskModel.cs`
- Create: `Algorithm.CSharp/Models/Gold2/Gold2RealRateCapModel.cs`
- Test: `Tests/Algorithm/Gold2RiskModelsTests.cs`

- [ ] **Step 1: 写失败测试**

```csharp
using System;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp.Models.Gold2;
using QuantConnect.Factors.Forward;

namespace QuantConnect.Tests.Algorithm
{
    [TestFixture]
    public class Gold2RiskModelsTests
    {
        private static readonly Symbol _gold = Symbol.Create("518880", SecurityType.Equity, Market.SSE);

        // 设计 §5.4 + §8.8
        [Test]
        public void ExtremeRisk_Triggered_CapsTo03()
            => Assert.AreEqual(0.30m, Gold2ExtremeRiskModel.ApplyCap(0.80m, triggered: true, 0.30m));

        [Test]
        public void ExtremeRisk_NotTriggered_PassesThrough()
            => Assert.AreEqual(0.80m, Gold2ExtremeRiskModel.ApplyCap(0.80m, triggered: false, 0.30m));

        [Test]
        public void RealRate_RisingFast_CapsTo06()
        {
            var inner = new GoldRealRateRegimeFactor();
            inner.InjectRegime(_gold, GoldRegime.RISING_FAST);
            Assert.AreEqual(0.60m, Gold2RealRateCapModel.ComputeCap(inner, _gold, DateTime.Now, 0.6m));
        }

        [Test]
        public void BothRisks_TakeMin()
        {
            decimal extremeCapped = Gold2ExtremeRiskModel.ApplyCap(0.80m, true, 0.30m);
            decimal realRateCap = 0.60m;
            Assert.AreEqual(0.30m, Math.Min(extremeCapped, realRateCap));
        }

        [Test]
        public void ExtremeRisk_OverridesDeadZone()
        {
            decimal deadZoneHeld = Gold2VolTargetPortfolioModel.ApplyDeadZone(0.80m, 0.81m, 0.05m); // 0.80
            decimal afterRisk = Gold2ExtremeRiskModel.ApplyCap(deadZoneHeld, true, 0.30m);
            Assert.AreEqual(0.30m, afterRisk, "极端触发绕过 dead-zone 直接 cap");
        }
    }
}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2RiskModelsTests" 2>&1 | tail -8`
Expected: FAIL — 类型/方法不存在。

- [ ] **Step 3: 写实现**

`Algorithm.CSharp/Models/Gold2/Gold2ExtremeRiskModel.cs`:
```csharp
using System;
using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Factors.Forward;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2
{
    /// <summary>
    /// gold2 L4 Risk: 极端开关。Triggered → cap 到 extremeCap(0.30),硬执行绕过 dead-zone。
    /// 详见 spec §5.4。
    /// </summary>
    public class Gold2ExtremeRiskModel : RiskManagementModel
    {
        private readonly Gold2ExtremeRiskFactor _ext;
        private readonly Symbol _gold;
        private readonly decimal _extremeCap;
        public override string Name => "Gold2ExtremeRiskModel";

        public Gold2ExtremeRiskModel(Gold2ExtremeRiskFactor ext, Symbol gold, decimal extremeCap)
        {
            _ext = ext; _gold = gold; _extremeCap = extremeCap;
        }

        public override IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            var triggered = (int)_ext.Compute(_gold, algorithm.Time).Value == 1;
            foreach (var t in targets)
            {
                decimal w = TargetToWeight(algorithm, t);
                w = ApplyCap(w, triggered, _extremeCap);
                yield return PortfolioTarget.Percent(algorithm, t.Symbol, w);
            }
        }

        /// <summary>纯逻辑: triggered → min(w, cap),否则 w。</summary>
        public static decimal ApplyCap(decimal weight, bool triggered, decimal cap)
            => triggered ? Math.Min(weight, cap) : weight;

        private static decimal TargetToWeight(QCAlgorithm algorithm, IPortfolioTarget t)
        {
            if (t.Quantity == 0) return 0m;
            var sec = algorithm.Securities[t.Symbol];
            return sec.Price > 0 ? t.Quantity * sec.Price / algorithm.Portfolio.TotalPortfolioValue : 0m;
        }
    }
}
```

`Algorithm.CSharp/Models/Gold2/Gold2RealRateCapModel.cs`:
```csharp
using System;
using System.Collections.Generic;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Factors.Forward;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Gold2
{
    /// <summary>
    /// gold2 L4 Risk: 实际利率帽子。RISING_FAST → cap 0.60,否则 1.0。与 ExtremeRisk 串联取 min。
    /// 详见 spec §5.4。
    /// </summary>
    public class Gold2RealRateCapModel : RiskManagementModel
    {
        private readonly Gold2RealRateCapFactor _cap;
        private readonly Symbol _gold;
        public override string Name => "Gold2RealRateCapModel";

        public Gold2RealRateCapModel(Gold2RealRateCapFactor cap, Symbol gold)
        {
            _cap = cap; _gold = gold;
        }

        public override IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            var capFactor = _cap.Compute(_gold, algorithm.Time).Value;
            foreach (var t in targets)
            {
                decimal w = TargetToWeight(algorithm, t);
                w = Math.Min(w, capFactor);
                yield return PortfolioTarget.Percent(algorithm, t.Symbol, w);
            }
        }

        /// <summary>纯逻辑(测试用): 直接从 inner regime factor 算 cap。</summary>
        public static decimal ComputeCap(GoldRealRateRegimeFactor inner, Symbol gold, DateTime time, decimal risingFastCap)
        {
            var regime = (GoldRegime)(decimal)inner.Compute(gold, time).Value;
            return regime == GoldRegime.RISING_FAST ? risingFastCap : 1.0m;
        }

        private static decimal TargetToWeight(QCAlgorithm algorithm, IPortfolioTarget t)
        {
            if (t.Quantity == 0) return 0m;
            var sec = algorithm.Securities[t.Symbol];
            return sec.Price > 0 ? t.Quantity * sec.Price / algorithm.Portfolio.TotalPortfolioValue : 0m;
        }
    }
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Gold2RiskModelsTests" 2>&1 | tail -8`
Expected: 5/5 PASS。

- [ ] **Step 5: 提交**

```bash
cd /home/project/hope/Lean
git add Algorithm.CSharp/Models/Gold2/Gold2ExtremeRiskModel.cs Algorithm.CSharp/Models/Gold2/Gold2RealRateCapModel.cs Tests/Algorithm/Gold2RiskModelsTests.cs
git commit -m "feat(gold2): L4 ExtremeRisk+RealRateCap risk models with tests

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 10: Gold2BetaVolTargetStrategy 五层拼装 + config + manifest

**Files:**
- Create: `Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs`
- Create: `Launcher/config/config-gold2-beta-vol-target-backtest.json`
- Create: `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml`

- [ ] **Step 1: 写 Strategy**

```csharp
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect.Algorithm.CSharp.Common;
using QuantConnect.Algorithm.CSharp.Models.Gold2;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Data;
using QuantConnect.Data.Custom.Gold;
using QuantConnect.Factors.Forward;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// gold2 Beta+波动率目标策略。五层 Framework: ManualUniverse(518880) + TrendAlpha + VolTargetPortfolio
    /// + ExtremeRisk/RealRateCap Risk + AShareLotSize Execution。beta 暴露+风险叠加,不追 alpha。
    /// 详见 docs/superpowers/specs/2026-07-09-gold2-beta-vol-target-design.md。
    /// </summary>
    public class Gold2BetaVolTargetStrategy : QCAlgorithm, IOptimizableStrategy, IRlStateExportable
    {
        private Symbol _gold, _auSym, _vixSym, _dfii10Sym;
        private Gold2TrendFactor _trend;
        private Gold2VolRegimeFactor _vol;
        private Gold2ExtremeRiskFactor _ext;
        private Gold2RealRateCapFactor _realrate;
        private GoldRealRateRegimeFactor _realrateInner;
        private int _volWarmup;
        private readonly Queue<decimal> _rvolReturns = new();

        public override void Initialize()
        {
            SetAccountCurrency(Currencies.CNY);
            SetCash(GetDecimalParameter("initial-capital", 1_000_000m));
            SetStartDate(GetDateParameter("start-date", new DateTime(2020, 1, 1)));
            SetEndDate(GetDateParameter("end-date", new DateTime(2026, 6, 23)));
            SetBenchmark("518880");

            var eq = AddEquity("518880", Resolution.Daily, Market.SSE);
            eq.FeeModel = new AShareStockFeeModel();
            eq.FillModel = new AShareStockFillModel();
            eq.BuyingPowerModel = new AShareStockBuyingPowerModel();
            eq.SettlementModel = new DelayedSettlementModel(0, TimeSpan.Zero);
            _gold = eq.Symbol;

            _auSym = AddData<AuShfDailyBar>("AU.SHF", Resolution.Daily).Symbol;
            _vixSym = AddData<FredMacroData>("VIX", Resolution.Daily).Symbol;
            _dfii10Sym = AddData<FredMacroData>("DFII10", Resolution.Daily).Symbol;

            _trend = new Gold2TrendFactor(GetIntParameter("trend-ma-short", 20), GetIntParameter("trend-ma-long", 120));
            _vol = new Gold2VolRegimeFactor(GetDecimalParameter("ewma-lambda", 0.94m),
                GetDecimalParameter("vol-target", 0.11m), GetIntParameter("vol-warmup", 60),
                GetDecimalParameter("smooth-alpha", 0.25m));
            _ext = new Gold2ExtremeRiskFactor();
            _realrateInner = new GoldRealRateRegimeFactor();
            _realrate = new Gold2RealRateCapFactor(_realrateInner, GetDecimalParameter("realrate-cap", 0.6m));
            _volWarmup = GetIntParameter("vol-warmup", 60);

            SetUniverseSelection(new Gold2UniverseSelectionModel());
            SetAlpha(new Gold2TrendAlphaModel(_trend, _gold, GetDecimalParameter("trend-floor", 0.2m)));
            SetPortfolioConstruction(new Gold2VolTargetPortfolioModel(_vol, _gold, GetDecimalParameter("rebalance-threshold", 0.05m)));
            AddRiskManagement(new Gold2ExtremeRiskModel(_ext, _gold, GetDecimalParameter("extreme-vol-cap", 0.3m)));
            AddRiskManagement(new Gold2RealRateCapModel(_realrate, _gold));
            SetExecution(new AShareLotSizeExecutionModel());
        }

        public override void OnData(Slice data)
        {
            if (data.Bars.TryGetValue(_gold, out var bar518880))
            {
                _trend.Update518880(bar518880.Close, bar518880.EndTime);
                _vol.Update(bar518880.Close, bar518880.EndTime);
                if (bar518880.Open > 0)
                    _rvolReturns.Enqueue((decimal)Math.Log((double)(bar518880.Close / bar518880.Open)));
                while (_rvolReturns.Count > _volWarmup) _rvolReturns.Dequeue();
                if (_rvolReturns.Count >= _volWarmup)
                {
                    var variance = Gold2VolRegimeFactor.Variance(_rvolReturns);
                    var rvol = (decimal)Math.Sqrt((double)variance) * (decimal)Math.Sqrt(252);
                    _ext.UpdateRvol60(rvol, bar518880.EndTime);
                }
            }
            if (data.TryGetValue(_auSym, out AuShfDailyBar auBar))
                _trend.UpdateAu(auBar.Close, auBar.EndTime);
            if (data.TryGetValue(_vixSym, out FredMacroData vix))
                _ext.UpdateVix(vix.MacroValue, vix.EndTime);
            if (data.TryGetValue(_dfii10Sym, out FredMacroData dfii10))
                _realrateInner.InjectRegime(_gold, ClassifyRealRateRegime(dfii10.MacroValue));
        }

        private static GoldRegime ClassifyRealRateRegime(decimal dfii10)
        {
            // 简化阈值分类(完整 5d/20d 斜率留作已知局限,spec §4.4 包装已有 inner)。
            if (dfii10 > 0.5m) return GoldRegime.RISING_FAST;
            if (dfii10 < -0.5m) return GoldRegime.FALLING_FAST;
            return GoldRegime.STABLE;
        }

        public IEnumerable<string> GetTunableParameterNames() => new[]
        {
            "trend-ma-short","trend-ma-long","ewma-lambda","vol-target","vol-warmup",
            "smooth-alpha","rebalance-threshold","extreme-vol-cap","realrate-cap","trend-floor"
        };

        public string SerializeRlState(QCAlgorithm algo)
        {
            var tpv = Portfolio.TotalPortfolioValue;
            var positions = Securities.Values
                .Where(s => s.Holdings.Quantity != 0)
                .Select(s => new { sym = s.Symbol.Value, w = s.Holdings.Quantity * s.Price / tpv }).ToList();
            return JsonConvert.SerializeObject(new
            {
                ts = algo.Time.ToString("o"), strategy = "Gold2BetaVolTargetStrategy",
                tpv, cash_pct = Portfolio.Cash / tpv, positions,
                w_smooth = _vol.Compute(_gold, algo.Time).Value,
                trend_dir = (int)_trend.Compute(_gold, algo.Time).Value,
                extreme_triggered = (int)_ext.Compute(_gold, algo.Time).Value == 1,
                realrate_cap = _realrate.Compute(_gold, algo.Time).Value
            });
        }

        private decimal GetDecimalParameter(string n, decimal d) =>
            decimal.TryParse(GetParameter(n), NumberStyles.Any, CultureInfo.InvariantCulture, out var v) ? v : d;
        private int GetIntParameter(string n, int d) =>
            int.TryParse(GetParameter(n), out var v) ? v : d;
        private DateTime GetDateParameter(string n, DateTime d) =>
            DateTime.TryParse(GetParameter(n), CultureInfo.InvariantCulture, DateTimeStyles.AssumeLocal, out var v) ? v : d;
    }
}
```

注意:`AddData<T>("AU.SHF", ...)` 的 Symbol.Value 含点号,但 `AuShfDailyBar.GetSource` 硬编码 `AU.SHF.csv` 路径,与 Symbol.Value 无关,安全。`data.TryGetValue<T>(symbol, out T)` 是 Slice 泛型取 custom data 的方法。`RiskManagementModel` 基类 `using QuantConnect.Algorithm.Framework.Risk`。

- [ ] **Step 2: 写 config**

`Launcher/config/config-gold2-beta-vol-target-backtest.json`:
```json
{
  "environment": "backtesting",
  "algorithm-type-name": "Gold2BetaVolTargetStrategy",
  "algorithm-language": "CSharp",
  "algorithm-location": "../../../Algorithm.CSharp/bin/Debug/QuantConnect.Algorithm.CSharp.dll",
  "data-folder": "../../../Data",
  "data-directory": "../../../Data",
  "history-provider": "FallbackTushareHistoryProvider",
  "data-provider": "DefaultDataProvider",
  "results-destination-folder": "../../../Results/gold2-betavol",
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
    "trend-ma-short": "20",
    "trend-ma-long": "120",
    "ewma-lambda": "0.94",
    "vol-target": "0.11",
    "vol-warmup": "60",
    "smooth-alpha": "0.25",
    "rebalance-threshold": "0.05",
    "extreme-vol-cap": "0.30",
    "realrate-cap": "0.60",
    "trend-floor": "0.20",
    "initial-capital": "1000000",
    "fee-rate": "0.0005"
  }
}
```

- [ ] **Step 3: 写 manifest**

`Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml`:
```yaml
strategy_name: Gold2BetaVolTargetStrategy
rl_state_completeness: partial
lean_config: Launcher/config/config-gold2-beta-vol-target-backtest.json
risk_model_target: Gold2ExtremeRiskModel+Gold2RealRateCapModel
universe_conflict: [GoldOvernightPremiumAlgorithm]
parameter_space:
  - {name: trend-ma-short, type: int, range: [10, 40], default: 20, layer: L2_Alpha}
  - {name: trend-ma-long, type: int, range: [60, 200], default: 120, layer: L2_Alpha}
  - {name: ewma-lambda, type: float, range: [0.85, 0.99], default: 0.94, layer: L3_Portfolio}
  - {name: vol-target, type: float, range: [0.08, 0.15], default: 0.11, layer: L3_Portfolio}
  - {name: vol-warmup, type: int, range: [40, 120], default:  60, layer: L3_Portfolio}
  - {name: smooth-alpha, type: float, range: [0.1, 0.5], default: 0.25, layer: L3_Portfolio}
  - {name: rebalance-threshold, type: float, range: [0.02, 0.10], default: 0.05, layer: L3_Portfolio}
  - {name: extreme-vol-cap, type: float, range: [0.20, 0.50], default: 0.30, layer: L4_Risk}
  - {name: realrate-cap, type: float, range: [0.40, 0.80], default: 0.60, layer: L4_Risk}
  - {name: trend-floor, type: float, range: [0.0, 0.30], default: 0.20, layer: L2_Alpha}
state_schema:
  fields:
    - {name: ts, type: string}
    - {name: strategy, type: string}
    - {name: tpv, type: float}
    - {name: cash_pct, type: float}
    - {name: positions, type: array, item_schema: [sym, w]}
    - {name: w_smooth, type: float}
    - {name: trend_dir, type: int}
    - {name: extreme_triggered, type: bool}
    - {name: realrate_cap, type: float}
  dim_hint: 9
reward_config:
  primary: dsr
  shaping:
    - {term: scaled_pnl, weight: 1.0}
    - {term: drawdown_excess_penalty, weight: 2.0}
universe:
  symbols: ["518880"]
  timezone: Asia/Shanghai
```

- [ ] **Step 4: 构建确认编译通过**

Run: `cd /home/project/hope/Lean && dotnet build QuantConnect.Lean.sln 2>&1 | tail -5`
Expected: Build succeeded(0 errors)。若有符号/命名空间错误,修正 using。

- [ ] **Step 5: 提交**

```bash
cd /home/project/hope/Lean
git add Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs Launcher/config/config-gold2-beta-vol-target-backtest.json Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml
git commit -m "feat(gold2): five-layer Strategy + config + manifest

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 11: gold2_data_gates.py 数据完整性 Gate

**Files:**
- Create: `Scripts/gold2_data_gates.py`

- [ ] **Step 1: 写 gate 脚本**

```python
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
    assert len(cov) > 1600, f"518880 覆盖不足: {len(cov)} < 1600"
    au = pd.read_parquet(f"{TUSHARE}/fut_daily/ts_code=AU.SHF/data.parquet")
    au["date"] = pd.to_datetime(au["trade_date"], format="%Y%m%d")
    cov_au = au[(au["date"] >= "2020-01-01") & (au["date"] <= "2026-06-23")]
    assert len(cov_au) > 1600, f"AU.SHF 覆盖不足: {len(cov_au)} < 1600"
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
```

- [ ] **Step 2: 运行 gate 确认通过**

Run: `cd /home/project/hope/Lean && python3 Scripts/gold2_data_gates.py`
Expected: 打印三个 `PASS` + `ALL GATES PASS`,exit 0。若 FRED CSV 缺失,先跑 Task 1 的 fetch 脚本。

- [ ] **Step 3: 提交**

```bash
cd /home/project/hope/Lean
git add Scripts/gold2_data_gates.py
git commit -m "feat(gold2): data coverage/alignment gates

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 12: 端到端回测 + 三曲线对比验证

**Files:**
- Run: 回测(产 `Results/gold2-betavol/`)
- Create: `Launcher/config/config-gold2-volonly.json`(vol_only 变体)
- Create: `Scripts/gold2_compare_curves.py`(三模式对比)

- [ ] **Step 1: 跑数据 Gate**

Run: `cd /home/project/hope/Lean && python3 Scripts/gold2_data_gates.py`
Expected: `ALL GATES PASS`。

- [ ] **Step 2: 跑完整回测(full 模式)**

Run: `cd /home/project/hope/Lean && dotnet build QuantConnect.Lean.sln 2>&1 | tail -3 && cd Launcher/bin/Debug && dotnet QuantConnect.Lean.Launcher.dll --config ../../../Launcher/config/config-gold2-beta-vol-target-backtest.json 2>&1 | tail -30`
Expected: 回测完成,打印 Final equity,`Results/gold2-betavol/` 产出 charts/equity。若崩,读 stderr 定位(常见:custom data Reader 列映射、AddData Symbol 点号、SMA Update 签名)。

- [ ] **Step 3: 跑 vol_only 变体(趋势层禁用)**

复制 config 为 `config-gold2-volonly.json`,改 `trend-floor=1.0`(趋势空头也满仓 → 纯波动目标,无方向择时)与 `results-destination-folder=../../../Results/gold2-volonly`。

Run: `cd /home/project/hope/Lean/Launcher/bin/Debug && dotnet QuantConnect.Lean.Launcher.dll --config ../../../Launcher/config/config-gold2-volonly.json 2>&1 | tail -15`
Expected: 回测完成,`Results/gold2-volonly/` 产出。

- [ ] **Step 4: 写对比脚本**

`Scripts/gold2_compare_curves.py`:
```python
#!/usr/bin/env python3
"""对比 baseline/vol_only/full 三曲线(spec §7.2)。主指标:最大回撤改善。
baseline = 518880 累计净值(从 full 回测的 Benchmark 输出取,无单独回测)。"""
import os, pandas as pd, numpy as np

RESULTS = "/home/project/hope/Lean/Results"

def load_equity(folder):
    for root, _, files in os.walk(os.path.join(RESULTS, folder)):
        for f in files:
            if f == "equity.csv":
                df = pd.read_csv(os.path.join(root, f))
                return df.iloc[:, 1] if df.shape[1] > 1 else df.iloc[:, 0]
    return None

def stats(eq):
    rets = eq.pct_change().dropna()
    peak = eq.cummax(); dd = (eq - peak) / peak
    max_dd = dd.min()
    in_dd = (dd < 0).values
    dur = cur = 0
    for v in in_dd:
        cur = cur + 1 if v else 0; dur = max(dur, cur)
    ann_ret = (eq.iloc[-1] / eq.iloc[0]) ** (252 / len(eq)) - 1
    sharpe = rets.mean() / rets.std() * np.sqrt(252) if rets.std() > 0 else 0
    return dict(ann_ret=ann_ret, max_dd=max_dd, dd_duration=dur, sharpe=sharpe)

rows = []
for label, folder in [("vol_only", "gold2-volonly"), ("full", "gold2-betavol")]:
    eq = load_equity(folder)
    if eq is None: print(f"{label}: 无 equity 数据"); continue
    s = stats(eq); s["mode"] = label; rows.append(s)
out = pd.DataFrame(rows)[["mode", "ann_ret", "max_dd", "dd_duration", "sharpe"]]
os.makedirs(os.path.join(RESULTS, "gold2-betavol"), exist_ok=True)
out.to_csv(os.path.join(RESULTS, "gold2-betavol", "comparison.csv"), index=False)
print(out.to_string(index=False))
print("\n注: baseline 需从回测 benchmark 输出单独取 518880 累计净值对比")
```

- [ ] **Step 5: 运行对比 + 判定**

Run: `cd /home/project/hope/Lean && python3 Scripts/gold2_compare_curves.py`
Expected: 打印对比表。判定线(spec §7.2):`full` 最大回撤 < `baseline`×0.85(改善≥15%)→ EFFECTIVE,否则 EFFECTIVENESS_FAIL。记录结果到 spec §7.2 的判定。

- [ ] **Step 6: 提交**

```bash
cd /home/project/hope/Lean
git add Scripts/gold2_compare_curves.py Launcher/config/config-gold2-volonly.json
git commit -m "feat(gold2): end-to-end backtest + three-curve comparison

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 自审

**1. Spec coverage:**
- §1 三个立足 → Task 1-2(tushare+FRED 真实数据)、Task 3-6(LEAN IFactor 原生)、Task 10(A 股标的 518880/SSE/100股) ✓
- §1.2 三层管线 → Factor Zoo(Task 3-6)→ Model Zoo(Task 7-9)→ Strategy(Task 10) ✓
- §1.3 五层架构 → L1 Task 7, L2 Task 7, L3 Task 8, L4 Task 9, L5 Task 10(复用) ✓
- §4 因子数学 → Task 3-6 一一对应 ✓
- §5 Model 实现 → Task 7-9 + Task 10 拼装 ✓
- §6 数据注入 → Task 1(FRED)、Task 2(AU)、Task 10(OnData 喂因子)、Task 11(Gate) ✓
- §7 测试 → Task 3-9 单元测试、Task 12 回测对比、Task 3/4 因果性测试 ✓
- §8 已知局限 → 文档已记;CFTC 跳过在 Task 3 用 AU 弱代理;GPR 在 Task 5 RVol 代理 ✓

**2. Placeholder scan:** 无 TBD/TODO。所有 step 含完整代码或确切命令。Task 3/5 的"先 Read 确认 API 签名"是实现指导非 placeholder。

**3. Type consistency:**
- `Gold2TrendFactor.Update518880/UpdateAu/Compute` — Task 3 定义,Task 7/10 调用一致 ✓
- `Gold2VolRegimeFactor.Update/Compute/Variance` — Task 4 定义,Task 8/10/12 调用一致 ✓
- `Gold2ExtremeRiskFactor.UpdateVix/UpdateRvol60/Compute` — Task 5 定义,Task 9/10 调用一致 ✓
- `Gold2RealRateCapFactor` — Task 6 定义,Task 9/10 调用一致 ✓
- `Gold2VolTargetPortfolioModel.ApplyDeadZone` — Task 8 定义,Task 9 测试调用一致 ✓
- `Gold2ExtremeRiskModel.ApplyCap` — Task 9 定义,Task 9 测试调用一致 ✓
- `Gold2RealRateCapModel.ComputeCap` — Task 9 定义,Task 9 测试调用一致 ✓
- `FactorResult` 用对象初始化器(无构造函数) — 所有因子一致 ✓

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-09-gold2-beta-vol-target-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
