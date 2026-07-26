# Alpha101 C# 读路径 + 内涵说明 — 设计

> **日期**: 2026-07-26
> **分支**: `feat/alpha101-csharp-read` (off `feat/alpha101-factor-zoo`)
> **前置**: Alpha101 因子生产链路已完成（101 alpha 公式 + factor_worker supervisor LIVE + alpha101 fresh@20260724 + 217 测试通过）。本 spec 只接通消费侧的 C# 直读路径 + 为每个因子补内涵说明。

## 1. 目标

让 backtest 算法经 `FactorStore.Get("alphaNNN", symbol, date)` 单点读取任一 alpha 值，底层复用 pythonnet+pandas 直读 `result/factor-zoo/alphaNNN/<date>/<ts_code>.parquet`。同时为 101 个 alpha 各补一份内涵说明（intent / scenarios / direction / family），便于 LLM 或人工调用时获知每个因子的能力。

**核心决策（brainstorming 已确认）**:
- 注册 **101 个独立 id** `alpha001..alpha101`（各 1 个 `RParquetAdapter`），**不注册向量 group id**。LLM/人工单点调用只读 1 个 parquet，1 次 GIL + 1 次 IO。批量场景罕见，YAGNI。
- `RParquetAdapter` **不改**——它本就能读这种 shape（单行 `{ts_code, alphaNNN:float}` parquet）。
- 内涵说明存**独立 yaml 文件**，`build_catalog.py` 合并进 catalog，`hypothesize.py` 的 LLM 提示自动带上。

## 2. 架构

```
result/factor-zoo/alphaNNN/<date>/<ts_code>.parquet   (生产侧已就绪)
        │
        ▼  RParquetAdapter.TryGet (复用, 不改)
FactorStore._adapters["alpha042"] ──► store.Get("alpha042", sym, date)
                                        │  1 次 GIL + 1 次 parquet 读
                                        ▼
                                    FactorResult{Value, Quality}

Scripts/factor_zoo/alpha101_descriptions.yaml  (101 条内涵)
        │  _load_alpha101_descriptions()
        ▼
build_catalog.py::_alpha_entry  ──►  factor-catalog.yaml (每条 alpha 多 4 字段)
        │
        ▼
hypothesize.py::_load_available_factors  ──► LLM 提示 (LLM 获知每个 alpha 能力)
```

## 3. 组件 A — 101 个独立 id 注册（C# 读侧）

### 3.1 新文件 `Common/Factors/Store/Alpha101FactorRegistration.cs`

```csharp
/*
 * Factor Zoo — Alpha101 独立 id 注册。
 * 把 101 个 alphaNNN 各注册一个 RParquetAdapter，使 FactorStore.Get("alphaNNN")
 * 能单点读取。RParquetAdapter 本就能读 result/factor-zoo/alphaNNN/<date>/<code>.parquet
 * 的单行 shape，无需新适配器。
 */
namespace QuantConnect.Factors.Store
{
    internal static class Alpha101FactorRegistration
    {
        public static void Register(FactorStore store)
        {
            for (int n = 1; n <= 101; n++)
            {
                var aid = $"alpha{n:03d}";
                // factorRoot 含 "factor-zoo/" 前缀，与 builder 输出路径一致：
                //   {resultRoot}/factor-zoo/alphaNNN/<date>/<code>.parquet
                // valueColumn = "alphaNNN" (parquet 单行 schema {ts_code, alphaNNN:float})
                // resultRoot 用 RParquetAdapter 默认值 (Lean/result，与 crowding 一致)。
                store.Register(aid, new RParquetAdapter($"factor-zoo/{aid}", aid));
            }
        }
    }
}
```

**路径解析验证**: `RParquetAdapter.TryGet` 构造 `path = Path.Combine(resultRoot, factorRoot, date.ToString("yyyy-MM-dd"), $"{tsCode}.parquet")` = `Lean/result/factor-zoo/alpha042/2026-07-24/600519.SH.parquet`，与 builder.py:88 的 `result_root/factor-zoo/<aid>/<date>/<ts_code>.parquet` 完全匹配。

### 3.2 `FactorStoreConfig.RegisterDefaults` 改动（既有文件，加 1 行）

末尾加：
```csharp
// Alpha101: 101 个独立 id (单点读, LLM/人工点名哪个读哪个)
Alpha101FactorRegistration.Register(store);
```

### 3.3 不改动的既有组件

- `RParquetAdapter` — 复用 `TryGet` / `SymbolToTsCode` / `DefaultResultRoot` / `DefaultPythonNetReader`。
- `FactorStore` — 复用 `Get` / `Register` / `AllMetadata` / `FreshnessReport`。**不加新方法**。
- `IFactorAdapter` / `FactorResult` / `FactorDataQuality` 枚举 — 零改动。

### 3.4 数据缺失语义

直接复用 `RParquetAdapter` 现有契约：
- 文件不存在 / 空 / NaN → `TryGet` 返回 `false`，`FactorStore.Get` 返回 `Quality=Missing, Value=0m`，**不抛异常**。
- 与 crowding 因子的缺失行为完全一致。
- **不引入 Partial 概念，不改枚举**。

### 3.5 消费侧示例

```csharp
var r = store.Get("alpha042", sym, algorithm.Time);
if (r.Quality == FactorDataQuality.Missing) { /* 该 alpha 该天无 parquet, hold */ continue; }
double a42 = (double)r.Value;   // LLM/人工点名哪个读哪个, 只读这 1 个 parquet
```

## 4. 组件 B — 101 条内涵说明

### 4.1 新文件 `Scripts/factor_zoo/alpha101_descriptions.yaml`

101 条，每条 4 字段：

```yaml
alpha001:
  intent: "Ts_ArgMax 反转动量。基于过去 20 日内收益最大日距今天数构造的反转信号。"
  scenarios:
    - "短期反转选股：近期涨幅最大的股票后续倾向回调"
    - "动量衰竭识别：argmax 距今越近, 反转概率越高"
  direction: "反向"
  family: "极值反转"

alpha042:
  intent: "VWAP-收盘均值回归。衡量当日 VWAP 相对收盘价的偏离及收敛倾向。"
  scenarios:
    - "短期反转选股：VWAP 显著高于收盘后, 次日倾向回落"
    - "日内择时：结合量能判断 VWAP 回归的可靠性"
  direction: "反向"
  family: "VWAP反转"
# ... alpha002 .. alpha101 同结构
```

**字段定义**:
- `intent` (string, 必填): 因子内涵——它衡量什么、基于什么信号构造。1-2 句。
- `scenarios` (list[string], 必填, ≥1 条): 适用场景——什么情况下会调用这个因子、它解决什么问题。
- `direction` (enum, 必填): `正向` (高值→多头) / `反向` (高值→空头) / `中性` (视结构而定)。
- `family` (enum, 必填): 13 个语义族之一 (见 §4.2)。

**direction 标注原则**: 对照 `docs/101.md` 原公式 + Kakushadze 2015 论文已知语义。能从公式结构 + 论文确定的标正向/反向；无法确定的标"中性"（诚实, 不臆测）。

### 4.2 13 个语义族

把 101 个 alpha 按主导信号归类，便于 LLM 按族检索：

| 族名 | 主导信号 | 代表 alpha |
|---|---|---|
| 量价反转 | 量价相关性/协方差反转 | #2,#3,#6,#13 |
| VWAP反转 | VWAP-收盘偏离回归 | #5,#28,#42 |
| 量价相关 | 量价时序相关极值 | #26,#27,#44 |
| 波动率 | 高低价/收益波动 | #12,#15,#16 |
| 动量反转 | 收益动量反转 | #4,#8,#31 |
| 开收结构 | 开盘/收盘结构 | #18,#33,#37 |
| 极值反转 | ts_argmax/argmin 极值 | #1,#9,#29 |
| 趋势条件 | 趋势符号+条件 | #19,#24,#30 |
| 行业中性 | indneutralize 去均 | #48,#58,#63 |
| 量能异动 | 量比/换手异动 | #39,#43 |
| 价量协方差 | 价量协方差复合 | #14,#47 |
| 高低量相关 | 高低价×量相关 | #22,#40 |
| 复合时序 | 多周期复合 | #17,#36,#71 |

(实施时每条 alpha 归入其一；上表为示例归属，最终归属在实施时按公式逐条确定。)

### 4.3 `build_catalog.py` 改动（既有文件，最小改）

加模块级加载函数 + 升级 `_alpha_entry`：

```python
_ALPHA101_DESCRIPTIONS = None  # lazy cache

def _load_alpha101_descriptions() -> dict:
    """Load Scripts/factor_zoo/alpha101_descriptions.yaml (101 entries).
    Returns {} if missing/corrupt (graceful degrade; catalog then omits the
    4 description fields for alphas, keeping only selection_hint).
    """
    global _ALPHA101_DESCRIPTIONS
    if _ALPHA101_DESCRIPTIONS is not None:
        return _ALPHA101_DESCRIPTIONS
    p = Path(__file__).parent / "alpha101_descriptions.yaml"
    if not p.exists():
        _ALPHA101_DESCRIPTIONS = {}
        return {}
    try:
        _ALPHA101_DESCRIPTIONS = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        _ALPHA101_DESCRIPTIONS = {}
    return _ALPHA101_DESCRIPTIONS


def _alpha_entry(n: int, hint: str) -> dict:
    aid = f"alpha{n:03d}"
    entry = {
        "id": aid,
        "name": f"WorldQuant Alpha#{n}",
        "category": "Alpha101",
        "compute_mode": "Precomputed",
        "storage": _parquet(f"result/factor-zoo/{aid}", aid, f"lean_factor_{aid}"),
        "tushare_deps": ["daily", "adj_factor", "daily_basic", "index_member_all"],
        "selection_hint": hint,
        "parameters": {"n": n},
    }
    desc = _load_alpha101_descriptions().get(aid, {})
    if desc:
        entry["intent"] = desc.get("intent", "")
        entry["scenarios"] = desc.get("scenarios", [])
        entry["direction"] = desc.get("direction", "")
        entry["family"] = desc.get("family", "")
    return entry
```

**`hypothesize.py` 不改**——它已遍历 catalog 的每个 factor 字段，新加的 4 个字段会自动出现在 LLM 提示里。如果当前 `build_prompt` 只投影了 `{id,category,selection_hint}`，实施时确认是否需要把 intent/direction/family 也拼进提示——这是实施细节，spec 层面只要求"catalog 带这 4 字段且 LLM 提示能用到"。

## 5. 测试计划

### 5.1 C# 侧 — `Tests/Common/Factors/Store/Alpha101FactorRegistrationTests.cs` (新)

- `Register_RegistersAll101Ids`: 注册后 `store` 含 `alpha001..alpha101` 全部 101 个 adapter（用注入 fake reader 验证路由, 不依赖 Python）。抽查 `alpha001/alpha042/alpha101`。
- `Register_Alpha042_DescribeMatchesBuilderPath`: `((RParquetAdapter)adapter).Describe()` == `("factor-zoo/alpha042", <Lean/result>)`。
- `Register_Alpha042_PathResolution`: 注入 fake reader 捕获 path，断言 = `.../Lean/result/factor-zoo/alpha042/2026-07-24/600519.SH.parquet`。
- `Register_TsCode_SH_SZ_ETF`: 600519→`.SH` / 000001→`.SZ` / 518880→`.SH`（复用 RParquetAdapter 已有逻辑, 这里只验 alpha101 注册后能走到）。
- `Register_MissingParquet_ReturnsMissingNotThrow`: fake reader 返回 null → `Quality=Missing`，不抛。
- `[Explicit] Get_RealDisk_Alpha042_20260724`: 读 `result/factor-zoo/alpha042/2026-07-24/603799.SH.parquet` → `alpha042 ≈ 1.06993`。
- `[Explicit] Get_RealDisk_Alpha001_20260724`: 读 `result/factor-zoo/alpha001/2026-07-24/600000.SH.parquet` → `alpha001 ≈ 0.396667`。

### 5.2 `Tests/Common/Factors/Store/FactorStoreTests.cs` (既有, 加 1 个)

- `Get_Alpha101IndependentId_RoutesToParquetAdapter`: `store.Get("alpha042", ...)` 路由到真实 RParquetAdapter（注入 fake reader 验证端到端）。

### 5.3 `Tests/Common/Factors/Store/FactorStoreIntegrationTests.cs` (既有, 加 1 个)

- `Get_Alpha101Id_ReturnsMissingWhenNoParquet`: 未注入 reader + 不存在的日期 → `Quality=Missing`（验 alpha101 id 在集成层的缺失契约）。

### 5.4 Python 侧 — `Tests/Python/FactorZoo/test_alpha101_descriptions.py` (新)

- `test_descriptions_cover_all_101`: 1..101 无缺。
- `test_each_entry_has_4_fields_nonempty`: 每条 intent/scenarios/direction/family 非空。
- `test_direction_in_valid_set`: direction ∈ {正向, 反向, 中性}。
- `test_family_in_13_families`: family ∈ 13 族集合。
- `test_scenarios_is_list`: scenarios 是 list 且 ≥1 条。
- `test_build_catalog_merges_descriptions`: `build_catalog()` 后 alpha042 的 entry 含 intent/scenarios/direction/family。
- `test_build_catalog_count_unchanged`: catalog 仍 159 条（描述是字段加法, 不增量）。

### 5.5 `Tests/Python/FactorZoo/test_build_catalog.py` (既有, 更新)

- 现有计数断言保持 159（不增）。
- 加 `test_alpha101_entries_have_description_fields`: 101 条 alpha 都有 intent/direction/family。

## 6. 文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `Common/Factors/Store/Alpha101FactorRegistration.cs` | 新建 | 101 id 注册循环 |
| `Common/Factors/Store/FactorStoreConfig.cs` | 改 (加 1 行) | 调用 `Alpha101FactorRegistration.Register(store)` |
| `Scripts/factor_zoo/alpha101_descriptions.yaml` | 新建 | 101 条内涵说明 |
| `Scripts/factor_zoo/build_catalog.py` | 改 (加载+合并) | `_load_alpha101_descriptions` + `_alpha_entry` 升级 |
| `Tests/Common/Factors/Store/Alpha101FactorRegistrationTests.cs` | 新建 | C# 单元+集成 |
| `Tests/Common/Factors/Store/FactorStoreTests.cs` | 改 (加 1) | `Get_Alpha101IndependentId_*` |
| `Tests/Common/Factors/Store/FactorStoreIntegrationTests.cs` | 改 (加 1) | `Get_Alpha101Id_*Missing*` |
| `Tests/Python/FactorZoo/test_alpha101_descriptions.py` | 新建 | Python 描述验证 |
| `Tests/Python/FactorZoo/test_build_catalog.py` | 改 (加 1) | alpha101 描述字段断言 |

## 7. 非目标

- 不写消费策略算法（单独 spec）。
- 不改 `RParquetAdapter` / `FactorStore` 既有方法 / `FactorResult` / `FactorDataQuality` 枚举。
- 不注册向量 group id（批量场景罕见, YAGNI）。
- 不接 InfluxDB 读路径（parquet 直读已够; InfluxDB 是监控用）。
- 不重算 101 个 alpha 的 direction（用论文已知语义 + 公式结构推断, 不确定标中性）。
- 不改 `hypothesize.py` 的提示模板逻辑（除非实施时发现 build_prompt 没投影新字段, 届时按最小改处理）。

## 8. 验收标准

1. `dotnet build QuantConnect.Lean.sln` 通过。
2. `dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~Alpha101FactorRegistration|FactorStoreTests"` 全绿。
3. `pytest Tests/Python/FactorZoo/test_alpha101_descriptions.py Tests/Python/FactorZoo/test_build_catalog.py` 全绿。
4. `factor-catalog.yaml` 中 alpha042 含 `intent/scenarios/direction/family` 4 字段。
5. 真实磁盘读: `store.Get("alpha042", 603799.SH, 2026-07-24)` 返回 `≈ 1.06993`（dry-run 实测值）。

## 9. 风险与对策

- **风险**: `hypothesize.py::build_prompt` 当前只投影 `{id,category,selection_hint}`, 新 4 字段进不了 LLM 提示。
  **对策**: 实施时先读 `build_prompt`, 若未投影新字段则最小改把 `intent/direction/family` 拼进 `## 可选用因子` 节（scenarios 太长可只取首条）。属实施细节, 不另开 spec。
- **风险**: 101 条描述的 direction 标注主观性。
  **对策**: 对照 `docs/101.md` 原公式 + 论文; 不确定标"中性"; 实施时每条标注附依据（公式结构或论文段落）写入 yaml 注释。
- **风险**: 真实磁盘集成测试依赖 `result/factor-zoo/` 已生成。
  **对策**: 标 `[Explicit]`, 不进 CI 默认跑; 文档说明需先跑 factor_worker 或 dry-run。
