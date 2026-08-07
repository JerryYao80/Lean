# 筹码峰量化策略设计方案

**日期**: 2026-06-26
**状态**: 已批准
**来源**: 基于 `docs/chouma.md`（筹码量化调研报告）的方法和建议
**约束**: 必须符合 LEAN 五层架构，使用 LEAN 原生接口

---

## 1. 目标与约束

### 1.1 目标

基于 `docs/chouma.md` 调研结论，构建筹码峰量化策略：
- **作为"防套牢过滤器"**：用筹码分布识别主力派发区，避开高位接盘
- **混合模式**：Alpha 选股（低位单峰密集）+ Risk 过滤（高位派发区）
- **覆盖全 A 股 + ETF**：~5000 只股票（非 CSI300）

### 1.2 关键约束

- ✅ LEAN 原生五层架构：每层从 LEAN 接口派生，无侵入式修改
- ✅ 内存安全：分批流式处理，峰值 < 一半系统内存
- ✅ 复权校正：cyq_chips 必须用 adj_factor 对齐复权口径（issue #1088）
- ✅ IC 验证前置：样本外 IC > 0.03 才集成

### 1.3 不碰（chouma.md 已验证无效）

- ❌ 残差资金流因子（2021 后失效）
- ❌ 龙虎榜跟单（验证负收益）
- ❌ VPIN/spoofing 检测（数据不够）

---

## 2. 数据源

### 2.1 cyq_chips 接口

`tushare_data_v2/cyq_chips/ts_code=XXXXXX.XX/data.parquet`

字段：
- `trade_date` (str, YYYYMMDD)
- `price` (float, 成本价格)
- `percent` (float, 价格占比%)

自 2018 年起，每日 18-19 点更新（由 `tushare_cyq_worker` supervisor daemon 自动下载）。

### 2.2 复权校正（关键）

cyq_chips 的 `price` 字段与 daily.close 前复权价可能偏离（GitHub issue #1088），对除权股票偏离可达数十至上百元。

**校正方法**：
```
corrected_price = price * adj_factor(对齐 trade_date)
```

`adj_factor` 来自 `tushare_data_v2/adj_factor/`，merge on trade_date。

---

## 3. LEAN 五层架构

| LEAN 层 | 原生基类 | 派生类 | 职责 |
|---------|---------|--------|------|
| Universe | `UniverseSelectionModel` | `AShareUniverseSelectionModel` | 全 A 股 + ETF 预筛选 |
| Alpha | `AlphaModel` | `ChipPeakAlphaModel` | 筹码打分 → Top-N Insight |
| Portfolio | `PortfolioConstructionModel` | `TopNEqualWeightPCM`（复用） | 周频等权 |
| Risk | `RiskManagementModel` | `ChipPeakRiskManagementModel` | 派发区过滤 Insight |
| Execution | `ExecutionModel` | `ImmediateExecutionModel`（原生） | 立即执行 |

**辅助模型（复用）**：
- `AShareStockFeeModel` / `AShareStockFillModel` / `AShareStockBuyingPowerModel` / `DelayedSettlementModel`
- `ChinaInterestRateProvider`（SHIBOR 1Y）
- ETF 用 `ImmediateSettlementModel`（T+0）

**风控语义**：Risk 层砍 Insight 而非砍持仓（LEAN 标准，阻止新建仓）。

---

## 4. 内存管理（全 A 股关键）

### 4.1 约束分析

| 维度 | CSI300 | 全 A 股 |
|------|--------|---------|
| 股票数 | 300 | ~5000 |
| 周读取量 | ~30MB | ~500MB+ |
| 策略 | 一次性可接受 | **必须分批流式** |

### 4.2 三阶段流式架构

```
Universe (轻量过滤: 剔除ST/停牌/成交额<1000万)
   ↓ ~候选股票池
Alpha (分批, 每批500, 处理完释放)
   ↓ Top-N scores
Risk (仅检查已持仓 ≤20 只, 惰性加载)
```

### 4.3 内存预算

- 单批 500 只 × ~200KB ≈ **100MB 峰值**
- adj_factor 可缓存（轻量），筹码分布不缓存（体积大）
- 用生成器 `yield (ts_code, chip_df)` 实现自动释放

---

## 5. 因子定义

### 5.1 单因子

| 因子 | 公式 | 范围 |
|------|------|------|
| 筹码集中度 | 1 - H(p)/ln(N)，p=percent 归一化（Shannon 熵） | [0,1]，大=集中 |
| 获利盘比例 | Σ(percent where price < 当前价) | [0,1] |
| 平均成本偏离度 | (现价 - 均成本)/均成本 | (-∞,+∞) |
| 筹码峰形态 | 阈值分类 | enum |

平均成本 = Σ(corrected_price × percent) / Σ(percent)

### 5.2 形态分类（阈值法）

```python
class PeakPattern(Enum):
    LOW_SINGLE_PEAK   # 低位单峰密集：集中度>0.6 & 获利盘<0.2（建仓区，最佳）
    HIGH_SINGLE_PEAK  # 高位单峰密集：集中度>0.6 & 获利盘>0.8（派发区，危险）
    DOUBLE_PEAK       # 双峰：两个峰值占比>0.3 且间距<15%
    DIVERGENT         # 发散：集中度<0.6 且无双峰
```

### 5.3 综合得分

```python
def composite_score(chip_df, current_price, params):
    pattern = classify_peak(chip_df, current_price, params)
    conc = concentration(chip_df)
    profit = profit_ratio(chip_df, current_price)
    dev = cost_deviation(chip_df, current_price)
    if pattern == LOW_SINGLE_PEAK:
        return conc * (1 - profit) * (1 + max(0, dev))  # 正分
    elif pattern == DOUBLE_PEAK:
        return 0.1 * conc  # 微弱正分
    else:
        return 0
```

### 5.4 默认参数

```python
DEFAULT_PARAMS = {
    'single_concentration': 0.6,   # chouma.md: 集中度>60%
    'low_profit_max': 0.2,         # chouma.md: 获利盘<20%
    'high_profit_min': 0.8,        # 派发区: 获利盘>80%
    'double_peak_ratio': 0.3,
    'double_peak_gap': 0.15,
}
```

---

## 6. 组件设计

### 6.1 ChipDataLoader（工具类，非 LEAN 组件）

```python
# ToolBox/ChipDataLoader.py
class ChipDataLoader:
    def __init__(self, data_folder, cyq_dir='cyq_chips', adj_dir='adj_factor'): ...
    def load_batch(self, ts_codes, trade_date):
        """生成器: yield (ts_code, chip_df with corrected_price)"""
    def load_single(self, ts_code, trade_date):
        """单只股票（Risk 层用）"""
    def _load_adj_factor(self, ts_code):
        """cached 读取 adj_factor"""
```

### 6.2 ChipPeakFactors（纯函数，无 LEAN 依赖）

```python
# Algorithm.Python/ChipPeakFactors.py
class PeakPattern(Enum): ...
class ChipPeakFactors:
    @staticmethod
    def average_cost(chip_df): ...
    @staticmethod
    def profit_ratio(chip_df, current_price): ...
    @staticmethod
    def concentration(chip_df): ...
    @staticmethod
    def cost_deviation(chip_df, current_price): ...
    @staticmethod
    def classify_peak(chip_df, current_price, params): ...
    @staticmethod
    def composite_score(chip_df, current_price, params): ...
```

### 6.3 ChipPeakAlphaModel

```python
class ChipPeakAlphaModel(AlphaModel):
    INSIGHT_HORIZON_DAYS = 7
    TOP_N = 20
    BATCH_SIZE = 500
    def update(self, algorithm, data):
        # 分批流式: load_batch → composite_score → 累积
        # Top-N → Insight.price(direction=UP, magnitude=score)
```

### 6.4 ChipPeakRiskManagementModel

```python
class ChipPeakRiskManagementModel(RiskManagementModel):
    def manage(self, algorithm, insights):
        # 对每个 insight.symbol: load_single → classify_peak
        # HIGH_SINGLE_PEAK → 砍 Insight
        # 返回过滤后列表
```

### 6.5 AShareUniverseSelectionModel

```python
class AShareUniverseSelectionModel(UniverseSelectionModel):
    MIN_AMOUNT = 10_000_000  # 成交额下限
    def create_universes(self, algorithm): ...
    def _load_all_codes(self, algorithm):
        # 硬筛选: cyq_chips 目录存在
        # 软筛选: daily 成交额、停牌标记
        # ST 筛选: name_change/basic_info
```

### 6.6 ChipPeakStrategyAlgorithm

```python
# Algorithm.Python/ChipPeakStrategyAlgorithm.py
class ChipPeakStrategyAlgorithm(QCAlgorithm):
    """筹码峰量化策略：全 A 股 + ETF，混合模式。"""

    def initialize(self):
        self.set_start_date(2022, 1, 1)   # 样本外起始
        self.set_end_date(2025, 12, 31)
        self.set_account_currency("CNY")
        self.set_cash("CNY", 1_000_000)

        data_folder = '/home/project/tushare-downloader/tushare_data_v2'

        # 五层架构
        self.set_universe_selection(
            AShareUniverseSelectionModel(data_folder, include_etf=True))
        self.set_alpha(ChipPeakAlphaModel(data_folder, top_n=20, batch_size=500))
        self.set_portfolio_construction(
            TopNEqualWeightPCM(top_n=20, rebalance=timedelta(days=7), no_trade_band=0.02))
        self.set_risk_management(ChipPeakRiskManagementModel(data_folder))
        self.set_execution(ImmediateExecutionModel())

        self.set_benchmark(lambda x: 0)
        self.set_risk_free_interest_rate_model(ChinaInterestRateProvider())
        self.set_warm_up(60, Resolution.DAILY)

    def on_end_of_algorithm(self):
        self.log(f'[ChipPeak] final portfolio value: {self.portfolio.total_portfolio_value:,.2f}')
```

---

## 7. 数据流

```
┌─────────────────────────────────────────────────────────────────┐
│                      ChipPeakStrategyAlgorithm                   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Universe Selection (AShareUniverseSelectionModel)       │  │
│  │  ├─ Load all codes from cyq_chips directory              │  │
│  │  ├─ Filter: ST / 停牌 / 成交额 < 1000万                    │  │
│  │  └─ Output: ~5000 symbols (全 A 股 + ETF)                 │  │
│  └──────────────────────────────────────────────────────────┘  │
│                          ↓ symbols (~5000)                       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Alpha (ChipPeakAlphaModel)                               │  │
│  │  ├─ Batch 1 (500 stocks)                                  │  │
│  │  │   ├─ ChipDataLoader.load_batch()                       │  │
│  │  │   ├─ Load parquet + adj_factor                          │  │
│  │  │   ├─ Correct price (复权校正)                           │  │
│  │  │   ├─ Compute composite_score()                         │  │
│  │  │   └─ Release memory                                    │  │
│  │  ├─ Batch 2...N (repeat)                                  │  │
│  │  ├─ Rank scores → Top-20                                  │  │
│  │  └─ Output: 20 UP Insights                                │  │
│  └──────────────────────────────────────────────────────────┘  │
│                          ↓ insights (20)                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Risk Management (ChipPeakRiskManagementModel)            │  │
│  │  ├─ For each insight.symbol                               │  │
│  │  ├─ Load chip distribution (single, 惰性)                 │  │
│  │  ├─ Classify peak pattern                                 │  │
│  │  ├─ Filter if HIGH_SINGLE_PEAK (派发区)                  │  │
│  │  └─ Output: filtered insights (<=20)                      │  │
│  └──────────────────────────────────────────────────────────┘  │
│                          ↓ filtered insights                    │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Portfolio (TopNEqualWeightPCM, 周频)                      │  │
│  │  ├─ Equal weight: 1/20                                    │  │
│  │  ├─ Rebalance weekly                                      │  │
│  │  └─ Output: Portfolio Targets                           │  │
│  └──────────────────────────────────────────────────────────┘  │
│                          ↓ targets                              │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Execution (ImmediateExecutionModel)                      │  │
│  │  └─ Place market orders immediately                       │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 8. IC 验证（前置门控）

### 8.1 离线脚本

```python
# ToolBox/ChipPeakICValidator.py
def run_ic_test(start_date, end_date):
    # 1. 遍历交易日，全 A 股计算复合得分（分批，复用 ChipDataLoader）
    # 2. 计算得分与未来 N 日收益的 Spearman Rank IC
    # 3. 输出 IC 均值、ICIR、分层收益
```

### 8.2 门控规则

- IC_mean ≥ 0.03 → 通过，集成到 LEAN
- IC_mean < 0.03 → 停止集成，记录"因子失效"

### 8.3 目标

chouma.md 开放问题1：样本外（2022-2025）IC > 0.03。

---

## 9. 测试方案

1. **单元测试**: `ChipPeakFactors` 各因子（合成筹码分布，验证数学正确性）
2. **IC 验证**: 离线脚本，2022-2025 样本外 IC > 0.03 门控
3. **内存测试**: 监控 Alpha 批处理峰值 < 一半系统内存
4. **回测**: 完整五层策略，Sharpe / 最大回撤 / 换手率
5. **对比基准**: vs CSI300 买入持有、vs 无筹码过滤的随机选股

---

## 10. 错误处理

| 故障 | 行为 |
|------|------|
| cyq_chips parquet 缺失 | 跳过该股票 |
| adj_factor 缺失 | 用未校正 price + 日志告警 |
| 当前价缺失（停牌） | 跳过该股票 |
| Alpha 批处理 OOM | 自动减小 BATCH_SIZE |
| IC < 0.03 | 停止集成，记录因子失效 |

---

## 10. 实现路径

```
Phase 1: 因子库 + 数据加载
├─ ChipPeakFactors.py（纯函数）
├─ ChipDataLoader.py（流式 + 复权校正）
└─ 单元测试

Phase 2: IC 验证（门控）
├─ ChipPeakICValidator.py
└─ IC > 0.03 → 继续；否则停止

Phase 3: LEAN 五层组件
├─ AShareUniverseSelectionModel.py
├─ ChipPeakAlphaModel.py
├─ ChipPeakRiskManagementModel.py
└─ ChipPeakStrategyAlgorithm.py（组装）

Phase 4: 回测验证
├─ config.json
├─ 回测 + 内存监控
└─ 绩效分析
```

---

## 11. 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `Algorithm.Python/ChipPeakFactors.py` | 新增 | 因子纯函数库 |
| `Algorithm.Python/ChipPeakAlphaModel.py` | 新增 | Alpha 层 |
| `Algorithm.Python/ChipPeakRiskManagementModel.py` | 新增 | Risk 层 |
| `Algorithm.Python/AShareUniverseSelectionModel.py` | 新增 | Universe 层 |
| `Algorithm.Python/ChipPeakStrategyAlgorithm.py` | 新增 | 主算法组装 |
| `ToolBox/ChipDataLoader.py` | 新增 | 数据加载（复权+流式） |
| `ToolBox/ChipPeakICValidator.py` | 新增 | IC 验证脚本 |
| `Launcher/config.chippeak.json` | 新增 | 回测配置 |
| `TopNEqualWeightPCM.py` | 复用 | PCM |
| `AShareStockFeeModel.py` 等 | 复用 | A 股本地化 |
