# ChipPeakRiskManagementModel - 筹码峰策略五层架构文档

本文档详细说明筹码峰量化策略的 LEAN 原生五层架构流程。

## 策略概述

筹码峰策略基于 A 股筹码分布数据（`cyq_perf`），识别低位单峰密集形态（建仓区），结合资金流向与估值因子生成交易信号。策略完全遵循 LEAN 框架五层架构：

```
Universe Selection → Alpha → Portfolio Construction → Risk Management → Execution
```

---

## 1. Universe Selection（选股池层）

### 模型
- **实现**: `AShareUniverseSelectionModel`
- **位置**: `Algorithm.Python/AShareUniverseSelectionModel.py`

### 职责
从数据文件夹加载全 A 股股票池（含 ETF），作为 Alpha 层的候选标的。

### 输入/输出
| 方向 | 内容 |
|---|---|
| **输入** | 数据文件夹路径（`/home/project/tushare-downloader/tushare_data_v2`） |
| **输出** | `active_securities`（活跃股票列表） → 传递给 Alpha 层 |

### 关键配置
```python
# ChipPeakStrategyAlgorithm.py:29
self.set_universe_selection(
    AShareUniverseSelectionModel(data_folder, include_etf=True)
)
```

### 参数说明
- `data_folder`: Tushare 数据目录
- `include_etf`: 是否包含 ETF（默认 `True`）

---

## 2. Alpha Model（信号生成层）

### 模型
- **实现**: `ChipPeakAlphaModel`
- **位置**: `Algorithm.Python/ChipPeakAlphaModel.py`
- **继承**: `AlphaModel` → `IAlphaModel`

### 职责
流式扫描股票池，计算筹码峰因子得分，筛选 Top-N 标的，生成方向性 Insight。

### 核心流程
```
分批加载 cyq_perf + moneyflow + daily_basic
    ↓
计算多因子得分（ChipPeakFactors.composite_score）
    ↓
筛选得分 > 0 且 Top-N
    ↓
生成 Insight(UP, magnitude=score, horizon=7d)
```

### 关键因子（ChipPeakFactors.py）

#### 基础筹码因子
| 因子 | 计算公式 | 含义 |
|---|---|---|
| **concentration** | `exp(-spread)` | 筹码集中度，spread = (cost_95 - cost_5) / weight_avg |
| **profit_ratio** | `winner_rate / 100` | 获利盘比例 |
| **classify_peak** | 阈值分类 | 低位单峰 / 高位单峰 / 发散 |

#### 增强因子
| 因子 | 权重 | 含义 |
|---|---|---|
| **net_moneyflow_signal** | 0.3 | 资金净流入信号（标准化到 [0,1]） |
| **value_quality_score** | 0.2 | 估值质量（低 PE/PB 高分） |
| **turnover_risk** | -0.3 | 换手率风险惩罚 |

#### 综合得分公式
```python
final_score = base_score
    * (1 + mf_weight * mf_signal)
    * (1 + value_weight * value_score)
    * (1 - turnover_risk * 0.3)
```

其中：
- `base_score = concentration * (1 - profit) * (1 + max(0, cost_deviation))`
- 仅 **低位单峰密集**（`LOW_SINGLE_PEAK`）得正分

### 输入/输出
| 方向 | 内容 |
|---|---|
| **输入** | `Slice data`（时间切片） + `active_securities`（股票池） |
| **输出** | `List[Insight]`（方向性信号） → 传递给 Portfolio 层 |

### 关键配置
```python
# ChipPeakStrategyAlgorithm.py:31
self.set_alpha(
    ChipPeakAlphaModel(data_folder, top_n=5, batch_size=500, max_workers=2)
)
```

### 参数说明
- `top_n=5`: 快速模式，仅选 Top 5
- `batch_size=500`: 分批加载控制内存
- `max_workers=2`: 双线程并行加载 parquet（IO 密集型加速）

### 内存控制策略
```python
# 分批流式处理（内存控制核心）
for i in range(0, len(codes), self.batch_size):
    batch_codes = codes[i:i + self.batch_size]
    for ts_code, chip_row in self.loader.enriched_batch(batch_codes, current_date):
        # chip_row 是 pd.Series，用完即释放
        score = ChipPeakFactors.composite_score(chip_row, current_price, self.params)
        ...
    # 循环结束后 chip_row 自动 GC
```

---

## 3. Portfolio Construction（组合构建层）

### 模型
- **实现**: `TopNEqualWeightPCM`
- **位置**: `Algorithm.Python/TopNEqualWeightPCM.py`
- **继承**: `PortfolioConstructionModel` → `IPortfolioConstructionModel`

### 职责
将 Alpha 层的 Insight 转换为目标仓位，等权配置 Top-N 标的。

### 核心逻辑
```python
# 等权分配
weight = 1.0 / top_n

# 应用无交易带宽（避免小额频繁调仓）
if abs(current_quantity - target_quantity) < no_trade_band * total_portfolio_value:
    skip rebalance
```

### 输入/输出
| 方向 | 内容 |
|---|---|
| **输入** | `Insight[]`（Alpha 信号） |
| **输出** | `IPortfolioTarget[]`（目标仓位） → 传递给 Risk 层 |

### 关键配置
```python
# ChipPeakStrategyAlgorithm.py:33
self.set_portfolio_construction(
    TopNEqualWeightPCM(top_n=5, rebalance=timedelta(days=7), no_trade_band=0.02)
)
```

### 参数说明
- `top_n=5`: 最大持仓数量（与 Alpha 层 `top_n` 对齐）
- `rebalance=timedelta(days=7)`: 周频再平衡
- `no_trade_band=0.02`: 2% 无交易带宽（低于此阈值不调仓）

---

## 4. Risk Management（风险管理层）

### 模型
- **实现**: `ChipPeakRiskManagementModel`
- **位置**: `Algorithm.Python/ChipPeakRiskManagementModel.py`
- **继承**: `RiskManagementModel` → `IRiskManagementModel`

### 职责
根据风险信号调整目标仓位，控制下行风险（止损/止盈/仓位上限）。

### 核心接口
```csharp
// IRiskManagementModel.cs
IEnumerable<IPortfolioTarget> ManageRisk(
    QCAlgorithm algorithm,
    IPortfolioTarget[] targets
);
```

### 输入/输出
| 方向 | 内容 |
|---|---|
| **输入** | `IPortfolioTarget[]`（Portfolio 目标） |
| **输出** | `IPortfolioTarget[]`（风险调整后目标） → 传递给 Execution 层 |

### 关键配置
```python
# ChipPeakStrategyAlgorithm.py:35
self.set_risk_management(ChipPeakRiskManagementModel(data_folder))
```

### 风险控制维度（推断）
- **止损**: 单只股票最大亏损阈值（如 -10%）
- **止盈**: 单只股票最大盈利阈值（如 +20%）
- **仓位上限**: 单只股票不超过总仓位的 X%
- **总杠杆**: 总持仓不超过现金的 Y%

### VPIN 扩展建议
VPIN（订单流毒性指标）最契合 Risk Management 层：

```python
class VpinRiskManagementModel(RiskManagementModel):
    def manage_risk(self, algorithm, targets):
        for target in targets:
            vpin = self._vpin_indicator[target.symbol]
            if vpin > self._toxicity_threshold:
                # 毒性过高 → 减仓或平仓
                yield IPortfolioTarget.Zero(target.symbol)
            else:
                yield target
```

**设计要点**:
- VPIN 计算应作为独立 Indicator（`Indicators/VpinIndicator.cs`）
- Risk 模型消费 Indicator，不直接计算
- 保持与 LEAN 原生接口对齐（符合 `feedback_lean-native-only`）

---

## 5. Execution（执行层）

### 模型
- **实现**: `ImmediateExecutionModel`
- **位置**: LEAN 框架内置模型
- **继承**: `ExecutionModel` → `IExecutionModel`

### 职责
将目标仓位转换为实际订单，立即执行。

### 核心逻辑
```csharp
// 收到目标后立即下单
foreach (var target in targets) {
    var order = new MarketOrder(target.Symbol, target.Quantity);
    algorithm.Order(order);
}
```

### 输入/输出
| 方向 | 内容 |
|---|---|
| **输入** | `IPortfolioTarget[]`（Risk 调整后目标） |
| **输出** | `Order[]`（订单） → 发送给 Brokerage |

### 关键配置
```python
# ChipPeakStrategyAlgorithm.py:37
self.set_execution(ImmediateExecutionModel())
```

### 执行方式
- **市价单**: 收到目标后立即以市价成交
- **无延迟**: 无算法拆单、无时间加权
- **适用场景**: 快速回测、模拟盘

### 扩展方向
生产环境可替换为：
- `VolumeWeightedAveragePriceExecutionModel`: VWAP 执行（降低冲击成本）
- `StandardDeviationExecutionModel`: 波动率自适应执行

---

## 数据流向图

```
┌─────────────────────────────────┐
│ AShareUniverseSelectionModel    │
│ (Universe Selection)            │
│ 输入: data_folder               │
│ 输出: active_securities         │
└─────────────────────────────────┘
            ↓ 股票池
┌─────────────────────────────────┐
│ ChipPeakAlphaModel              │
│ (Alpha)                         │
│ 分批加载 cyq_perf + moneyflow   │
│ + daily_basic                   │
│ 多因子得分 → Top-N → Insight    │
└─────────────────────────────────┘
            ↓ Insight[]
┌─────────────────────────────────┐
│ TopNEqualWeightPCM              │
│ (Portfolio Construction)        │
│ 等权配置 1/5 仓位               │
│ 无交易带宽过滤                  │
└─────────────────────────────────┘
            ↓ IPortfolioTarget[]
┌─────────────────────────────────┐
│ ChipPeakRiskManagementModel     │
│ (Risk Management)               │
│ 止损/止盈/仓位上限              │
│ VPIN 毒性信号扩展点             │
└─────────────────────────────────┘
            ↓ IPortfolioTarget[]
┌─────────────────────────────────┐
│ ImmediateExecutionModel         │
│ (Execution)                     │
│ 市价单立即执行                  │
└─────────────────────────────────┘
            ↓ Order[]
┌─────────────────────────────────┐
│ Brokerage                       │
│ 订单成交                        │
└─────────────────────────────────┘
```

---

## 关键设计要点

### 1. LEAN 原生架构
- 完全遵循 LEAN 五层框架接口（`IAlphaModel`, `IPortfolioConstructionModel`, `IRiskManagementModel`, `IExecutionModel`, `IUniverseSelectionModel`）
- 无自定义扩展，无修改核心代码
- 符合 `feedback_lean-native-only` 与 `never-modify-lean-native` 约定

### 2. 流式内存控制
```python
# Alpha 层按 500 只/批次加载
for ts_code, chip_row in self.loader.enriched_batch(batch_codes, current_date):
    # chip_row 是 pd.Series，用完即释放
    score = ChipPeakFactors.composite_score(chip_row, current_price, self.params)
    ...
# 循环结束后 chip_row 自动 GC，避免全量缓存
```

### 3. 多因子融合
```
基础筹码因子（concentration, profit_ratio）
    + 资金流增强（net_moneyflow_signal, 权重 0.3）
    + 估值增强（value_quality_score, 权重 0.2）
    - 换手率风险（turnover_risk, 权重 -0.3）
    = final_score
```

### 4. 缺失降级策略
当 `moneyflow` / `daily_basic` 缺失时：
- 自动降级为纯筹码因子得分
- 不中断策略运行
- 保持向后兼容

### 5. 参数可配置性
所有阈值集中定义在 `ChipPeakFactors.DEFAULT_PARAMS`：
```python
DEFAULT_PARAMS = {
    'single_concentration': 0.6,   # 单峰集中度阈值
    'low_profit_max': 0.2,         # 低位获利盘上限
    'high_profit_min': 0.8,        # 高位获利盘下限
    'mf_weight': 0.3,              # 资金流权重
    'value_weight': 0.2,           # 估值权重
    ...
}
```

---

## 文件索引

| 层级 | 文件路径 |
|---|---|
| Universe Selection | `Algorithm.Python/AShareUniverseSelectionModel.py` |
| Alpha | `Algorithm.Python/ChipPeakAlphaModel.py` |
| Alpha 因子库 | `Algorithm.Python/ChipPeakFactors.py` |
| Portfolio | `Algorithm.Python/TopNEqualWeightPCM.py` |
| Risk | `Algorithm.Python/ChipPeakRiskManagementModel.py` |
| Execution | LEAN 内置 `ImmediateExecutionModel` |
| 数据加载器 | `ToolBox/ChipDataLoader.py` |
| 主算法 | `Algorithm.Python/ChipPeakStrategyAlgorithm.py` |

---

## 相关文档

- **筹码峰因子设计**: `docs/chip-peak-cyqperf-refactor.md`
- **筹码峰调研**: `docs/chouma.md`
- **LEAN 架构**: `CLAUDE.md`
- **A 股市场扩展**: `docs/00-LEAN-Arch.md`

---

## 版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-06-30 | 初始文档，基于 `ChipPeakStrategyAlgorithm` v1.0 |

---

## 维护指南

### 修改因子逻辑
1. 编辑 `ChipPeakFactors.py`（纯函数库）
2. 运行 `Tests/Python/Scripts/ChipPeakFactorsTests.py` 验证
3. 无需修改 Alpha 模型（自动调用新因子）

### 修改风险模型
1. 编辑 `ChipPeakRiskManagementModel.py`
2. 确保继承 `RiskManagementModel`
3. 实现 `ManageRisk` 方法
4. 不修改 LEAN 核心代码

### 扩展 VPIN
1. 新建 `Indicators/VpinIndicator.cs`（计算层）
2. 新建 `Algorithm.Framework/Risk/VpinRiskManagementModel.cs`（消费层）
3. 保持与现有架构一致（独立、可插拔）

---

## 联系与支持

- **项目**: Lean A-Share Extension
- **分支**: `fix/price-scaling-10000x`
- **维护者**: yzj19870824
- **Grafana Dashboard**: `http://grafana.localhost:3000`（策略监控）