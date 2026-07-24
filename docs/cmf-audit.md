# 筹码峰（Chip Peak）计算审计

**审计日期：** 2026-07-07
**审计范围：** LEAN 项目所有筹码峰 / 筹码分布 / CYQ 相关计算与消费位点
**审计方法：** 4 路并行 workflow（C# 实现 / Python 实现 / 数据源 / 策略消费者）+ 1 路对抗验证（独立读核心代码 + parquet schema）
**结论：** 全部 5 路审计 verdict = confirmed，互相印证。核心结论一致。

---

## 一句话结论

**本项目不自行从逐笔成交重建筹码分布，也没有任何 argmax / 直方图取模 / 核密度峰值检测。所谓"筹码峰"全部源自 tushare 预算好的 `cyq_perf` 5 档成本分位表，用"带宽法"定义峰：90% 成本带宽（`cost_95 - cost_5`）相对加权均价越窄 → 越集中 → 越接近单峰。`cyq_chips` 表实测为每交易日单点位标量，不可用于多桶因子数学，已被全面弃用。**

---

## 一、使用的数据

### 主表：tushare `cyq_perf`（筹码分布 5 档成本分位）

**物理位置：** `/home/project/tushare-downloader/tushare_data_v2/cyq_perf/`
**分区方案：** Hive 风格 `ts_code=<symbol>/data.parquet`（5543 个标的目录，2018-01 至 2026-06）
**实测 schema**（以 000001.SZ 为例，2060 行 × 11 列，每交易日 1 行）：

| 字段 | 含义 | 单位 | 用途 |
|---|---|---|---|
| `ts_code` | 标的代码 | — | 分区键 |
| `trade_date` | 交易日 | YYYYMMDD | 时间键 |
| `his_low` | 历史成本下界 | 元 | 分布边界（未参与计算） |
| `his_high` | 历史成本上界 | 元 | 分布边界（未参与计算） |
| `cost_5pct` | 5% 成本分位 | 元 | **带宽下沿** |
| `cost_15pct` | 15% 成本分位 | 元 | 加载但**未使用** |
| `cost_50pct` | 50% 成本分位（中位成本） | 元 | **峰位/支撑位** |
| `cost_85pct` | 85% 成本分位 | 元 | 加载但**未使用** |
| `cost_95pct` | 95% 成本分位 | 元 | **带宽上沿** |
| `weight_avg` | 加权平均成本 | 元 | **带宽归一化**（Python 路径） |
| `winner_rate` | 获利盘比例 | % (0-100) | **峰形态分类** |

**样本行（000001.SZ 20260702）：** `cost_5pct=10.0, cost_50pct=10.8, cost_95pct=12.2, weight_avg=10.92, winner_rate=11.92`

> ⚠️ `cost_15pct` / `cost_85pct` 被 `ChipDataLoader._COST_FIELDS` 加载，但**没有任何筹码峰算术消费它们**——只用到 `cost_5pct / cost_50pct / cost_95pct / weight_avg / winner_rate` 五个字段。

### 辅助表（复权与增强）

| 表 | 用途 |
|---|---|
| `adj_factor` | 复权校正：`cost_x_adj = cost_x * adj_factor`，使成本分位与 LEAN 后复权价格同口径；`winner_rate` 与带宽比值复权不变 |
| `moneyflow` | 增强模式：`net_mf_amount` 资金流信号（万元） |
| `daily_basic` | 增强模式：`pe_ttm / pb / turnover_rate / total_mv` 估值质量分 |

### 弃用表：tushare `cyq_chips`

**物理位置：** `/home/project/tushare-downloader/tushare_data_v2/cyq_chips/`
**实测 schema**（000001.SZ，258 行 × 4 列，每交易日**仅 1 行**）：

| 字段 | 含义 |
|---|---|
| `ts_code` | 标的 |
| `trade_date` | 交易日 |
| `price` | 当日均价（单一点位） |
| `percent` | 该点位的集中度标量 |

**实测 rows-per-trade_date：** count=258, mean=1.0, std=0.0, min=1.0, max=1.0 —— **每交易日单点位标量，不是多桶直方图**，无法支撑 Shannon 熵 / 多桶累加 / argmax 取模。

`docs/chip-peak-cyqperf-refactor.md`（2026-06-26）正式记录了弃用决定。`api_registry.py` 里 `cyq_chips` 标 `enabled=False`；`ChipDataLoader.py` docstring 明确写"cyq_chips 实测为每交易日单点位，无法支撑多桶因子数学；改用 cyq_perf 的离散分位分布"。**全项目无任何代码读取 cyq_chips**（仅 registry/注释里残留）。

---

## 二、原理：带宽法（不是取模）

### 核心公式（Python 路径，`Algorithm.Python/ChipPeakFactors.py:58`）

```python
def concentration(chip_row) -> float:
    """筹码集中度 = exp(-spread), spread = (cost_95-cost_5)/weight_avg. [0,1]"""
    c95 = _f(chip_row, 'cost_95pct_adj')
    c5  = _f(chip_row, 'cost_5pct_adj')
    wavg = _f(chip_row, 'weight_avg_adj')
    if math.isnan(c95) or math.isnan(c5) or wavg <= 0:
        return float('nan')
    spread = (c95 - c5) / wavg
    if spread < 0: spread = 0.0
    return float(math.exp(-spread))
```

**原理：**
- `spread = (cost_95 - cost_5) / weight_avg` —— 90% 成本带宽占加权均价的比例
- `concentration = exp(-spread)` —— 带宽越窄 → spread 越小 → concentration 越接近 1（越集中/越"尖峰"）
- 对 `adj_factor` 不敏感（分子分母同尺度，复权自消）—— 稳健

### 峰形态分类（阈值法，`ChipPeakFactors.py:105`）

```python
def classify_peak(chip_row, current_price, params):
    conc = concentration(chip_row)
    profit = profit_ratio(chip_row)   # winner_rate / 100
    if isnan(conc) or isnan(profit): return DIVERGENT
    if conc < params['single_concentration']:   # 默认 0.6
        return DIVERGENT
    if profit < params['low_profit_max']:       # 默认 0.2
        return LOW_SINGLE_PEAK     # 低位单峰密集（建仓区）
    if profit > params['high_profit_min']:      # 默认 0.8
        return HIGH_SINGLE_PEAK    # 高位单峰密集（派发区）
    return DIVERGENT
```

| 形态 | 条件 | 含义 |
|---|---|---|
| `LOW_SINGLE_PEAK` | conc ≥ 0.6 且 winner_rate < 20% | 低位单峰密集——建仓区，发多头信号 |
| `HIGH_SINGLE_PEAK` | conc ≥ 0.6 且 winner_rate > 80% | 高位单峰密集——派发区，风险门过滤 |
| `DIVERGENT` | 其余 | 发散，不发信号 |

### 复合打分（仅 LOW_SINGLE_PEAK 触发，`ChipPeakFactors.py:228`）

```python
base_score = conc * (1 - profit) * (1 + max(0, dev))
# dev = (current_price - weight_avg_adj) / weight_avg_adj  (成本偏离度)
final_score = base_score * (1 + mf_weight * mf_signal) * (1 + value_weight * value_score) * (1 - turnover_risk * 0.3)
```

- `base_score` = 集中度 × (1−获利盘) × (1+正偏离) —— 选"集中 + 低位 + 略偏离成本"的标的
- 增强项：资金流净流入 + 低估值加分 + 高换手扣分
- 非 LOW_SINGLE_PEAK 一律返回 0

---

## 三、三条独立计算路径（都源 cyq_perf，都用带宽法）

### 路径 A：Python 实时路径（FactorZoo）

```
ChipPeakFactorZooAlphaModel.Update()  [C#, Models/ChipPeakFactorZooAlphaModel.cs:125]
  → Python.NET 调 ChipDataLoader.enriched_load_single(tsCode, date)  [ToolBox/ChipDataLoader.py:103]
    → 读 cyq_perf/ts_code=<sym>/data.parquet + adj_factor + moneyflow + daily_basic
    → cost_x_adj = cost_x * adj_factor；winner_rate 原样
  → ChipPeakFactors.composite_score(chipRow, price, params)  [Algorithm.Python/ChipPeakFactors.py]
    → concentration = exp(-(cost_95-cost_5)/weight_avg)
    → profit_ratio = winner_rate / 100
    → classify_peak → 仅 LOW_SINGLE_PEAK 触发 base_score
  → _compositeFactor.InjectValue(sym, score)  [C# ChipPeakCompositeFactor]
```

**C# 因子类（`Common/Factors/Chip/*`：ChipPeakCompositeFactor / ConcentrationFactor / ProfitRatioFactor / PeakPatternFactor / CostDeviationFactor）全部是 `ComputeMode=Precomputed` 外壳**，`Compute()` 只返回 `InjectValue` 注入的标量，自身不做任何算术。真正的数学在 Python。

### 路径 B：C# 多因子路径（MultiFamily）

```
AShareMultiFamilySignalModel  [Algorithm.CSharp/AShareMultiFamilySignalModel.cs]
  → 从预合并 CSV 读 cost_5pct/cost_50pct/cost_95pct/winner_rate（via AShareTushareFactorData.GetDecimal）
  → ChipSpread = -(cost_95 - cost_5) / cost_50          [line 496]
  → CostProximity = -|cost_50/close - 1|                [line 566]
  → chip_cost family = 0.5*winner_rate + 0.5*CostProximity
  → chip_concentration family = 0.4*ChipSpread + 0.4*winner_rate + 0.2*CostProximity
  → ApplyFactorDict 横截面 z-score
```

**与路径 A 的差异：** 带宽归一化用 `cost_50`（中位成本）而非 `weight_avg`；`cost_50pct` 被当作峰位/支撑位，`close` 与 `cost_50pct` 的接近度定义支撑强度。

### 路径 C：离线 Barra 预烘焙路径

```
data-source/tushare/barra_cne5v2_factor_builder.py::_compute_chipcost_descriptors  [line 417]
  → load_dataset('cyq_perf', fields=[winner_rate, cost_5pct, cost_50pct, cost_95pct])
  → WINRATE = winner_rate
  → CSPREAD = -(cost_95 - cost_5) / cost_50    # 与路径 B 同公式
  → chipcost = 0.5*WINRATE + 0.5*CSPREAD
  → 写入 CSV 第 15 列 'chipcost'
  → BarraCNE5 V2/V2.1/V3/V3.2/V4/V4.2 通过 AShareBarraCNE5V2FactorData.cs:143 读 csv[15]
  → 作为 15 个 Barra 风格因子之一，默认权重 0.07（high_vol regime 0.10）
```

**三条路径一致性：** 全部源 `cyq_perf`，全部用带宽法（`cost_95 - cost_5` 作 90% 带宽）。路径 A 归一化用 `weight_avg`，路径 B/C 用 `cost_50`——**两种归一化都合理但口径不同**，不是 bug，是两种"集中度"定义并存。

---

## 四、策略消费者（谁真正用到筹码峰）

| 策略 / 模型 | 文件 | 角色 | 数据路径 |
|---|---|---|---|
| **ChipPeakStrategyAlgorithm** | `Algorithm.Python/ChipPeakStrategyAlgorithm.py:35` | **alpha + risk 双重** | 路径 A 实时 cyq_perf |
| ChipPeakFactorZooAlphaModel | `Algorithm.CSharp/Models/ChipPeakFactorZooAlphaModel.cs` | alpha（注入 ChipPeakCompositeFactor） | 路径 A |
| ChipPeakRiskManagementModel | `Algorithm.Python/ChipPeakRiskManagementModel.py:61` | **风险门**：过滤 HIGH_SINGLE_PEAK | 路径 A `classify_peak` |
| ChipPeakAlphaModel (Py) | `Algorithm.Python/ChipPeakAlphaModel.py` | alpha | 路径 A |
| AShareDataDrivenMultiFamilyAlgorithm | `AShareDataDrivenMultiFamilyAlgorithm.cs:841` | alpha（chip_cost + chip_concentration 两个家族） | 路径 B 实时 CSV |
| BarraCNE5 V2/V2.1/V3/V3.2/V4/V4.2 | `AShareBarraCNE5V*AlphaModel.cs` | alpha（chipcost 因子，权重 0.07/0.10） | 路径 C 离线 CSV |
| CrowdingFactors.py | `Algorithm.Python/CrowdingFactors.py:137` | **休眠**（无策略 wire） | 路径 A 字段 |
| **OptionVolArb5Layer** | — | **不用筹码**（IV/HV/skew 驱动） | — |
| **VarStrategy** | — | **不用筹码**（VaR/drawdown 驱动） | — |

**RL 状态：** `AShareBarraCNE5V4Algorithm.SerializeRlState` 只导出 `tpv/cash_pct/positions/drawdown/n_open_positions`，**筹码峰/chipcost 不在 RL state 向量里**——RL 策略不直接观察筹码数据，只通过 alpha 选股间接影响。

---

## 五、值得注意的发现（gaps）

1. **VaRFactor 死线**：`Common/Factors/Risk/VaRFactor.cs:37` 声明 `chip_concentration` 为依赖、`FactorRegistry.Get("chip_concentration")` 取了句柄，但**全文从未调用 `_chipConcentration.Compute()`**。依赖声明仅用于元数据，筹码数据并未实际流入 VaR regime 评分。需人工确认是有意（metadata-only）还是漏接。

2. **MC 因子暴露错位**：`AShareDataDrivenMultiFamilyAlgorithm.cs:2762` 在 `StrategyMonteCarloFactorExposure` 投影里把 `row.ChipCost` 映射进 `ResidualVolatility` 槽位（后续家族整体左移一列）。`chip_cost` 被当成了 `residual volatility`——**疑似列错位 bug**，需人工核对。

3. **两种集中度公式并存**：路径 A 用 `exp(-(cost_95-cost_5)/weight_avg)`，路径 B/C 用 `-(cost_95-cost_5)/cost_50`。前者是 `[0,1]` 概率式集中度，后者是带符号带宽比。两套数字不可直接横比，但各自在各自策略里自洽。

4. **`cost_15pct` / `cost_85pct` 闲置**：`ChipDataLoader` 加载了 5 档，但算术只用 3 档（5/50/95）。中间两档（15/85）可用来做更细的带宽/偏度刻画，目前未利用。

5. **复权口径依赖人工对齐**：`cost_deviation` 要求 `current_price` 与 `weight_avg_adj` 同后复权口径。`ChipDataLoader` 用 `cost_x * adj_factor`（后复权）。若 LEAN daily 价格用前复权，`cost_deviation` 会偏。无自动口径校验。

6. **cyq_perf 无 cron 增量**：`api_registry.py` 里 `cyq_perf` 标 `enabled=False`（需 `ts_code+start_date+end_date` 特殊参数），tushare_worker 通用分块下载器**不自动刷新** cyq_perf。数据由独立/手工流程加载，最近 mtime 2026-07-03。回测前需确认覆盖区间。

7. **HIGH_SINGLE_PEAK 风险门静默退化**：`ChipPeakRiskManagementModel` 在 cyq_perf 缺数据时 `classify_peak` 返回 `DIVERGENT`（保守放行），风险门变成 no-op pass-through——**静默漂移而非崩溃**，需监控 cyq_perf 数据完整性。

8. **serenity-skill `chips` 命令**：`/root/.claude/skills/serenity-skill/scripts/tushare_analysis.py:793` 的 `chips` 子命令也只是返回 cyq_perf 最近 N 行的 5 档分位，**不计算 peak/argmax**——"peak" 隐式等于 `cost_50pct` 序列。与 LEAN 项目口径一致。

---

## 六、与项目记忆的对照

项目 `MEMORY.md` 记载："cyq_chips 单点位不可用；筹码分布策略须用 cyq_perf 5 档成本分位"。

**审计结论：完全吻合。**
- cyq_chips 实测每交易日 1 行（price + percent 标量），全项目无代码读取
- cyq_perf 5 档成本分位（cost_5/15/50/85/95pct + weight_avg + winner_rate）是所有筹码峰算术的唯一数据源
- `docs/chip-peak-cyqperf-refactor.md` 正式记录了这一重构决定

---

## 七、总账

| 维度 | 结论 |
|---|---|
| **数据源** | tushare `cyq_perf` parquet（`ts_code=<sym>/data.parquet`），5 档成本分位 + weight_avg + winner_rate |
| **复权** | `adj_factor` 表，`cost_x_adj = cost_x * adj_factor`；winner_rate 与带宽比复权不变 |
| **弃用** | `cyq_chips`（单点位标量，不可多桶数学） |
| **原理** | **带宽法**：`spread = (cost_95 - cost_5) / weight_avg`（或 `/cost_50`），`concentration = exp(-spread)`；非 argmax / 非直方图取模 / 非核密度 |
| **峰形态** | 阈值法：conc ≥ 0.6 且 winner_rate < 20% → 建仓区；> 80% → 派发区 |
| **三路径** | A) Python 实时（FactorZoo，归一化 weight_avg）；B) C# MultiFamily（归一化 cost_50）；C) 离线 Barra chipcost（归一化 cost_50） |
| **C# 因子类** | 全部 Precomputed 外壳，算术在 Python |
| **消费者** | ChipPeakStrategy（alpha+risk）、MultiFamily、BarraCNE5 V2-V4.2；OptionVolArb / VarStrategy 不用 |
| **RL state** | 不含筹码字段 |
| **潜在 bug** | VaRFactor 死线、MC 暴露列错位（chipcost→ResidualVolatility） |

---

## 来源文件

**核心计算：**
- `Algorithm.Python/ChipPeakFactors.py`（concentration line 58、classify_peak line 105、composite_score line 228）
- `ToolBox/ChipDataLoader.py`（_COST_FIELDS line 33、复权 line 127-129）
- `Algorithm.CSharp/AShareMultiFamilySignalModel.cs`（ChipSpread line 496、CostProximity line 566）
- `data-source/tushare/barra_cne5v2_factor_builder.py`（_compute_chipcost_descriptors line 417）

**C# 外壳（Precomputed）：**
- `Common/Factors/Chip/ChipPeakCompositeFactor.cs`
- `Common/Factors/Chip/ConcentrationFactor.cs`
- `Common/Factors/Chip/ProfitRatioFactor.cs`
- `Common/Factors/Chip/PeakPatternFactor.cs`
- `Common/Factors/Chip/CostDeviationFactor.cs`
- `Common/Factors/Core/FactorRegistry.cs:51`

**桥接与消费者：**
- `Algorithm.CSharp/Models/ChipPeakFactorZooAlphaModel.cs:80,125,132`
- `Algorithm.Python/ChipPeakStrategyAlgorithm.py:35`
- `Algorithm.Python/ChipPeakRiskManagementModel.py:61`
- `Algorithm.CSharp/AShareBarraCNE5V2FactorData.cs:143`
- `Algorithm.CSharp/AShareBarraCNE5V4AlphaModel.cs:414`
- `Algorithm.CSharp/AShareDataDrivenMultiFamilyAlgorithm.cs:841,2762`

**数据源 schema：**
- `/home/project/tushare-downloader/tushare_data_v2/cyq_perf/ts_code=000001.SZ/data.parquet`（11 列，每交易日 1 行）
- `/home/project/tushare-downloader/tushare_data_v2/cyq_chips/ts_code=000001.SZ/data.parquet`（4 列，每交易日 1 行——弃用）
- `data-source/tushare/api_registry.py:439-454`

**重构决定：**
- `docs/chip-peak-cyqperf-refactor.md`（2026-06-26，cyq_chips → cyq_perf 重构记录）

**serenity-skill 口径对照：**
- `/root/.claude/skills/serenity-skill/scripts/tushare_analysis.py:793`（`chips` 命令同样读 cyq_perf，不取模）
