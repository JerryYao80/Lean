# 筹码因子计算与策略实现总结 (cyq-calc)

> 日期: 2026-06-26（初版）/ 2026-06-27（多因子增强）
> 分支: `fix/price-scaling-10000x`
> 关联: `docs/chip-peak-cyqperf-refactor.md`（重构决策）、`docs/chouma.md`（调研）

---

## 〇、多因子增强（2026-06-27 增量）

在 cyq_perf 纯筹码因子基础上，叠加 **moneyflow（资金流向）** + **daily_basic（估值）**
两类辅助信号，实现多因子融合。逻辑互补：筹码刻画"持仓成本结构（主力建仓/派发）"，
资金流刻画"当下资金意图（流入/流出）"，估值刻画"安全边际（PE/PB）"。

**数据实测验证**（避免 cyq_chips 式陷阱）：
- `moneyflow` ✅ 可用：v2 覆盖 5509 股，字段 `net_mf_amount`（万元）
- `daily_basic` ✅ 可用：v2 覆盖 5616 股，字段 `pe_ttm / pb / turnover_rate / total_mv`
- `moneyflow_hsgt` ❌ 不可用：37 个 parquet 全为空（0 行），未接入

**新增因子函数**（`ChipPeakFactors.py`）：
| 因子 | 输入字段 | 输出 | 逻辑 |
|------|---------|------|------|
| `net_moneyflow_signal` | `net_mf_amount` | [0,1] | 净流入达阈值5000万→1.0；流出→0 |
| `value_quality_score` | `pe_ttm, pb` | [0,1] | 低PE高分；PB>6打折；缺失→0.5 |
| `turnover_risk` | `turnover_rate` | [0,1] | <3%→0；3-10%→0.5；≥10%→1.0 |

**融合公式**（`composite_score` 增强）：
```
base_score = conc × (1-profit) × (1 + max(0, dev))          # 原筹码得分
final = base × (1 + mf_weight × mf_signal)                  # 资金流增益
              × (1 + value_weight × value_score)            # 估值增益
              × (1 - turnover_risk × 0.3)                    # 换手率风险打折
```
默认权重 `mf_weight=0.3, value_weight=0.2`。增强模式向后兼容：缺数据自动降级为纯筹码因子。

**新增数据加载方法**（`ChipDataLoader.py`）：
- `enriched_load_single(ts_code, date)` → 筹码+资金流+估值合并 Series
- `enriched_batch(ts_codes, date)` → 流式生成器（内存安全）
- `_load_moneyflow_single` / `_load_daily_basic_single` → 单表加载

**Alpha 集成**：`ChipPeakAlphaModel.update()` 改用 `enriched_batch()`，下游 `composite_score()`
自动消费增强字段。风控模型 `ChipPeakRiskManagementModel` 仍用纯筹码（派发区判断不依赖资金流）。

**测试**：13 个新增单元测试（资金流4 + 估值4 + 换手3 + 融合2）+ 5 个增强加载测试，全部通过（24/24）。

---

## 一、工作目标

基于 Tushare 筹码数据，在 LEAN 引擎中实现 **筹码峰量化策略**（全 A 股 + ETF），用于：
- **选股（Alpha）**：识别"低位单峰密集"（主力建仓区）的股票，得分越高越优先买入。
- **风控（Risk）**：过滤"高位单峰密集"（主力派发区）的股票，阻止新建仓，避免接盘被套。
- **可回测**：LEAN 原生五层框架 + IC 因子验证门控。

---

## 二、核心发现：cyq_chips vs cyq_perf

Tushare 筹码数据有两张表，**用途截然不同**：

| 表 | 结构 | 能否做筹码峰 |
|----|------|--------------|
| `cyq_chips` | 每 `trade_date` **仅 1 行单点位**（`price`=成交均价，`percent`=该价位集中度标量） | ❌ 无法支撑多桶因子 |
| `cyq_perf` | 每 `trade_date` 1 行，含 **5 档成本分位** + 加权均价 + 获利盘比例 | ✅ 真正的离散分布 |

**`cyq_perf` 字段：**
```
his_low / his_high        筹码历史最低/最高成本（分布边界）
cost_5pct  .. cost_95pct  5%/15%/50%/85%/95% 筹码成本分位（5 档离散分布）
weight_avg                加权平均成本（直接 = 平均成本）
winner_rate               获利盘比例 %（直接 = profit_ratio）
```
覆盖 5536 股，2018-01 至 2026-06，仅存在于 `tushare_data_v2`（v1 的 cyq_perf 为空）。

---

## 三、因子计算公式（增强版，基于 cyq_perf + moneyflow + daily_basic）

输入：`ChipDataLoader.enriched_load_single()` 返回的 `pd.Series`（cyq_perf 一行 + adj 复权校正字段 + moneyflow/daily_basic 增强字段）。

### 原筹码因子（cyq_perf 核心）

#### 1. 筹码集中度 concentration ∈ [0,1]
```
spread = (cost_95pct_adj - cost_5pct_adj) / weight_avg_adj
concentration = exp(-spread)
```
- spread 越小（90% 筹码带宽越窄）→ 越集中 → 越接近 1。
- **带宽法对 adj_factor 不敏感**（分子分母同尺度），稳健。
- 替代了原计划的 Shannon 熵法（Shannon 熵需多桶分布，cyq_chips 单点位下退化失效）。

#### 2. 获利盘比例 profit_ratio ∈ [0,1]
```
profit_ratio = winner_rate / 100
```
直接取 cyq_perf，无需累加多桶。

#### 3. 平均成本 average_cost
```
average_cost = weight_avg_adj   （× adj_factor 复权校正）
```

#### 4. 平均成本偏离度 cost_deviation
```
cost_deviation = (current_price - weight_avg_adj) / weight_avg_adj
```
`current_price` 来自 LEAN 行情（`security.price`），与 weight_avg_adj 须同复权口径（后复权）。

#### 5. 形态分类 classify_peak（阈值法）
```
concentration < 0.6                    → DIVERGENT      发散
集中 & profit_ratio < 0.2              → LOW_SINGLE_PEAK   低位建仓区 ✅
集中 & profit_ratio > 0.8              → HIGH_SINGLE_PEAK  高位派发区 ⚠️
其余                                   → DIVERGENT
```

### 增强因子（moneyflow + daily_basic）

#### 6. 资金流信号 net_moneyflow_signal ∈ [0,1]
```
threshold = 5000.0  （万元）
signal = max(0, min(1, net_mf_amount / threshold))
```
- 净流入达到 5000 万元 → signal = 1.0（满分）
- 流出（负值）→ signal = 0.0
- 缺失 → 0.0（中性）

**互补逻辑**：低位单峰 + 大单流入 = 主力启动信号；低位单峰 + 无流入 = 建仓未完（观望）。

#### 7. 估值质量 score value_quality_score ∈ [0,1]
```
PE_TTM < 20  → 1.0（低估值）
20 ≤ PE < 40 → 0.8
40 ≤ PE < 60 → 0.6
PE ≥ 60      → 0.4
PB > 6       → × 0.8（高PB打折）
缺失         → 0.5（中性）
```

**安全边际**：过滤高位接盘；低估值提供下跌缓冲。

#### 8. 换手率风险 turnover_risk ∈ [0,1]
```
turnover < 3%   → 0.0（正常）
3% ≤ turnover < 10% → 0.5（偏高）
turnover ≥ 10% → 1.0（极高，短期炒作）
```

### 9. 综合得分 composite_score（多因子融合）
```
base_score = concentration × (1-profit) × (1 + max(0, deviation))   # 原筹码得分
mf_signal = net_moneyflow_signal(chip_row)
value_score = value_quality_score(chip_row)
turnover_risk = turnover_risk(chip_row)

final_score = base_score × (1 + 0.3 × mf_signal)
                        × (1 + 0.2 × value_score)
                        × (1 - turnover_risk × 0.3)
```

- LOW_SINGLE_PEAK（建仓区）→ 正分，其余 → 0
- **资金流增益**：大单流入最多 +30% 增益
- **估值增益**：低估值最多 +20% 增益
- **换手率风险**：高换手率最多 -30% 打折
- 数据缺失 → 自动降级为纯筹码得分（向后兼容）

---

## 四、复权校正

cyq_perf 成本字段为原始（未复权）价位；LEAN 行情为复权价。统一用 `adj_factor` 校正：
```
cost_x_adj   = cost_x   × adj_factor
weight_avg_adj = weight_avg × adj_factor
```
`winner_rate` 与 `concentration`（带宽比）天然 adj 不变，无需校正。

> 已知注意点：`cost_deviation` 依赖 `current_price` 与 `weight_avg_adj` 同口径。当前用后复权（× adj_factor）。若 LEAN daily 为前复权，需进一步除以最新 adj_factor；回测后按偏差再校准。

---

## 五、策略架构（LEAN 五层）

```
ChipPeakStrategyAlgorithm (QCAlgorithm)
  ├─ Universe : AShareUniverseSelectionModel   全 A 股+ETF，cyq_perf 硬筛，A 股本地化模型
  ├─ Alpha    : ChipPeakAlphaModel             流式扫描 cyq_perf → composite_score → Top-20 UP insight
  ├─ Portfolio: TopNEqualWeightPCM             周频等权，100 手取整，no_trade_band
  ├─ Risk     : ChipPeakRiskManagementModel    过滤 HIGH_SINGLE_PEAK（派发区）insight
  └─ Execution: ImmediateExecutionModel
  风险利率: ChinaInterestRateProvider (SHIBOR 1Y)
```

- **Universe**：从 `cyq_perf` 目录发现全部 ts_code（数据可用性硬筛），`AddEquity` 注册并套用 `AShareStockFeeModel/FillModel/BuyingPowerModel`，股票 T+1 / ETF T+0。
- **Alpha**：分批 500 只流式加载（内存安全），每日 `composite_score` 排序取 Top-20，发出 UP insight（magnitude=score）。
- **Risk**：仅检查 ≤Top-N 只持仓 insight，惰性 `load_single`，HIGH_SINGLE_PEAK 砍 insight（阻止建仓）。

---

## 六、交付文件

| 文件 | 用途 | 多因子增强（2026-06-27） |
|------|------|--------------------------|
| `ToolBox/ChipDataLoader.py` | cyq_perf 流式加载 + adj 复权校正 | ✅ 新增 `enriched_load_single`, `enriched_batch`, `_load_moneyflow_single`, `_load_daily_basic_single` |
| `Algorithm.Python/ChipPeakFactors.py` | 纯因子库（静态方法） | ✅ 新增 `net_moneyflow_signal`, `value_quality_score`, `turnover_risk`；增强 `composite_score` 支持多因子融合 |
| `ToolBox/ChipPeakICValidator.py` | 离线 IC 验证门控 | ✅ 改用 `enriched_batch`，统计增强因子分布 |
| `Algorithm.Python/AShareUniverseSelectionModel.py` | 全 A 股+ETF 选域 | 不变 |
| `Algorithm.Python/ChipPeakAlphaModel.py` | 筹码打分 → Top-N insight | ✅ 改用 `enriched_batch` 加载增强数据 |
| `Algorithm.Python/ChipPeakRiskManagementModel.py` | 派发区风控过滤 | 不变（纯筹码判断） |
| `Algorithm.Python/ChipPeakStrategyAlgorithm.py` | 五层集成主算法 | 不变 |
| `Launcher/config/config-chip-peak.json` | 回测配置 | 不变（数据路径已指向 v2） |
| `Tests/Python/Scripts/ChipPeakFactorsTests.py` | 因子单元测试 | ✅ 新增 13 个增强因子测试（共 24 项） |
| `Tests/Python/Scripts/ChipDataLoaderTests.py` | 数据加载测试 | 不变 |
| `Tests/Python/Scripts/ChipDataLoaderEnrichedTests.py` | 增强数据加载测试 | ✅ 新增 5 项测试 |

---

## 七、验证结果

- **单元测试**：11 因子 + 4 数据测试全部通过。
- **IC 验证**（全 A 股 2023-11-13 快照）：
  - 处理 5218/5536 股（94%），score_mean=0.052，positive_ratio=7.6%
  - 形态分布：低位建仓 399 / 高位派发 1406 / 发散 3413 → **真实信号，非退化**
- **LEAN 回测烟测**：
  - `[AShareUniverse] loaded 5537 symbols` ✅
  - `[ChipPeakAlpha] scanned N, emitted 20 insights` ✅ 端到端跑通无崩溃

---

## 八、关键经验教训

1. **数据假设必须实测验证**：原计划盲信"cyq_chips 是多桶分布"，从未打开 parquet，导致因子数学在真实数据上完全退化。合成测试不能替代真实数据验证。
2. **cyq_perf 已聚合好关键标量**（`winner_rate`、`weight_avg`），无需自己累加多桶，更准确且 adj 稳健。
3. **LEAN Python 方法名契约**：`RiskManagementModel.manage_risk`（非 `manage`）；`UserDefinedUniverse` 构造需 `SubscriptionDataConfig`（手动 `add_equity` 注册更简单）。
4. **config data-folder** 必须指向 LEAN `Data/`（含 symbol-properties + equity/sse|szse），tushare 路径放 `parameters.tushare-data-path`。

---

## 九、运行方式

```bash
# 离线因子验证（门控）
cd /home/project/hope/Lean
python3 ToolBox/ChipPeakICValidator.py --start 2022-01-01 --end 2025-12-31

# LEAN 回测（全 A 股，2022-2025，较慢）
cd Launcher/bin/Debug
/usr/local/dotnet/dotnet QuantConnect.Lean.Launcher.dll \
  --config /home/project/hope/Lean/Launcher/config/config-chip-peak.json
```
