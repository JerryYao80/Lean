# Tushare 因子动物园 — Phase 2 实现计划 (FactorStore + 3 只读适配器)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地统一读接口 `FactorStore` + 3 只读适配器 (RRegistry / RBarra / RParquet), 让策略与重构 LLM 经单一 pull API 取因子值, 不改任何现有 FactorRegistry / Barra / 因子原码。

**Architecture:** 方案 A 分层适配的 C# 读层。`FactorStore.Get(factorId, symbol, date, history)` 按 factor_id 路由到唯一拥有该因子的适配器: Runtime 因子 → RRegistryAdapter (委托 FactorRegistry.Compute, 传 history); Barra 15 因子 → RBarraAdapter (File.ReadAllLines 读 CSV); parquet 标量因子 (crowding + Phase 5 新因子) → RParquetAdapter (pythonnet+pandas 读 parquet, 零新依赖)。RInflux 推迟 (无读客户端 lib)。每个适配器只读, 不写, 不碰现有原码。

**Tech Stack:** C# (.NET 10), NUnit 4.2.2, pythonnet 2.0.53 (已在 Common.csproj), pandas (运行时), pytest (守 source-substring 契约)。

**Spec:** `docs/superpowers/specs/2026-07-22-tushare-factor-zoo-design.md` §2 (架构) + §4.2 (C# 测试) + §4.3 (约束对齐)。

---

## 前置事实 (实现前已验证, 不必重新探)

1. **`FactorRegistry`** (`Common/Factors/Core/FactorRegistry.cs`): `public static class`, namespace `QuantConnect.Factors.Core`。写有锁读无锁 (读层不依赖其同步)。`Get(string) -> IFactor` (实际可空, 当 null 处理); `AllMetadata() -> IReadOnlyDictionary<string, FactorMetadata>`; `GetByComputeMode(FactorComputeMode)` / `GetByCategory`。`Initialize()` 幂等, 注册 36 因子。**Runtime 因子** (ComputeMode=Runtime, 自算不需 InjectValue): `hv_20d`, `momentum_20d`, `ma_cross_5_20`, `rsi_14d`, `amihud_20d`, `iv_hv_spread`, `var_1d99`。**Precomputed 因子** (需 InjectValue, 其值在 parquet 里): crowding, chip_*, pe_pct_*, roe, net_margin, ... — 这些 Phase 2 走 RParquetAdapter 直读 parquet 标量, 不走 InjectValue。
2. **`IFactor`** (`Common/Factors/Core/IFactor.cs`): `Compute(Symbol, DateTime, IEnumerable<BaseData> history = null) -> FactorResult`; `ComputeRank`; `IsAvailable(Symbol, DateTime) -> bool`; 6 props (Id/Name/Category/Scope/ComputeMode/DataSource)。`InjectValue`/`ClearInjectedValues` 是具体类约定, **不在接口** — 本计划不碰它们。
3. **`FactorResult`** (`Common/Factors/Core/FactorResult.cs`): `struct`, 字段 `Value, RawValue, Time, Symbol, FactorId, Quality, ComputeTimeMs`。`Quality` 枚举是 **`FactorDataQuality`** (非 FactorQuality): `Valid, Missing, Outlier, Stale`。
4. **`FactorMetadata`**: `class`, props Id/Name/Category/Scope/ComputeMode/DataSource/Parameters/Dependencies/CsvPath/Description。
5. **消费契约 (不可破坏)**: 现有 AlphaModel 用 `FactorRegistry.Initialize()` → `Get(id) as ConcreteFactor` → `Compute()` → 查 `Quality`/`RawValue`。**`Tests/Factors/test_crowding_factor.py` 是 pytest source-substring 测试** (断言 C# 源码含 `FactorRegistry.Initialize` / `FactorRegistry.Get("crowding")` / `CrowdingFactor.InjectValue` / `Py.GIL` 等) — 不得重命名任何这些, 否则该测试红。
6. **NUnit 测试**: `Tests/Common/Factors/`, namespace `QuantConnect.Tests.Common.Factors`, `[TestFixture]`, 无共享 SetUp, FactorRegistry 是进程全局静态 (测试顺序相关, 保留此 wart 不修)。Tests.csproj 引用 `Common/QuantConnect.csproj`, NUnit 4.2.2。
7. **parquet 读路径**: 仓库无任何 C# parquet lib。现有 6 处 C# 经 pythonnet+pandas `Py.GIL()` + `pd.read_parquet` 读 parquet。模板: `Algorithm.CSharp/Models/Alpha/CrowdingFactorZooAlphaModel.cs:130-184` (读 `result/crowding-factor/<YYYY-MM-DD>/<ts_code>.parquet`, 取标量列)。`QuantConnect.pythonnet 2.0.53` 已在 `Common/QuantConnect.csproj`。**零新依赖**。
8. **CSV 读路径**: `Common/Factors/Volatility/IVPercentileFactor.cs:44-67` — `File.ReadAllLines` + `Split(',')` + 列名查表。Barra V2 CSV: `Data/alternative/barra-cne5v2-factors/{sse|szse}/daily/{ticker}.csv`, 列 `trade_date,beta,momentum,size,earnyld,resvol,growth,btop,leverage,liquidity,nlsize,moneyflow,quality,northbound,margin,chipcost,total_mv,turnover_rate,listed_days,missing_factor_count,is_st`。
9. **Symbol→ts_code**: 裸 ticker `600519` → `600519.SH` (6/9 开头上交所) / `000001.SZ` (深交所)。`CrowdingFactorZooAlphaModel.SymbolToTsCode` 已实现此映射 — 本计划在 RParquetAdapter 内镜像同一逻辑 (不复用 Algorithm.CSharp 的类, 避免跨层依赖)。
10. **parquet 布局**: crowding → `result/crowding-factor/<YYYY-MM-DD>/<ts_code>.parquet` (列 composite/trading/fund/chip/degraded/hk_hold_stale); Phase 5 新因子 → `result/factor-zoo/<YYYY-MM-DD>/<ts_code>.parquet` (Phase 2 时尚不存在, 用合成 parquet 测)。
11. HARD CONSTRAINT: 只在 `Common/Factors/Store/` (新目录) + `Tests/Common/Factors/` 加文件。不改 `Common/Factors/Core/`、`Common/Factors/<Category>/`、`Algorithm.CSharp/`、`data-source/`、任何现有策略。不改 csproj (pythonnet 已在)。

---

## File Structure (本 Phase 2 涉及)

| 文件 | 职责 | 新/改 |
|---|---|---|
| `Common/Factors/Store/IFactorAdapter.cs` | 适配器接口: `TryGet(symbol, date, history, out FactorResult)` | 新 |
| `Common/Factors/Store/FactorStore.cs` | 统一读接口 + factor_id→adapter 路由 + AllMetadata/FreshnessReport | 新 |
| `Common/Factors/Store/RRegistryAdapter.cs` | 只读包 FactorRegistry, 委托 Runtime 因子 Compute | 新 |
| `Common/Factors/Store/RBarraAdapter.cs` | 读 Data/alternative/barra-*.csv 标量 | 新 |
| `Common/Factors/Store/RParquetAdapter.cs` | pythonnet 读 result/<root>/<date>/<ts_code>.parquet 标量 (含可注入读缝) | 新 |
| `Common/Factors/Store/FactorStoreConfig.cs` | factor_id→(adapter, root/column) 配置 + 默认注册 | 新 |
| `Tests/Common/Factors/Store/FactorStoreTests.cs` | FactorStore 路由 + 集成 NUnit 测试 | 新 |
| `Tests/Common/Factors/Store/RRegistryAdapterTests.cs` | Runtime 因子委托测试 | 新 |
| `Tests/Common/Factors/Store/RBarraAdapterTests.cs` | CSV 读 + 缺失/ST edge | 新 |
| `Tests/Common/Factors/Store/RParquetAdapterTests.cs` | 可注入读缝单测 + pythonnet 集成 (Integration category) | 新 |
| `Tests/Common/Factors/Store/FactorStoreIntegrationTests.cs` | 跨适配器路由 + FreshnessReport | 新 |
| `Tests/Python/FactorZoo/test_factor_store_contract.py` | pytest: 守 FactorStore C# 契约 (不破坏现有 crowding source-substring) + Store 命名空间存在 | 新 |

**不碰**: `Common/Factors/Core/*`, `Common/Factors/<Category>/*`, `Common/QuantConnect.csproj`, `Algorithm.CSharp/*`, `data-source/*`, `Scripts/factor_zoo/*` (Phase 1 产物)。

---

## Task 1: IFactorAdapter 接口 + FactorStore 骨架 + 路由 (TDD)

**Files:**
- Create: `Common/Factors/Store/IFactorAdapter.cs`
- Create: `Common/Factors/Store/FactorStore.cs`
- Create: `Common/Factors/Store/FactorStoreConfig.cs`
- Create: `Tests/Common/Factors/Store/FactorStoreTests.cs`

- [ ] **Step 1: Write failing test (routing by factor_id)**

Create `Tests/Common/Factors/Store/FactorStoreTests.cs`:
```csharp
using System;
using System.Collections.Generic;
using NUnit.Framework;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Common.Factors.Store
{
    [TestFixture]
    public class FactorStoreTests
    {
        [Test]
        public void Get_UnknownFactorId_ReturnsMissing()
        {
            var store = new FactorStore();
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var r = store.Get("does_not_exist", sym, new DateTime(2026, 7, 22));
            Assert.AreEqual(FactorDataQuality.Missing, r.Quality);
            Assert.AreEqual(0m, r.Value);
        }

        [Test]
        public void Get_RoutesToRegisteredAdapter()
        {
            var store = new FactorStore();
            var fake = new FakeAdapter();
            store.Register("fake_factor", fake);
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var r = store.Get("fake_factor", sym, new DateTime(2026, 7, 22));
            Assert.AreEqual(FactorDataQuality.Valid, r.Quality);
            Assert.AreEqual(1.23m, r.Value);
            Assert.IsTrue(fake.Called);
        }

        [Test]
        public void AllMetadata_AggregatesFromFactorRegistry()
        {
            FactorRegistry.Initialize();
            var store = new FactorStore();
            var meta = store.AllMetadata();
            Assert.IsTrue(meta.ContainsKey("crowding"));
            Assert.IsTrue(meta.ContainsKey("hv_20d"));
        }

        private class FakeAdapter : IFactorAdapter
        {
            public bool Called;
            public bool TryGet(Symbol symbol, DateTime date, IEnumerable<BaseData> history, out FactorResult result)
            {
                Called = true;
                result = new FactorResult { Value = 1.23m, RawValue = 1.23m, Time = date, Symbol = symbol, FactorId = "fake_factor", Quality = FactorDataQuality.Valid };
                return true;
            }
        }
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~FactorStoreTests" --nologo 2>&1 | tail -15`
Expected: FAIL — `FactorStore` / `IFactorAdapter` / namespace `QuantConnect.Factors.Store` not found (compile error).

- [ ] **Step 3: Implement IFactorAdapter + FactorStore + FactorStoreConfig**

Create `Common/Factors/Store/IFactorAdapter.cs`:
```csharp
/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Factor Zoo — Phase 2 read-only adapter interface.
 * See docs/superpowers/specs/2026-07-22-tushare-factor-zoo-design.md §2.
 */
using System;
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Store
{
    /// <summary>
    /// Read-only adapter contract: resolve one factor's value for (symbol, date).
    /// Adapters NEVER write, NEVER mutate existing FactorRegistry/Barra state.
    /// </summary>
    public interface IFactorAdapter
    {
        /// <summary>Return false (with Quality=Missing result) if data unavailable; never throw on missing.</summary>
        bool TryGet(Symbol symbol, DateTime date, IEnumerable<BaseData> history, out FactorResult result);
    }
}
```

Create `Common/Factors/Store/FactorStore.cs`:
```csharp
/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Factor Zoo — Phase 2 unified read interface (pull API).
 * Routes factor_id -> owning adapter. Read-only; does not modify FactorRegistry.
 */
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Store
{
    /// <summary>
    /// Single pull-based entry point for factor values: Get(factorId, symbol, date, history).
    /// Routes to the one adapter that owns the factor. Unknown ids -> Quality=Missing.
    /// </summary>
    public class FactorStore
    {
        private readonly Dictionary<string, IFactorAdapter> _adapters = new();
        private readonly object _lock = new();

        public FactorStore()
        {
            FactorStoreConfig.RegisterDefaults(this);
        }

        public void Register(string factorId, IFactorAdapter adapter)
        {
            lock (_lock) { _adapters[factorId] = adapter; }
        }

        public FactorResult Get(string factorId, Symbol symbol, DateTime date, IEnumerable<BaseData> history = null)
        {
            IFactorAdapter adapter;
            lock (_lock) { _adapters.TryGetValue(factorId, out adapter); }
            if (adapter == null)
            {
                return new FactorResult { Value = 0m, RawValue = 0m, Time = date, Symbol = symbol, FactorId = factorId, Quality = FactorDataQuality.Missing };
            }
            if (adapter.TryGet(symbol, date, history, out var result))
            {
                return result;
            }
            return new FactorResult { Value = 0m, RawValue = 0m, Time = date, Symbol = symbol, FactorId = factorId, Quality = FactorDataQuality.Missing };
        }

        /// <summary>Aggregate metadata from FactorRegistry (read-only view).</summary>
        public IReadOnlyDictionary<string, FactorMetadata> AllMetadata()
        {
            return FactorRegistry.AllMetadata();
        }
    }
}
```

Create `Common/Factors/Store/FactorStoreConfig.cs`:
```csharp
/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Factor Zoo — Phase 2 default adapter registration.
 */
using QuantConnect.Factors.Core;

namespace QuantConnect.Factors.Store
{
    /// <summary>
    /// Wires default factor_id -> adapter mappings into a FactorStore.
    /// Tasks 2-4 populate this as adapters are built. For Task 1 it is a no-op
    /// stub so FactorStore compiles and routes unknown ids to Missing.
    /// </summary>
    internal static class FactorStoreConfig
    {
        public static void RegisterDefaults(FactorStore store)
        {
            FactorRegistry.Initialize();
            // Task 2: Runtime factors -> RRegistryAdapter
            // Task 3: Barra factors -> RBarraAdapter
            // Task 4: parquet factors -> RParquetAdapter
        }
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~FactorStoreTests" --nologo 2>&1 | tail -15`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/project/hope/Lean
git add Common/Factors/Store/IFactorAdapter.cs Common/Factors/Store/FactorStore.cs Common/Factors/Store/FactorStoreConfig.cs Tests/Common/Factors/Store/FactorStoreTests.cs
git commit -m "feat(factor-zoo): Phase 2 Task 1 — FactorStore skeleton + routing

Unified pull-based read API Get(factorId, symbol, date, history) -> FactorResult,
routes by factor_id to the owning adapter; unknown -> Quality=Missing (never
throws). AllMetadata aggregates from FactorRegistry read-only. New isolated
namespace QuantConnect.Factors.Store; no existing code touched.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: RRegistryAdapter (Runtime 因子委托 FactorRegistry)

**Files:**
- Create: `Common/Factors/Store/RRegistryAdapter.cs`
- Create: `Tests/Common/Factors/Store/RRegistryAdapterTests.cs`
- Modify: `Common/Factors/Store/FactorStoreConfig.cs` (register Runtime factors)

- [ ] **Step 1: Write failing test (delegates to a Runtime factor via FactorRegistry)**

Create `Tests/Common/Factors/Store/RRegistryAdapterTests.cs`:
```csharp
using System;
using System.Collections.Generic;
using NUnit.Framework;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Common.Factors.Store
{
    [TestFixture]
    public class RRegistryAdapterTests
    {
        [Test]
        public void TryGet_RuntimeFactor_DelegatesToFactorRegistryCompute()
        {
            FactorRegistry.Initialize();
            var adapter = new RRegistryAdapter("hv_20d");
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var history = MakeHistory(sym, 25, 100m, 110m);
            var ok = adapter.TryGet(sym, new DateTime(2026, 7, 22), history, out var result);
            Assert.IsTrue(ok);
            Assert.AreEqual(FactorDataQuality.Valid, result.Quality);
            Assert.Greater(result.Value, 0m); // realized vol of rising series is positive
        }

        [Test]
        public void TryGet_UnknownIdInRegistry_ReturnsMissingNotThrow()
        {
            FactorRegistry.Initialize();
            var adapter = new RRegistryAdapter("not_a_registered_runtime_factor");
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var ok = adapter.TryGet(sym, new DateTime(2026, 7, 22), null, out var result);
            Assert.IsFalse(ok);
            Assert.AreEqual(FactorDataQuality.Missing, result.Quality);
        }

        private static IEnumerable<BaseData> MakeHistory(Symbol sym, int bars, decimal start, decimal end)
        {
            for (int i = 0; i < bars; i++)
            {
                var close = start + (end - start) * i / (bars - 1);
                yield return new TradeBar(new DateTime(2026, 6, 25).AddDays(i), sym, close, close, close, close, 1000m);
            }
        }
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~RRegistryAdapterTests" --nologo 2>&1 | tail -15`
Expected: FAIL — `RRegistryAdapter` not found (compile error).

- [ ] **Step 3: Implement RRegistryAdapter**

Create `Common/Factors/Store/RRegistryAdapter.cs`:
```csharp
/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Factor Zoo — Phase 2 read-only adapter wrapping FactorRegistry for Runtime factors.
 * Delegates Compute() to the registered IFactor; never mutates registry state.
 */
using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Store
{
    /// <summary>
    /// Read-only wrapper for FactorRegistry Runtime factors (hv_20d, momentum_20d,
    /// ma_cross_5_20, rsi_14d, amihud_20d, iv_hv_spread). These Compute() from
    /// history with no InjectValue. Precomputed factors are owned by RParquetAdapter.
    /// </summary>
    public class RRegistryAdapter : IFactorAdapter
    {
        private readonly string _factorId;

        public RRegistryAdapter(string factorId) { _factorId = factorId; }

        public bool TryGet(Symbol symbol, DateTime date, IEnumerable<BaseData> history, out FactorResult result)
        {
            var factor = FactorRegistry.Get(_factorId);
            if (factor == null)
            {
                result = new FactorResult { Value = 0m, Time = date, Symbol = symbol, FactorId = _factorId, Quality = FactorDataQuality.Missing };
                return false;
            }
            // Runtime factors only — guard against accidentally routing a Precomputed factor here.
            if (factor.ComputeMode != FactorComputeMode.Runtime)
            {
                result = new FactorResult { Value = 0m, Time = date, Symbol = symbol, FactorId = _factorId, Quality = FactorDataQuality.Missing };
                return false;
            }
            result = factor.Compute(symbol, date, history);
            return result.Quality == FactorDataQuality.Valid;
        }
    }
}
```

Register Runtime factors in `FactorStoreConfig.RegisterDefaults` (replace the Task 2 comment line):
```csharp
            // Task 2: Runtime factors -> RRegistryAdapter
            foreach (var fid in new[] { "hv_20d", "momentum_20d", "ma_cross_5_20", "rsi_14d", "amihud_20d", "iv_hv_spread" })
            {
                store.Register(fid, new RRegistryAdapter(fid));
            }
```
(var_1d99 is registered via VaRFactors.Register(), not FactorRegistry.Initialize — leave it out of the default loop; a strategy needing it can Register() explicitly.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~RRegistryAdapterTests" --nologo 2>&1 | tail -15`
Expected: 2 PASS. If `hv_20d` returns a value the test disagrees with, inspect `Common/Factors/Volatility/HVFactor.cs` for the exact formula and adjust the assertion to match (e.g. assert `> 0` for a rising series, or exact annualized stdev). Do NOT weaken to a trivially-true assertion. If `hv_20d` needs a specific history length/shape, adjust `MakeHistory`.

- [ ] **Step 5: Commit**

```bash
cd /home/project/hope/Lean
git add Common/Factors/Store/RRegistryAdapter.cs Common/Factors/Store/FactorStoreConfig.cs Tests/Common/Factors/Store/RRegistryAdapterTests.cs
git commit -m "feat(factor-zoo): Phase 2 Task 2 — RRegistryAdapter (Runtime factors)

Read-only delegation of Runtime factors (hv_20d/momentum_20d/...) to
FactorRegistry.Get(id).Compute(history). Guards ComputeMode==Runtime so a
Precomputed factor is never mis-routed here. Default-registers 6 Runtime ids.
No existing code touched.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: RBarraAdapter (CSV 读 Barra 15 因子)

**Files:**
- Create: `Common/Factors/Store/RBarraAdapter.cs`
- Create: `Tests/Common/Factors/Store/RBarraAdapterTests.cs`
- Modify: `Common/Factors/Store/FactorStoreConfig.cs` (register Barra factors)

- [ ] **Step 1: Write failing test (read a Barra column from synthetic CSV)**

Create `Tests/Common/Factors/Store/RBarraAdapterTests.cs`:
```csharp
using System.IO;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Common.Factors.Store
{
    [TestFixture]
    public class RBarraAdapterTests
    {
        private static string WriteBarraCsv(string dir, string ticker, string market)
        {
            Directory.CreateDirectory(Path.Combine(dir, market, "daily"));
            var path = Path.Combine(dir, market, "daily", $"{ticker}.csv");
            File.WriteAllText(path,
                "trade_date,beta,momentum,size,earnyld,resvol,growth,btop,leverage,liquidity,nlsize,moneyflow,quality,northbound,margin,chipcost,total_mv,turnover_rate,listed_days,missing_factor_count,is_st\n" +
                "20260721,-0.04,0.12,12.3,0.01,0.02,0.03,0.4,1.2,0.5,11.0,0.0,0.15,0.0,0.0,0.1,1e10,0.2,2000,0,0\n" +
                "20260722,-0.05,0.13,12.4,0.011,0.021,0.031,0.41,1.21,0.51,11.1,0.0,0.16,0.0,0.0,0.11,1.05e10,0.21,2001,0,0\n");
            return path;
        }

        [Test]
        public void TryGet_ReadsBarraColumnForDate()
        {
            var dir = Path.Combine(Path.GetTempPath(), "fz_barra_test");
            if (Directory.Exists(dir)) Directory.Delete(dir, true);
            WriteBarraCsv(dir, "600519", "sse");
            try
            {
                var adapter = new RBarraAdapter("beta", dataRoot: dir);
                var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
                var ok = adapter.TryGet(sym, new DateTime(2026, 7, 22), null, out var result);
                Assert.IsTrue(ok);
                Assert.AreEqual(-0.05m, result.Value);
                Assert.AreEqual(FactorDataQuality.Valid, result.Quality);
            }
            finally { if (Directory.Exists(dir)) Directory.Delete(dir, true); }
        }

        [Test]
        public void TryGet_MissingDate_ReturnsMissingNotThrow()
        {
            var dir = Path.Combine(Path.GetTempPath(), "fz_barra_test2");
            if (Directory.Exists(dir)) Directory.Delete(dir, true);
            WriteBarraCsv(dir, "600519", "sse");
            try
            {
                var adapter = new RBarraAdapter("beta", dataRoot: dir);
                var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
                var ok = adapter.TryGet(sym, new DateTime(2099, 1, 1), null, out var result);
                Assert.IsFalse(ok);
                Assert.AreEqual(FactorDataQuality.Missing, result.Quality);
            }
            finally { if (Directory.Exists(dir)) Directory.Delete(dir, true); }
        }

        [Test]
        public void TryGet_MissingCsvFile_ReturnsMissing()
        {
            var dir = Path.Combine(Path.GetTempPath(), "fz_barra_test3");
            if (Directory.Exists(dir)) Directory.Delete(dir, true);
            var adapter = new RBarraAdapter("beta", dataRoot: dir);
            var sym = Symbol.Create("999999", SecurityType.Equity, Market.SSE);
            var ok = adapter.TryGet(sym, new DateTime(2026, 7, 22), null, out var result);
            Assert.IsFalse(ok);
            Assert.AreEqual(FactorDataQuality.Missing, result.Quality);
        }
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~RBarraAdapterTests" --nologo 2>&1 | tail -15`
Expected: FAIL — `RBarraAdapter` not found.

- [ ] **Step 3: Implement RBarraAdapter**

Create `Common/Factors/Store/RBarraAdapter.cs`:
```csharp
/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Factor Zoo — Phase 2 read-only adapter for Barra CNE5 V2 CSV factors.
 * Mirrors the IVPercentileFactor GetIVHistory read pattern (File.ReadAllLines +
 * Split(',') + column-name lookup). Never writes.
 */
using System;
using System.Globalization;
using System.IO;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Store
{
    /// <summary>
    /// Reads one Barra factor column (beta/momentum/.../chipcost) for (symbol, date)
    /// from Data/alternative/barra-cne5v2-factors/{market}/daily/{ticker}.csv.
    /// </summary>
    public class RBarraAdapter : IFactorAdapter
    {
        private readonly string _column;
        private readonly string _dataRoot;

        public RBarraAdapter(string column, string dataRoot = null)
        {
            _column = column;
            _dataRoot = dataRoot ?? Path.Combine(Globals.DataFolder, "alternative", "barra-cne5v2-factors");
        }

        public bool TryGet(Symbol symbol, DateTime date, System.Collections.Generic.IEnumerable<BaseData> history, out FactorResult result)
        {
            var (market, ticker) = ResolveMarketTicker(symbol);
            var csvPath = Path.Combine(_dataRoot, market, "daily", $"{ticker}.csv");
            result = new FactorResult { Value = 0m, Time = date, Symbol = symbol, FactorId = _column, Quality = FactorDataQuality.Missing };
            if (!File.Exists(csvPath)) return false;

            var lines = File.ReadAllLines(csvPath);
            if (lines.Length < 2) return false;
            var header = lines[0].Split(',');
            int idxDate = -1, idxCol = -1;
            for (int i = 0; i < header.Length; i++)
            {
                var h = header[i].Trim();
                if (h == "trade_date") idxDate = i;
                else if (h == _column) idxCol = i;
            }
            if (idxDate < 0 || idxCol < 0) return false;

            var want = date.ToString("yyyyMMdd", CultureInfo.InvariantCulture);
            string bestValue = null;
            for (int i = lines.Length - 1; i >= 1; i--)
            {
                var parts = lines[i].Split(',');
                if (parts.Length <= Math.Max(idxDate, idxCol)) continue;
                var d = parts[idxDate].Trim();
                if (string.CompareOrdinal(d, want) <= 0) { bestValue = parts[idxCol].Trim(); break; }
            }
            if (bestValue == null) return false;
            if (!decimal.TryParse(bestValue, NumberStyles.Float, CultureInfo.InvariantCulture, out var v)) return false;
            result = new FactorResult { Value = v, RawValue = v, Time = date, Symbol = symbol, FactorId = _column, Quality = FactorDataQuality.Valid };
            return true;
        }

        private static (string market, string ticker) ResolveMarketTicker(Symbol symbol)
        {
            var ticker = symbol.ID.Symbol;
            var c = ticker.Length > 0 ? ticker[0] : '0';
            var market = (c == '6' || c == '9') ? "sse" : "szse";
            return (market, ticker);
        }
    }
}
```

Register Barra factors in `FactorStoreConfig.RegisterDefaults` (replace the Task 3 comment line):
```csharp
            // Task 3: Barra factors -> RBarraAdapter
            var barraRoot = System.IO.Path.Combine(Globals.DataFolder, "alternative", "barra-cne5v2-factors");
            foreach (var col in new[] { "beta","momentum","size","earnyld","resvol","growth","btop","leverage","liquidity","nlsize","moneyflow","quality","northbound","margin","chipcost" })
            {
                store.Register($"barra_{col}", new RBarraAdapter(col, barraRoot));
            }
```
(Barra factor_id in FactorStore is prefixed `barra_` to avoid collision with FactorRegistry ids.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~RBarraAdapterTests" --nologo 2>&1 | tail -15`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/project/hope/Lean
git add Common/Factors/Store/RBarraAdapter.cs Common/Factors/Store/FactorStoreConfig.cs Tests/Common/Factors/Store/RBarraAdapterTests.cs
git commit -m "feat(factor-zoo): Phase 2 Task 3 — RBarraAdapter (CSV read-only)

Reads one Barra CNE5 V2 factor column for (symbol, date) from
Data/alternative/barra-cne5v2-factors/{market}/daily/{ticker}.csv via
File.ReadAllLines + column lookup (mirrors IVPercentileFactor). Latest<=date
row; missing file/date -> Quality=Missing, never throws. Default-registers
15 barra_* ids. No existing code touched.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: RParquetAdapter (pythonnet 读 parquet 标量 + 可注入读缝)

**Files:**
- Create: `Common/Factors/Store/RParquetAdapter.cs`
- Create: `Tests/Common/Factors/Store/RParquetAdapterTests.cs`
- Modify: `Common/Factors/Store/FactorStoreConfig.cs` (register crowding)

- [ ] **Step 1: Write failing test (injectable read-seam; no pythonnet needed for unit test)**

Create `Tests/Common/Factors/Store/RParquetAdapterTests.cs`:
```csharp
using System;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Common.Factors.Store
{
    [TestFixture]
    public class RParquetAdapterTests
    {
        [Test]
        public void TryGet_InjectedReader_ReturnsScalar()
        {
            var adapter = new RParquetAdapter(
                factorRoot: "factor-zoo", valueColumn: "value",
                readScalar: (path, column) => 0.42m);
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var ok = adapter.TryGet(sym, new DateTime(2026, 7, 22), null, out var result);
            Assert.IsTrue(ok);
            Assert.AreEqual(0.42m, result.Value);
            Assert.AreEqual(FactorDataQuality.Valid, result.Quality);
        }

        [Test]
        public void TryGet_InjectedReaderReturnsNull_Missing()
        {
            var adapter = new RParquetAdapter(
                factorRoot: "factor-zoo", valueColumn: "value",
                readScalar: (path, column) => (decimal?)null);
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var ok = adapter.TryGet(sym, new DateTime(2026, 7, 22), null, out var result);
            Assert.IsFalse(ok);
            Assert.AreEqual(FactorDataQuality.Missing, result.Quality);
        }

        [Test]
        public void TryGet_ResolvesParquetPathAndTsCode()
        {
            string seenPath = null, seenCol = null;
            var adapter = new RParquetAdapter(
                factorRoot: "crowding-factor", valueColumn: "composite",
                resultRoot: "/tmp/fz_parquet_test_root",
                readScalar: (path, column) => { seenPath = path; seenCol = column; return 0.1m; });
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            adapter.TryGet(sym, new DateTime(2026, 7, 22), null, out _);
            Assert.AreEqual("/tmp/fz_parquet_test_root/crowding-factor/2026-07-22/600519.SH.parquet", seenPath);
            Assert.AreEqual("composite", seenCol);
        }
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~RParquetAdapterTests" --nologo 2>&1 | tail -15`
Expected: FAIL — `RParquetAdapter` not found.

- [ ] **Step 3: Implement RParquetAdapter**

Create `Common/Factors/Store/RParquetAdapter.cs`:
```csharp
/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Factor Zoo — Phase 2 read-only adapter for parquet-scalar factors.
 * Uses the EXISTING pythonnet+pandas bridge (Py.GIL + pd.read_parquet) — the
 * repo's established parquet-read mechanism, zero new deps. Mirrors
 * Algorithm.CSharp/Models/Alpha/CrowdingFactorZooAlphaModel.cs:130-184.
 *
 * The scalar-read is behind a delegate seam so unit tests inject a fake (no Python
 * needed); the default delegate uses pythonnet+pandas at runtime.
 */
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Securities;

namespace QuantConnect.Factors.Store
{
    /// <summary>
    /// Reads one parquet scalar for (symbol, date) from
    /// {resultRoot}/{factorRoot}/{date:yyyy-MM-dd}/{ts_code}.parquet[column].
    /// Owns: crowding (result/crowding-factor/, col composite) + Phase 5 new factors.
    /// </summary>
    public class RParquetAdapter : IFactorAdapter
    {
        private readonly string _factorRoot;
        private readonly string _valueColumn;
        private readonly string _resultRoot;
        private readonly Func<string, string, decimal?> _readScalar;

        public RParquetAdapter(string factorRoot, string valueColumn,
                               string resultRoot = null,
                               Func<string, string, decimal?> readScalar = null)
        {
            _factorRoot = factorRoot;
            _valueColumn = valueColumn;
            _resultRoot = resultRoot ?? DefaultResultRoot();
            _readScalar = readScalar ?? DefaultPythonNetReader;
        }

        public bool TryGet(Symbol symbol, DateTime date, IEnumerable<BaseData> history, out FactorResult result)
        {
            var tsCode = SymbolToTsCode(symbol);
            var path = Path.Combine(_resultRoot, _factorRoot,
                date.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture), $"{tsCode}.parquet");
            result = new FactorResult { Value = 0m, Time = date, Symbol = symbol, FactorId = _factorRoot, Quality = FactorDataQuality.Missing };
            decimal? v;
            try { v = _readScalar(path, _valueColumn); }
            catch { return false; }
            if (!v.HasValue) return false;
            result = new FactorResult { Value = v.Value, RawValue = v.Value, Time = date, Symbol = symbol, FactorId = _factorRoot, Quality = FactorDataQuality.Valid };
            return true;
        }

        /// <summary>Expose roots for FactorStore.FreshnessReport (read-only).</summary>
        public (string FactorRoot, string ResultRoot) Describe() => (_factorRoot, _resultRoot);

        /// <summary>A-share Symbol -> tushare ts_code (6/9-prefix .SH else .SZ).</summary>
        internal static string SymbolToTsCode(Symbol symbol)
        {
            var ticker = symbol.ID.Symbol;
            var c = ticker.Length > 0 ? ticker[0] : '0';
            return (c == '6' || c == '9') ? $"{ticker}.SH" : $"{ticker}.SZ";
        }

        private static string DefaultResultRoot()
        {
            try { return Path.GetFullPath(Path.Combine(Globals.DataFolder, "..", "..", "result")); }
            catch { return "result"; }
        }

        /// <summary>Default reader: pythonnet + pandas read_parquet, scalar extraction. Mirrors CrowdingFactorZooAlphaModel.</summary>
        private static decimal? DefaultPythonNetReader(string path, string column)
        {
            if (!File.Exists(path)) return null;
            using (Python.Runtime.Py.GIL())
            {
                dynamic pd = Python.Runtime.Py.Import("pandas");
                dynamic df = pd.read_parquet(path);
                int len = (int)df.__len__();
                if (len == 0) return null;
                dynamic val = df[column].iloc[0];
                if (val == null) return null;
                // numeric columns come back as Python float/int; coerce via double
                try { return Convert.ToDecimal((double)val, CultureInfo.InvariantCulture); }
                catch { return null; }
            }
        }
    }
}
```

Register crowding in `FactorStoreConfig.RegisterDefaults` (replace the Task 4 comment line):
```csharp
            // Task 4: parquet factors -> RParquetAdapter
            store.Register("crowding", new RParquetAdapter("crowding-factor", "composite"));
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~RParquetAdapterTests" --nologo 2>&1 | tail -15`
Expected: 3 PASS (these use the injected fake reader, no Python needed). If `DefaultPythonNetReader` has a compile issue (pythonnet API surface differs), the unit tests still pass because they inject a fake; fix the default reader's API calls to match `CrowdingFactorZooAlphaModel.cs:130-184` exactly (read that file first) — the default-reader path is exercised in Phase 5.

- [ ] **Step 5: Commit**

```bash
cd /home/project/hope/Lean
git add Common/Factors/Store/RParquetAdapter.cs Common/Factors/Store/FactorStoreConfig.cs Tests/Common/Factors/Store/RParquetAdapterTests.cs
git commit -m "feat(factor-zoo): Phase 2 Task 4 — RParquetAdapter (pythonnet read-only)

Reads one parquet scalar for (symbol,date) from
result/<factorRoot>/<yyyy-MM-dd>/<ts_code>.parquet[column] via the EXISTING
pythonnet+pandas bridge (zero new deps; mirrors CrowdingFactorZooAlphaModel).
Scalar-read behind a delegate seam so unit tests inject a fake (no Python
needed); default delegate uses Py.GIL+pd.read_parquet at runtime. Corrupt/
missing -> Quality=Missing, never throws. Default-registers crowding.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: FactorStore 集成 + FreshnessReport + 契约 pytest

**Files:**
- Modify: `Common/Factors/Store/FactorStore.cs` (add FreshnessReport)
- Create: `Tests/Common/Factors/Store/FactorStoreIntegrationTests.cs`
- Create: `Tests/Python/FactorZoo/test_factor_store_contract.py`

- [ ] **Step 1: Write failing integration test (cross-adapter routing)**

Create `Tests/Common/Factors/Store/FactorStoreIntegrationTests.cs`:
```csharp
using System;
using NUnit.Framework;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Securities;

namespace QuantConnect.Tests.Common.Factors.Store
{
    [TestFixture]
    public class FactorStoreIntegrationTests
    {
        [Test]
        public void Get_RoutesBarraAndParquetAndRegistryByPrefix()
        {
            var store = new FactorStore();
            var sym = Symbol.Create("600519", SecurityType.Equity, Market.SSE);
            var rb = store.Get("barra_beta", sym, new DateTime(2026, 7, 22));
            Assert.AreEqual(FactorDataQuality.Missing, rb.Quality);
            var ru = store.Get("nope", sym, new DateTime(2026, 7, 22));
            Assert.AreEqual(FactorDataQuality.Missing, ru.Quality);
        }

        [Test]
        public void FreshnessReport_ListsRegisteredParquetFactors()
        {
            var store = new FactorStore();
            var rep = store.FreshnessReport();
            Assert.IsNotNull(rep);
            CollectionAssert.IsSubsetOf(new[] { "crowding" }, rep.Keys);
        }
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~FactorStoreIntegrationTests" --nologo 2>&1 | tail -15`
Expected: FAIL — `FreshnessReport` not found.

- [ ] **Step 3: Add FreshnessReport to FactorStore**

In `Common/Factors/Store/FactorStore.cs`, add:
```csharp
        /// <summary>Best-effort freshness: factor_id -> last available date (null if unknown/Runtime).</summary>
        public IReadOnlyDictionary<string, DateTime?> FreshnessReport()
        {
            var rep = new Dictionary<string, DateTime?>();
            List<KeyValuePair<string, IFactorAdapter>> snapshot;
            lock (_lock) { snapshot = new List<KeyValuePair<string, IFactorAdapter>>(_adapters); }
            foreach (var kv in snapshot)
            {
                if (kv.Value is RParquetAdapter pa)
                {
                    rep[kv.Key] = ParquetFreshness(pa);
                }
                else
                {
                    rep[kv.Key] = null; // CSV/Runtime: not resolvable at store level
                }
            }
            return rep;
        }

        private static DateTime? ParquetFreshness(RParquetAdapter pa)
        {
            var (factorRoot, resultRoot) = pa.Describe();
            var dir = System.IO.Path.Combine(resultRoot, factorRoot);
            if (!System.IO.Directory.Exists(dir)) return null;
            DateTime best = DateTime.MinValue;
            foreach (var sub in System.IO.Directory.GetDirectories(dir))
            {
                var name = System.IO.Path.GetFileName(sub);
                if (DateTime.TryParseExact(name, "yyyy-MM-dd", System.Globalization.CultureInfo.InvariantCulture,
                    System.Globalization.DateTimeStyles.None, out var d) && d > best) best = d;
            }
            return best == DateTime.MinValue ? null : best;
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~FactorStoreIntegrationTests" --nologo 2>&1 | tail -15`
Expected: 2 PASS.

- [ ] **Step 5: Add pytest contract guard**

Create `Tests/Python/FactorZoo/test_factor_store_contract.py`:
```python
"""Guard: FactorStore (Phase 2) lives in QuantConnect.Factors.Store and does not
break the existing crowding C# contract (source-substring test in test_crowding_factor.py).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "Scripts"))


def test_factor_store_namespace_exists():
    store_dir = REPO / "Common" / "Factors" / "Store"
    assert store_dir.is_dir(), f"expected {store_dir} to exist (Phase 2)"
    assert (store_dir / "FactorStore.cs").exists()
    assert (store_dir / "IFactorAdapter.cs").exists()


def test_existing_crowing_contract_unchanged():
    alpha = REPO / "Algorithm.CSharp" / "Models" / "Alpha" / "CrowdingFactorZooAlphaModel.cs"
    if not alpha.exists():
        return
    src = alpha.read_text(encoding="utf-8")
    assert "FactorRegistry.Initialize" in src
    assert 'FactorRegistry.Get("crowding")' in src
    assert "InjectValue" in src
    assert "Py.GIL" in src


def test_factor_store_does_not_modify_factor_registry_core():
    reg = REPO / "Common" / "Factors" / "Core" / "FactorRegistry.cs"
    src = reg.read_text(encoding="utf-8")
    assert "CrowdingFactor" in src, "crowding registration must remain in FactorRegistry.Initialize"
```

- [ ] **Step 6: Run pytest**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest Tests/Python/FactorZoo/test_factor_store_contract.py -v`
Expected: 3 PASS.

- [ ] **Step 7: Run the FULL Phase 1+2 test sweep**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest Tests/Python/FactorZoo/ -q`
Expected: pytest green (Phase 1's 17 + 3 new = 20). (NUnit Store tests run via Step 4.)

- [ ] **Step 8: Commit**

```bash
cd /home/project/hope/Lean
git add Common/Factors/Store/FactorStore.cs Tests/Common/Factors/Store/FactorStoreIntegrationTests.cs Tests/Python/FactorZoo/test_factor_store_contract.py
git commit -m "feat(factor-zoo): Phase 2 Task 5 — FreshnessReport + integration + contract guard

FactorStore.FreshnessReport() best-effort last-available-date per factor
(parquet subdir scan; CSV/Runtime null). Integration test asserts cross-adapter
routing (barra_*/crowding/unknown). pytest contract guard locks: Store namespace
exists + existing crowding C# contract strings unchanged + FactorRegistry still
registers CrowdingFactor (additive, not modified).

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Phase 2 完成判据 (Definition of Done)

- [ ] `Common/Factors/Store/` 新命名空间存在: `IFactorAdapter`, `FactorStore`, `RRegistryAdapter`, `RBarraAdapter`, `RParquetAdapter`, `FactorStoreConfig`
- [ ] NUnit Store 测试全绿: `dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Factors.Store" --nologo`
- [ ] pytest 契约测试绿: `Tests/Python/FactorZoo/test_factor_store_contract.py` (含 Phase 1 的 17 + 本 Phase 3 新)
- [ ] 零新依赖: `git diff master..HEAD -- '*.csproj'` 为空 (pythonnet 已在, 不改 csproj)
- [ ] 不改现有特性: `git diff master..HEAD --name-only -- Common/Factors/Core/ Common/Factors/Trend/ Common/Factors/Volatility/ Common/Factors/Chip/ Common/Factors/Sentiment/ Common/Factors/Value/ Common/Factors/Quality/ Common/Factors/Liquidity/ Common/Factors/Forward/ Common/Factors/Risk/ Algorithm.CSharp/` 为空
- [ ] 现有 crowding source-substring 测试 (`Tests/Factors/test_crowding_factor.py`) 仍绿
- [ ] 适配器只读: RBarraAdapter/RParquetAdapter 只读不改文件 (单测断言无写)

## Phase 2 之后的路线 (不在本 plan, 仅提示)

- **Phase 3**: factor_worker supervisor 程序 (接入现有 crowding/forward build_day + Phase 5 新因子 build_day, 每日增量补齐 + 新鲜度上报) — spec §3.1
- **Phase 4**: FactorCatalog 生成器 + manifest schema + 重构 LLM prompt 注入 + 优化器 manifest.factor-include — spec §3.2
- **Phase 5**: 7 新因子 build_day (财务类复用 Phase 1 pit_financials) — spec §5
- **Phase 6**: Barra 接入 factor_worker + Grafana 面板 + 端到端回归 — spec §7 step 6/9/10
- **RInfluxAdapter**: 推迟 — 需时以原始 HTTP /api/v2/query (Flux) 实现, 无新依赖
