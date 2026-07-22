# ChipPeak 持仓风控升级方案（无高频数据版）

本文档说明在无高频 Tick 数据、且不下载外部数据的前提下，如何为筹码峰策略补充持仓风控能力。

---

## 背景与约束

### 用户约束
- **无高频 Tick 数据**：无法计算 VPIN（订单流毒性指标）
- **不下载外部数据**：仅使用现有日线级数据
- **数据范围**：`cyq_perf`（筹码分布）+ LEAN 原生 `TradeBar`（OHLCV）

### VPIN 的不可行性
VPIN（Volume-Synchronized Probability of Informed Trading）需要高频 Tick 数据：
- 成交价、成交量、时间戳
- 买卖方向分类（Lee-Ready 规则）
- Volume bucket 分桶

**结论**：在当前数据约束下，VPIN **完全不可用**，放弃该方向，专注于基于日线数据的持仓风控方案。

---

## 当前风控缺失诊断

### 当前实现：仅入场风控

**文件**：`Algorithm.Python/ChipPeakRiskManagementModel.py`

```python
def manage_risk(self, algorithm, insights):
    for insight in insights:
        if pattern == HIGH_SINGLE_PEAK:
            continue  # 砍 Insight → 不买入
        filtered.append(insight)
    return filtered
```

### 核心问题

| 维度 | 现状 |
|---|---|
| **入场风险** | ✅ 派发区过滤（阻止买入） |
| **持仓风险** | ❌ 不管理已有持仓 |
| **市场风险** | ❌ 不监测系统性风险 |
| **流动性风险** | ❌ 不监测 |

### 危险场景

策略买入某股票后（低位单峰密集），持仓期间筹码峰形态可能恶化：
1. 低位单峰 → 发散（筹码松动）
2. 发散 → 高位单峰（机构派发完成）
3. **当前风控完全无反应**，任由亏损扩大

---

## 可用数据清单

| 数据项 | 是否可用 | 来源 | 频率 |
|---|---|---|---|
| **筹码分布（cyq_perf）** | ✅ | `ChipDataLoader.load_single` | 日线 |
| **日线 OHLCV** | ✅ | LEAN 原生 `TradeBar` | 日线 |
| **历史收益率** | ✅ | `algorithm.history()` | 日线 |
| **波动率** | ✅ | `security.volatility` / `Std` 指标 | 日线 |
| **成交量** | ✅ | `TradeBar.Volume` | 日线 |
| **持仓信息** | ✅ | `algorithm.portfolio` | 实时 |
| **高频 Tick 数据** | ❌ | 不下载 | - |
| **订单簿数据** | ❌ | 不下载 | - |

---

## 风控方案

### 方案 1：LEAN 内置止损/止盈（最简单）

**数据依赖**：无（仅用持仓盈亏）

**实现**：
```python
from MaximumDrawdownPercentPerSecurityRiskManagementModel import MaximumDrawdownPercentPerSecurityRiskManagementModel
from MaximumUnrealizedProfitPercentPerSecurityRiskManagementModel import MaximumUnrealizedProfitPercentPerSecurityRiskManagementModel

self.set_risk_management([
    ChipPeakRiskManagementModel(data_folder),                     # 第一层：入场风控
    MaximumDrawdownPercentPerSecurityRiskManagementModel(0.10),   # 单股止损 10%
    MaximumUnrealizedProfitPercentPerSecurityRiskManagementModel(0.20)  # 单股止盈 20%
])
```

**评估**：
- ✅ 实现成本最低（LEAN 内置）
- ✅ 无数据依赖
- ⚠️ 与筹码峰策略语义不匹配（通用止损，非形态风控）
- ⚠️ 参数固定（10% / 20%），未结合筹码峰状态

---

### 方案 2：筹码峰形态持仓风控（策略专属，推荐）

**数据依赖**：`cyq_perf`（已有）

**核心思想**：持仓期间监测筹码峰形态，恶化则调整仓位。

#### 形态 → 动作映射

| 形态 | 含义 | 动作 |
|---|---|---|
| `LOW_SINGLE_PEAK` | 建仓区（低位单峰密集） | 保持持仓 |
| `DIVERGENT` | 筹码发散（松动） | 减仓 50% |
| `HIGH_SINGLE_PEAK` | 派发区（高位单峰密集） | 平仓 100% |

#### 完整实现

```python
# Algorithm.Python/ChipPeakHoldingRiskManagementModel.py
from AlgorithmImports import *
import importlib.util
import sys
from pathlib import Path

# 加载 ChipPeakFactors
_ROOT = Path(__file__).resolve().parent.parent
_FACTORS_PATH = _ROOT / 'Algorithm.Python' / 'ChipPeakFactors.py'
_spec = importlib.util.spec_from_file_location('ChipPeakFactors', _FACTORS_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
ChipPeakFactors = _mod.ChipPeakFactors
PeakPattern = _mod.PeakPattern

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from ToolBox.ChipDataLoader import ChipDataLoader


class ChipPeakHoldingRiskManagementModel(RiskManagementModel):
    """
    筹码峰持仓风控：形态恶化触发减仓/平仓。

    逻辑：
    - LOW_SINGLE_PEAK（建仓区）→ 保持持仓
    - DIVERGENT（发散）→ 减仓 50%
    - HIGH_SINGLE_PEAK（派发区）→ 平仓 100%

    数据基础: cyq_perf（日线级别）。
    """

    def __init__(self, data_folder: str, params: dict = None):
        super().__init__()
        self.data_folder = data_folder
        self.params = params or ChipPeakFactors.DEFAULT_PARAMS.copy()
        self.loader = ChipDataLoader(data_folder, max_batch_size=50)

    def manage_risk(self, algorithm: QCAlgorithm, insights: list) -> list:
        """检查已持仓的筹码峰形态，恶化则调整目标仓位。"""
        current_date = algorithm.time.strftime('%Y%m%d')

        for symbol, holding in algorithm.portfolio.items():
            if not holding.invested:
                continue  # 未持仓跳过

            security = algorithm.securities.get(symbol)
            if security is None:
                continue

            ts_code = self._symbol_to_ts_code(symbol)
            chip_row = self.loader.load_single(ts_code, current_date)
            if chip_row is None:
                continue  # 缺数据保守保留

            pattern = ChipPeakFactors.classify_peak(chip_row, float(security.price), self.params)

            # 根据形态调整仓位
            if pattern == PeakPattern.HIGH_SINGLE_PEAK:
                # 派发区 → 平仓
                algorithm.debug(f'[ChipPeakHoldingRisk] {symbol} HIGH_SINGLE_PEAK → 平仓')
                algorithm.liquidate(symbol)

            elif pattern == PeakPattern.DIVERGENT:
                # 发散 → 减仓 50%
                current_qty = holding.quantity
                target_qty = current_qty * 0.5
                weight = target_qty * float(security.price) / algorithm.portfolio.total_portfolio_value
                algorithm.debug(f'[ChipPeakHoldingRisk] {symbol} DIVERGENT → 减仓50%')
                algorithm.set_holdings(symbol, weight)

            # LOW_SINGLE_PEAK → 保持持仓，不调整

        return insights  # 入场风控由 ChipPeakRiskManagementModel 处理

    @staticmethod
    def _symbol_to_ts_code(symbol: Symbol) -> str:
        ticker = str(symbol.value) if hasattr(symbol, 'value') else str(symbol)
        t = ticker.replace('.SH', '').replace('.SZ', '').strip()
        if t.startswith('6') or t.startswith('51'):
            return f'{t}.SH'
        return f'{t}.SZ'
```

**集成到策略**：
```python
# ChipPeakStrategyAlgorithm.py
from ChipPeakHoldingRiskManagementModel import ChipPeakHoldingRiskManagementModel

self.set_risk_management([
    ChipPeakRiskManagementModel(data_folder),            # 第一层：入场风控
    ChipPeakHoldingRiskManagementModel(data_folder),     # 第二层：持仓形态风控
])
```

**评估**：
- ✅ 与筹码峰策略语义完全匹配
- ✅ 数据已具备（`cyq_perf`）
- ✅ 策略专属，差异化风控
- ⚠️ 直接调用 `algorithm.liquidate` / `algorithm.set_holdings`，绕过 Portfolio 层

---

### 方案 3：波动率风控（LEAN 原生数据）

**数据依赖**：LEAN 原生 `TradeBar`

**核心思想**：波动率飙升时减仓（流动性恶化预警，无高频数据的替代方案）。

#### 完整实现

```python
# Algorithm.Python/VolatilityRiskManagementModel.py
from AlgorithmImports import *

class VolatilityRiskManagementModel(RiskManagementModel):
    """
    波动率风控：波动率飙升触发减仓。

    逻辑：
    - 计算过去 N 天收益率标准差
    - 波动率 > 2× 历史平均 → 减仓 50%
    - 波动率 > 3× 历史平均 → 平仓

    数据基础: TradeBar（LEAN 原生）。
    """

    def __init__(self, lookback=30, vol_mult_high=2.0, vol_mult_extreme=3.0):
        super().__init__()
        self.lookback = lookback
        self.vol_mult_high = vol_mult_high
        self.vol_mult_extreme = vol_mult_extreme

    def manage_risk(self, algorithm: QCAlgorithm, insights: list) -> list:
        for symbol, holding in algorithm.portfolio.items():
            if not holding.invested:
                continue

            # 获取历史收益率
            history = algorithm.history([symbol], self.lookback, Resolution.DAILY)
            if history.empty:
                continue

            # 计算波动率（收益率标准差）
            returns = history['close'].pct_change().dropna()
            current_vol = returns.std()

            # 计算历史平均波动率（排除最近 5 天）
            historical_vol = returns[:-5].std() if len(returns) > 5 else current_vol
            if historical_vol == 0 or math.isnan(historical_vol):
                continue

            # 判断波动率飙升
            if current_vol > self.vol_mult_extreme * historical_vol:
                algorithm.debug(f'[VolatilityRisk] {symbol} 极端波动 → 平仓')
                algorithm.liquidate(symbol)

            elif current_vol > self.vol_mult_high * historical_vol:
                current_qty = holding.quantity
                weight = (current_qty * 0.5) * float(algorithm.securities[symbol].price) / algorithm.portfolio.total_portfolio_value
                algorithm.debug(f'[VolatilityRisk] {symbol} 高波动 → 减仓50%')
                algorithm.set_holdings(symbol, weight)

        return insights
```

**集成到策略**：
```python
# ChipPeakStrategyAlgorithm.py
from VolatilityRiskManagementModel import VolatilityRiskManagementModel

self.set_risk_management([
    ChipPeakRiskManagementModel(data_folder),            # 第一层：入场风控
    ChipPeakHoldingRiskManagementModel(data_folder),     # 第二层：持仓形态风控
    VolatilityRiskManagementModel(lookback=30),          # 第三层：波动率风控
])
```

**评估**：
- ✅ 无筹码数据依赖（LEAN 原生数据）
- ✅ 覆盖筹码数据缺失场景
- ✅ 流动性恶化预警（VPIN 的日线替代）
- ⚠️ 每次调用 `algorithm.history()` 有性能开销
- ⚠️ 波动率飙升可能是趋势信号（非风险）

---

### 方案 4：成交量风控（LEAN 原生数据）

**数据依赖**：LEAN 原生 `TradeBar.Volume`

**核心思想**：成交量异常萎缩时减仓（流动性枯竭预警）。

#### 完整实现

```python
# Algorithm.Python/VolumeRiskManagementModel.py
from AlgorithmImports import *

class VolumeRiskManagementModel(RiskManagementModel):
    """
    成交量风控：成交量萎缩触发减仓。

    逻辑：
    - 成交量 < 0.3× 历史平均 → 流动性枯竭 → 减仓 50%
    - 成交量 < 0.1× 历史平均 → 极端萎缩 → 平仓

    数据基础: TradeBar.Volume（LEAN 原生）。
    """

    def __init__(self, lookback=30, vol_thresh_low=0.3, vol_thresh_extreme=0.1):
        super().__init__()
        self.lookback = lookback
        self.vol_thresh_low = vol_thresh_low
        self.vol_thresh_extreme = vol_thresh_extreme

    def manage_risk(self, algorithm: QCAlgorithm, insights: list) -> list:
        for symbol, holding in algorithm.portfolio.items():
            if not holding.invested:
                continue

            # 获取历史成交量
            history = algorithm.history([symbol], self.lookback, Resolution.DAILY)
            if history.empty:
                continue

            current_volume = history['volume'].iloc[-1]
            avg_volume = history['volume'].mean()

            if avg_volume == 0:
                continue

            # 判断成交量萎缩
            if current_volume < self.vol_thresh_extreme * avg_volume:
                algorithm.debug(f'[VolumeRisk] {symbol} 极端萎缩 → 平仓')
                algorithm.liquidate(symbol)

            elif current_volume < self.vol_thresh_low * avg_volume:
                current_qty = holding.quantity
                weight = (current_qty * 0.5) * float(algorithm.securities[symbol].price) / algorithm.portfolio.total_portfolio_value
                algorithm.debug(f'[VolumeRisk] {symbol} 流动性枯竭 → 减仓50%')
                algorithm.set_holdings(symbol, weight)

        return insights
```

**评估**：
- ✅ 无筹码数据依赖
- ✅ 流动性枯竭预警
- ⚠️ A 股成交量受涨跌停影响，需结合涨跌停状态判断

---

## 三层风控架构（推荐方案）

```
┌─────────────────────────────────┐
│ ChipPeakRiskManagementModel     │
│ (第一层：入场风控)               │
│ 派发区过滤 → 阻止买入            │
│ 数据: cyq_perf                  │
└─────────────────────────────────┘
            ↓ Insight（已过滤）
┌─────────────────────────────────┐
│ ChipPeakHoldingRiskManagementModel│
│ (第二层：持仓形态风控)           │
│ 高位单峰 → 平仓                  │
│ 发散 → 减仓 50%                  │
│ 数据: cyq_perf                  │
└─────────────────────────────────┘
            ↓ PortfolioTarget
┌─────────────────────────────────┐
│ VolatilityRiskManagementModel   │
│ (第三层：波动率风控)             │
│ VPIN 的日线替代方案              │
│ 波动率飙升 → 减仓/平仓           │
│ 数据: TradeBar（LEAN 原生）      │
└─────────────────────────────────┘
            ↓ 最终目标
┌─────────────────────────────────┐
│ ImmediateExecutionModel         │
│ 市价单执行                      │
└─────────────────────────────────┘
```

### 集成代码

```python
# ChipPeakStrategyAlgorithm.py
from ChipPeakHoldingRiskManagementModel import ChipPeakHoldingRiskManagementModel
from VolatilityRiskManagementModel import VolatilityRiskManagementModel

class ChipPeakStrategyAlgorithm(QCAlgorithm):
    def initialize(self):
        # ... 其他配置 ...

        # 三层风控架构（无高频数据）
        self.set_risk_management([
            ChipPeakRiskManagementModel(data_folder),            # 第一层：入场风控
            ChipPeakHoldingRiskManagementModel(data_folder),     # 第二层：持仓形态风控
            VolatilityRiskManagementModel(lookback=30),          # 第三层：波动率风控
        ])
```

---

## 各方案对比

| 方案 | 数据依赖 | 实现难度 | 风控语义 | 适用场景 | 推荐度 |
|---|---|---|---|---|---|
| **LEAN 内置止损** | 无 | ⭐ 最简单 | 单股回撤止损 | 通用 | ⭐⭐⭐ |
| **持仓形态风控** | cyq_perf | ⭐⭐ 中等 | 筹码峰恶化 | 筹码策略专属 | ⭐⭐⭐⭐⭐ |
| **波动率风控** | TradeBar | ⭐ 简单 | 波动率飙升 | VPIN 日线替代 | ⭐⭐⭐⭐ |
| **成交量风控** | TradeBar | ⭐ 简单 | 流动性枯竭 | 流动性预警 | ⭐⭐⭐ |
| **VPIN** | 高频 Tick | ❌ 无法实现 | 订单流毒性 | **不适用**（无数据） | ❌ |

---

## 实施顺序建议

### 第 1 步：实现持仓形态风控（最高优先级）

**理由**：筹码峰策略的专属风控，直接继承当前架构逻辑，数据已具备。

**任务**：
1. 创建 `Algorithm.Python/ChipPeakHoldingRiskManagementModel.py`
2. 集成到 `ChipPeakStrategyAlgorithm.py`
3. 回测验证（对比单一入场风控 vs 双层风控）

**验证指标**：
- 最大回撤是否降低
- 年化收益是否保持
- Sharpe 是否提升

### 第 2 步：补充波动率风控（次优先级）

**理由**：覆盖筹码数据缺失场景，VPIN 的日线替代方案。

**任务**：
1. 创建 `Algorithm.Python/VolatilityRiskManagementModel.py`
2. 集成到策略
3. 调优参数（`lookback=30`, `vol_mult_high=2.0`, `vol_mult_extreme=3.0`）

---

## 关键原则

### 1. 数据约束优先
> **用户约束**：无高频数据，不下载外部数据。
>
> VPIN 需要高频 Tick 数据，在当前数据约束下**完全不可用**。

### 2. LEAN 原生优先
> **架构约束**：所有模型必须继承 LEAN 原生接口，无侵入性修改。
>
> - ✅ 继承 `RiskManagementModel`
> - ✅ 实现 `manage_risk` 方法（snake_case）
> - ✅ 使用 LEAN 原生数据（`algorithm.portfolio`, `algorithm.history()`）
> - ❌ 不修改 `QCAlgorithm` 或 LEAN 核心代码

### 3. 渐进验证
> **工程实践**：每次升级都需要回测验证，确保三维改善（收益、风险、成本）。
>
> **停止条件**：任一维度恶化 → 停止升级，回退到上一版本

### 4. 策略专属优先
> **设计原则**：优先实现与策略语义匹配的风控（持仓形态风控），通用风控作为补充（波动率风控）。
>
> - 策略专属：筹码峰形态恶化 → 直接命中策略风险源
> - 通用补充：波动率/成交量异常 → 覆盖筹码数据缺失场景

---

## 已知局限

### 1. 直接下单绕过 Portfolio 层
方案 2/3/4 中 `algorithm.liquidate` / `algorithm.set_holdings` 绕过了 Portfolio Construction 层，直接下单。

**影响**：
- 绕过 PCM 的等权/信号加权逻辑
- 可能导致仓位超出 PCM 设定的上限

**缓解**：
- 在 PCM 层设置仓位上限（如单股 ≤ 20%）
- 风控层只做减仓/平仓，不做加仓
- 总仓位由 PCM 层控制

### 2. 波动率/成交量历史查询性能
每次 `manage_risk` 调用 `algorithm.history()` 有性能开销。

**缓解**：
- 使用 LEAN RollingWindow 缓存历史数据
- 或使用 LEAN 原生 `STD` 指标实时更新波动率

### 3. A 股特殊性
- 涨跌停限制：波动率/成交量异常可能是涨跌停导致，非真实风险
- T+1 制度：当日买入不能卖出，风控减仓需考虑 T+1

---

## 附录：数据基础确认

### cyq_perf 字段（ChipPeakFactors.py）
```python
# 已确认可用字段
- cost_5pct_adj .. cost_95pct_adj:  # 复权校正后的 5 档成本分位
- weight_avg_adj:                   # 复权校正后的加权平均成本
- winner_rate:                      # 获利盘比例 %（0-100，复权不变）
```

### LEAN 原生 TradeBar 字段
```python
# LEAN 原生日线数据
- TradeBar.Open:   # 开盘价
- TradeBar.High:   # 最高价
- TradeBar.Low:    # 最低价
- TradeBar.Close:  # 收盘价
- TradeBar.Volume: # 成交量
```

### 筹码峰形态分类（ChipPeakFactors.py）
```python
class PeakPattern(Enum):
    LOW_SINGLE_PEAK = "low_single_peak"      # 低位单峰密集（建仓区）
    HIGH_SINGLE_PEAK = "high_single_peak"    # 高位单峰密集（派发区）
    DOUBLE_PEAK = "double_peak"              # 双峰（保留枚举，暂不检测）
    DIVERGENT = "divergent"                  # 发散
```

---

## 版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-06-30 | 初始文档，无高频数据条件下的持仓风控方案 |

---

## 联系与支持

- **项目**: Lean A-Share Extension
- **分支**: `fix/price-scaling-10000x`
- **维护者**: yzj19870824
- **相关文档**:
  - `docs/ChipPeakRiskManagementModel.md`（五层架构）
  - `docs/ChipPeakRiskManagementModel-update.md`（组合优化升级）
  - `docs/ChipPeakRiskManagementModel-update2.md`（本文件，持仓风控）
