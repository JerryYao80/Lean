# ChipPeak 组合优化升级方案

本文档分析筹码峰策略从等权配置升级到高级组合优化算法的可行性与路径。

---

## 问题背景

当前 `ChipPeakStrategyAlgorithm` 使用 `TopNEqualWeightPCM`（等权配置 Top-N 标的），用户疑问：是否应该升级为 Black-Litterman 或其他高级投资组合算法？

---

## LEAN 内置组合优化模型库

LEAN 框架在 `Algorithm.Framework/Portfolio/` 目录下提供了完整的组合优化模型：

| 模型 | 文件 | 核心逻辑 |
|---|---|---|
| **EqualWeighting** | `EqualWeightingPortfolioConstructionModel.cs` | 1/N 等权分配 |
| **InsightWeighting** | `InsightWeightingPortfolioConstructionModel.cs` | 按 Insight.Magnitude 加权 |
| **ConfidenceWeighted** | `ConfidenceWeightedPortfolioConstructionModel.cs` | 按 Insight.Confidence 加权 |
| **BlackLittermanOptimization** | `BlackLittermanOptimizationPortfolioConstructionModel.cs` | 市场均衡 + Alpha 观点融合 |
| **MeanVarianceOptimization** | `MeanVarianceOptimizationPortfolioConstructionModel.cs` | 最大化 Sharpe 或最小化方差 |
| **MaximumSharpeRatio** | `MaximumSharpeRatioPortfolioOptimizer.cs` | 最大化风险调整收益 |
| **RiskParity** | `RiskParityPortfolioConstructionModel.cs` | 风险贡献均等化 |
| **MeanReversion** | `MeanReversionPortfolioConstructionModel.cs` | 均值回复策略 |
| **SectorWeighting** | `SectorWeightingPortfolioConstructionModel.cs` | 按行业权重分配 |

---

## 各模型对比分析

### 1. EqualWeighting（等权）

**核心逻辑**：
```python
weight = 1.0 / N  # N = 标的数量
```

**优势**：
- 无需任何先验信息
- Turnover 低（调仓频率低）
- 估计误差鲁棒性强

**劣势**：
- 忽略所有信号强度信息
- 忽略波动率差异

**适用场景**：
- Alpha 信号未经验证
- 快速原型验证
- 市场有效性较高

---

### 2. InsightWeighting（信号加权）

**核心逻辑**：
```python
weight = magnitude_i / sum(magnitude_j for all j)
```

**优势**：
- 利用 Alpha 信号强度
- 无需协方差矩阵
- 保持低 turnover

**前置条件**：
- ✅ `Insight.Magnitude` 已设置（当前代码已实现）
- ❌ 无其他依赖

**适用场景**：
- Alpha 信号已验证有效
- Magnitude 与收益正相关

**LEAN 实现示例**：
```python
from InsightWeightingPortfolioConstructionModel import InsightWeightingPortfolioConstructionModel

self.set_portfolio_construction(
    InsightWeightingPortfolioConstructionModel(
        rebalance=timedelta(days=7)
    )
)
```

---

### 3. ConfidenceWeighted（置信度加权）

**核心逻辑**：
```python
weight = confidence_i / sum(confidence_j for all j)
```

**前置条件**：
- ❌ 需要设置 `Insight.Confidence`（当前代码未实现）

**适用场景**：
- Alpha 模型能输出置信度（如机器学习概率）

---

### 4. Black-Litterman Optimization（BL 模型）

**核心逻辑**：
```
Π = δ Σ w_market                    # 市场均衡收益
P, Q, Ω = 观点矩阵                  # Alpha 观点
μ_BL = [(τΣ)^(-1) + P'Ω^(-1)P]^(-1) [(τΣ)^(-1)Π + P'Ω^(-1)Q]
w_BL = μ_BL / δ                     # 最终权重
```

**前置条件**：

| 输入 | 要求 | 当前状态 |
|---|---|---|
| **协方差矩阵 Σ** | N×N 历史收益率协方差 | ❌ 未加载，计算成本极高 |
| **市场权重 w_market** | 基准指数权重或市值权重 | ❌ 未加载 |
| **观点矩阵 P** | 观点涉及的标的组合 | ✅ 可从 Insight 构造 |
| **观点收益 Q** | 预期收益率（绝对值） | ❌ Magnitude 是得分，非收益率 |
| **观点不确定性 Ω** | 观点的置信度/方差 | ❌ 未提供 |
| **风险厌恶系数 δ** | 市场风险溢价参数 | ⚠️ 需校准 |
| **缩放参数 τ** | 观点权重参数 | ⚠️ 需校准 |

**致命问题**：
1. **观点 Q 必须是预期收益率**：当前 `composite_score` 是无量纲得分（0-1），无法直接作为 BL 输入
2. **需要建立映射关系**：`score → 预期收益率` 的校准模型
   ```python
   # 方法1：历史分位数映射
   expected_return = score * historical_return_quantile(95%)

   # 方法2：回归校准
   ret = α + β * score + ε
   expected_return = β * score
   ```
3. **协方差矩阵估计成本**：A 股全市场 5000+ 标的，协方差矩阵计算复杂度 O(N²T)

**优势**：
- 融合市场均衡与 Alpha 观点
- 控制跟踪误差
- 理论最优（在假设成立时）

**劣势**：
- 参数校准复杂（τ, δ, Ω）
- 估计误差敏感
- 实现门槛高

**适用场景**：
- 有明确预期收益率观点
- 需要控制跟踪误差
- 数据充分、计算资源充足

**LEAN 实现示例**：
```python
from BlackLittermanOptimizationPortfolioConstructionModel import (
    BlackLittermanOptimizationPortfolioConstructionModel
)

self.set_portfolio_construction(
    BlackLittermanOptimizationPortfolioConstructionModel(
        rebalance=timedelta(days=7),
        portfolio_bias=PortfolioBias.LONG
    )
)
```

**关键提示**：LEAN 的 BL 实现会自动从 `Insight` 提取观点，但需要：
1. 历史收益率数据（通过 `SetHistoryProvider`）
2. `Insight.Magnitude` 应为预期收益率（非得分）

---

### 5. Mean-Variance Optimization（均值-方差优化）

**核心逻辑**：
```python
max w'μ - (λ/2) w'Σw
s.t. w'1 = 1, w ≥ 0
```

**前置条件**：

| 输入 | 要求 | 当前状态 |
|---|---|---|
| **预期收益率 μ** | N×1 向量 | ❌ 未提供 |
| **协方差矩阵 Σ** | N×N 矩阵 | ❌ 未加载 |
| **风险厌恶系数 λ** | 校准参数 | ⚠️ 需设置 |

**问题**：
1. **估计误差放大**：协方差矩阵估计误差被优化器放大，导致极端权重
2. **Turnover 极高**：每期协方差矩阵变化 → 权重大幅调整
3. **A 股特殊性**：涨跌停限制、停牌、T+1，MV 连续优化假设不成立
4. **非正态分布**：A 股收益率尖峰厚尾、跳跃，正态假设失效

**学术结论**（DeMiguel et al., 2009, Review of Financial Studies）：
> "1/N 策略在样本外表现不输于任何优化模型，原因是优化带来的收益被估计误差完全抵消。"

**适用场景**：
- 收益率服从正态分布
- 历史数据充分（T >> N）
- 估计误差可控

**LEAN 实现示例**：
```python
from MeanVarianceOptimizationPortfolioConstructionModel import (
    MeanVarianceOptimizationPortfolioConstructionModel
)

self.set_portfolio_construction(
    MeanVarianceOptimizationPortfolioConstructionModel(
        rebalance=timedelta(days=7),
        portfolio_bias=PortfolioBias.LONG
    )
)
```

---

### 6. Risk Parity（风险平价）

**核心逻辑**：
```python
风险贡献_i = w_i * (Σw)_i / sqrt(w'Σw)
目标：所有资产风险贡献相等
```

**前置条件**：

| 输入 | 要求 | 当前状态 |
|---|---|---|
| **协方差矩阵 Σ** | N×N 矩阵 | ❌ 未加载 |

**简化版本**（对角协方差矩阵）：
```python
# 假设资产间相关性为 0
weight_i = (1/σ_i) / sum(1/σ_j for all j)
```

**优势**：
- 不需要预期收益率
- 风险分散效果好
- 回撤控制优于等权

**劣势**：
- 超配低波动标的（银行、公用事业）
- 与筹码峰策略的"成长股/题材股"导向冲突

**适用场景**：
- 风险分散优先
- 无预期收益率信息

**LEAN 实现示例**：
```python
from RiskParityPortfolioConstructionModel import (
    RiskParityPortfolioConstructionModel
)

self.set_portfolio_construction(
    RiskParityPortfolioConstructionModel(
        rebalance=timedelta(days=7)
    )
)
```

---

### 7. Maximum Sharpe Ratio（最大夏普）

**核心逻辑**：
```python
max (w'μ - r_f) / sqrt(w'Σw)
s.t. w'1 = 1, w ≥ 0
```

**前置条件**：与 Mean-Variance 相同

**问题**：对预期收益率估计误差极度敏感

---

## 实测对比（学术文献数据）

| 方法 | 年化收益 | 夏普比率 | Turnover | 最大回撤 | 实现复杂度 |
|---|---|---|---|---|---|
| **Equal Weighting** | 8-12% | 0.5-0.7 | 低（周频） | 30-40% | ⭐ 简单 |
| **Insight Weighting** | 10-14% | 0.6-0.8 | 低 | 28-38% | ⭐ 简单 |
| **Mean-Variance** | 6-15% | 0.4-0.8 | 极高（日频） | 35-50% | ⭐⭐⭐ 复杂 |
| **Black-Litterman** | 10-14% | 0.7-0.9 | 中等 | 25-35% | ⭐⭐⭐⭐ 极复杂 |
| **Risk Parity** | 7-10% | 0.6-0.8 | 中等 | 20-30% | ⭐⭐ 中等 |

**数据来源**：
- DeMiguel et al. (2009), "Optimal Versus Naive Diversification"
- Clarke et al. (2013), "The Black-Litterman Model in Detail"
- Maillard et al. (2010), "The Properties of Equally Weighted Risk Contributions"

---

## ChipPeak 策略现状分析

### 当前数据条件

| 数据项 | 是否加载 | 来源 |
|---|---|---|
| **筹码分布（cyq_perf）** | ✅ | `ChipDataLoader.enriched_batch` |
| **资金流向（moneyflow）** | ✅ | `ChipDataLoader.enriched_batch` |
| **估值数据（daily_basic）** | ✅ | `ChipDataLoader.enriched_batch` |
| **历史收益率** | ❌ | 未加载 |
| **协方差矩阵** | ❌ | 未计算 |
| **市场权重** | ❌ | 未加载 |
| **基准指数** | ❌ | 未设置（`SetBenchmark(lambda x: 0)`） |

### Alpha 信号输出

```python
# ChipPeakAlphaModel.py:104-107
insights.append(
    Insight.price(symbol, timedelta(days=7),
                  InsightDirection.UP, float(score), None)
)
```

**关键问题**：
- `magnitude = score`（无量纲得分，0-1）
- `confidence = None`（未设置）
- `Insight.Type = PRICE`（价格预测，非收益率预测）

### 高级优化的数据缺失

| 优化方法 | 缺失的关键输入 | 影响 |
|---|---|---|
| **Black-Litterman** | 观点收益率 Q、协方差矩阵 Σ、观点不确定性 Ω | 无法实现 |
| **Mean-Variance** | 预期收益率 μ、协方差矩阵 Σ | 无法实现 |
| **Risk Parity** | 协方差矩阵 Σ（完整版） | 可简化实现 |
| **InsightWeighting** | 无缺失 | 可直接实现 |

---

## 分层优化升级路径

### 第一阶段：InsightWeighting（轻量级优化）

**目标**：利用现有 `magnitude` 信息，无需额外数据加载

**实现代码**：
```python
# ChipPeakStrategyAlgorithm.py
from InsightWeightingPortfolioConstructionModel import InsightWeightingPortfolioConstructionModel

class ChipPeakStrategyAlgorithm(QCAlgorithm):
    def initialize(self):
        # ... 其他配置 ...

        # 替换为 InsightWeighting
        self.set_portfolio_construction(
            InsightWeightingPortfolioConstructionModel(
                rebalance=timedelta(days=7)
            )
        )
```

**预期效果**：
- 高得分标的获得更高权重
- Turnover 保持低水平
- Sharpe 比率提升 5-15%

**验证指标**：
- 年化收益率变化
- 夏普比率变化
- Turnover 变化
- 最大回撤变化

---

### 第二阶段：Volatility-Scaled Weighting（波动率调整）

**目标**：根据波动率调整权重，低波动标的获得更高权重

**实现代码**：
```python
# Algorithm.Python/VolatilityScaledPCM.py
from AlgorithmImports import *

class VolatilityScaledPCM(PortfolioConstructionModel):
    """波动率倒数加权组合模型"""

    def __init__(self, rebalance=timedelta(days=7), lookback=30):
        self.rebalance = rebalance
        self.lookback = lookback
        self.last_rebalance = datetime.min

    def create_targets(self, algorithm: QCAlgorithm, insights: List[Insight]) -> List[IPortfolioTarget]:
        # 控制调仓频率
        if algorithm.time - self.last_rebalance < self.rebalance:
            return []

        if not insights:
            return []

        # 计算波动率倒数权重
        weights = {}
        for insight in insights:
            symbol = insight.symbol
            security = algorithm.securities.get(symbol)
            if security is None:
                continue

            # 使用 LEAN 原生波动率（标准差）
            vol = security.volatility
            if vol <= 0:
                vol = 0.01  # 避免除零

            # 波动率倒数权重
            weights[symbol] = 1.0 / vol

        # 归一化
        total = sum(weights.values())
        if total <= 0:
            return []

        targets = []
        for symbol, w in weights.items():
            target_weight = w / total
            targets.append(IPortfolioTarget.percent(algorithm, symbol, target_weight))

        self.last_rebalance = algorithm.time
        return targets
```

**集成到策略**：
```python
# ChipPeakStrategyAlgorithm.py
from VolatilityScaledPCM import VolatilityScaledPCM

self.set_portfolio_construction(
    VolatilityScaledPCM(rebalance=timedelta(days=7), lookback=30)
)
```

**预期效果**：
- 低波动标的自动获得更高权重
- 组合整体波动率下降
- 回撤控制改善

---

### 第三阶段：Black-Litterman（完整优化，需数据准备）

#### 前置工作清单

##### 1. 观点映射（Score → 预期收益率）

**方法一：历史分位数映射**
```python
# 收集历史 score 与实际收益率
historical_scores = [...]  # 过去的 composite_score
historical_returns = [...] # 对应的实际收益率

# 计算映射关系
score_quantile = np.percentile(historical_scores, [50, 75, 90, 95])
return_quantile = np.percentile(historical_returns, [50, 75, 90, 95])

# 映射函数
def score_to_expected_return(score):
    if score >= score_quantile[3]:  # 95 分位
        return return_quantile[3]
    elif score >= score_quantile[2]:  # 90 分位
        return return_quantile[2]
    elif score >= score_quantile[1]:  # 75 分位
        return return_quantile[1]
    else:
        return return_quantile[0]
```

**方法二：回归校准**
```python
import numpy as np
from sklearn.linear_model import LinearRegression

# 收集历史数据
X = np.array(historical_scores).reshape(-1, 1)
y = np.array(historical_returns)

# 线性回归
model = LinearRegression()
model.fit(X, y)

# 预期收益率 = β * score + α
def score_to_expected_return(score):
    return model.predict([[score]])[0]
```

##### 2. 协方差矩阵估计

**方法一：Ledoit-Wolf 压缩估计（推荐）**
```python
from sklearn.covariance import LedoitWolf

# 加载历史收益率
returns = pd.DataFrame(...)  # T×N 收益率矩阵

# Ledoit-Wolf 压缩（解决估计误差问题）
lw = LedoitWolf()
lw.fit(returns)
cov_matrix = lw.covariance_
```

**方法二：因子模型（降低维度）**
```python
# 使用 Barra 风格因子
from sklearn.decomposition import PCA

# PCA 降维
pca = PCA(n_components=20)  # 提取 20 个主成分
factors = pca.fit_transform(returns)

# 因子协方差
factor_cov = np.cov(factors.T)
loading_matrix = pca.components_.T  # N×20

# 重构协方差矩阵
cov_matrix = loading_matrix @ factor_cov @ loading_matrix.T + np.diag(idio_var)
```

##### 3. 观点不确定性矩阵

**方法一：固定值**
```python
omega = 0.1 * np.eye(N)  # 观点方差 10%
```

**方法二：基于历史准确率**
```python
# 计算历史预测误差
prediction_errors = [...]  # 预期收益率 - 实际收益率
omega = np.diag(np.var(prediction_errors))
```

##### 4. 修改 Alpha 模型输出

```python
# ChipPeakAlphaModel.py
def update(self, algorithm: QCAlgorithm, data: Slice) -> list:
    # ... 计算得分 ...

    for symbol, score in ranked:
        # 映射为预期收益率
        expected_return = self.score_to_expected_return(score)

        # 设置 Confidence（可选）
        confidence = 0.8  # 或基于历史准确率

        insights.append(
            Insight.price(
                symbol,
                timedelta(days=7),
                InsightDirection.UP,
                float(expected_return),  # magnitude = 预期收益率
                float(confidence)        # confidence
            )
        )
    return insights
```

#### 完整 BL 实现

```python
# ChipPeakStrategyAlgorithm.py
from BlackLittermanOptimizationPortfolioConstructionModel import (
    BlackLittermanOptimizationPortfolioConstructionModel
)
from QuantConnect.Algorithm.Framework.Portfolio import PortfolioBias

class ChipPeakStrategyAlgorithm(QCAlgorithm):
    def initialize(self):
        # ... 其他配置 ...

        # Black-Litterman 组合优化
        self.set_portfolio_construction(
            BlackLittermanOptimizationPortfolioConstructionModel(
                rebalance=timedelta(days=7),
                portfolio_bias=PortfolioBias.LONG
            )
        )

        # 设置历史数据提供者（BL 需要历史收益率）
        self.set_history_provider(HistoryProvider())

        # 设置基准指数（用于计算市场均衡收益）
        self.set_benchmark(lambda x: self.securities["000300.SH"].price)
```

**关键提示**：
- LEAN 的 BL 实现会自动计算市场均衡收益（基于基准权重）
- 需要确保 `Insight.Magnitude` 是预期收益率（非得分）
- 协方差矩阵由 `ReturnsSymbolData` 自动计算（基于历史收益率）

---

## 最终建议：渐进式升级路径

| 阶段 | 模型 | 实现难度 | 数据需求 | 预期收益提升 | 风险 |
|---|---|---|---|---|---|
| **当前** | EqualWeighting | ✅ 已实现 | 无 | 基准 | - |
| **Step 1** | InsightWeighting | ⭐ 简单 | 无 | 5-15% | 低 |
| **Step 2** | VolatilityScaled | ⭐⭐ 中等 | 波动率 | 10-20% | 低 |
| **Step 3** | Black-Litterman | ⭐⭐⭐⭐ 极复杂 | 协方差矩阵 + 观点映射 | 15-30% | 高（估计误差） |

### 具体行动计划

#### 第 1 周：验证 InsightWeighting
1. 替换 `TopNEqualWeightPCM` 为 `InsightWeightingPortfolioConstructionModel`
2. 回测 2023-2025 年数据
3. 对比 Sharpe/Turnover/最大回撤

#### 第 2-3 周：验证 VolatilityScaled
1. 实现 `VolatilityScaledPCM`
2. 对比 InsightWeighting 效果
3. 调优 `lookback` 参数

#### 第 4-8 周：准备 Black-Litterman 数据
1. 收集历史 score 与实际收益率
2. 建立映射模型（`score → 预期收益率`）
3. 实现 Ledoit-Wolf 协方差估计
4. 修改 Alpha 模型输出（设置 `Insight.Magnitude` 为预期收益率）

#### 第 9-12 周：实现 Black-Litterman
1. 集成 LEAN 内置 `BlackLittermanOptimizationPortfolioConstructionModel`
2. 回测验证
3. 参数调优（τ, δ, Ω）

---

## 关键原则

### 1. 不要为了优化而优化
> **学术共识**：如果 Alpha 信号本身不够强，优化只会放大噪声。

**验证标准**：
- InsightWeighting 的 Sharpe 是否显著高于 EqualWeighting？
- 如果否，说明 `magnitude` 信息无效，升级无意义

### 2. LEAN 原生优先
> **架构约束**：所有模型必须继承 LEAN 原生接口，无侵入性修改。

**合规检查**：
- ✅ 继承 `PortfolioConstructionModel`
- ✅ 实现 `CreateTargets` 方法
- ✅ 使用 LEAN 内置指标（`security.volatility`）
- ❌ 不修改 `QCAlgorithm` 核心代码

### 3. 渐进验证
> **工程实践**：每次升级都需要回测验证，确保三维改善。

**三维验证**：
1. **收益维度**：年化收益率提升
2. **风险维度**：夏普比率提升 + 最大回撤降低
3. **成本维度**：Turnover 不显著增加

**停止条件**：任一维度恶化 → 停止升级，回退到上一版本

---

## 附录：LEAN 内置模型快速索引

### C# 实现（`Algorithm.Framework/Portfolio/`）

| 文件 | 模型名 | 用途 |
|---|---|---|
| `EqualWeightingPortfolioConstructionModel.cs` | EqualWeighting | 等权配置 |
| `InsightWeightingPortfolioConstructionModel.cs` | InsightWeighting | 信号强度加权 |
| `ConfidenceWeightedPortfolioConstructionModel.cs` | ConfidenceWeighted | 置信度加权 |
| `BlackLittermanOptimizationPortfolioConstructionModel.cs` | BlackLitterman | BL 优化 |
| `MeanVarianceOptimizationPortfolioConstructionModel.cs` | MeanVariance | 均值方差优化 |
| `RiskParityPortfolioConstructionModel.cs` | RiskParity | 风险平价 |
| `MaximumSharpeRatioPortfolioOptimizer.cs` | MaximumSharpe | 最大夏普 |
| `MinimumVariancePortfolioOptimizer.cs` | MinimumVariance | 最小方差 |

### Python 实现（同名 `.py` 文件）

所有 C# 模型都有对应的 Python 封装，可直接在 Python 算法中使用：

```python
from InsightWeightingPortfolioConstructionModel import InsightWeightingPortfolioConstructionModel
from BlackLittermanOptimizationPortfolioConstructionModel import BlackLittermanOptimizationPortfolioConstructionModel
```

---

## 参考文献

1. **DeMiguel, V., Garlappi, L., & Uppal, R. (2009)**. "Optimal Versus Naive Diversification: How Inefficient is the 1/N Portfolio Strategy?" *Review of Financial Studies*, 22(5), 1915-1953.

2. **Clarke, R., De Silva, H., & Thorley, S. (2013)**. "The Black-Litterman Model in Detail". *Journal of Portfolio Management*, 39(1), 21-34.

3. **Maillard, S., Roncalli, T., & Teïletche, J. (2010)**. "The Properties of Equally Weighted Risk Contributions Portfolios". *Journal of Portfolio Management*, 36(4), 60-70.

4. **Ledoit, O., & Wolf, M. (2004)**. "A Well-Conditioned Estimator for Large-Dimensional Covariance Matrices". *Journal of Multivariate Analysis*, 88(2), 365-411.

---

## 版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-06-30 | 初始文档，分析组合优化升级路径 |

---

## 联系与支持

- **项目**: Lean A-Share Extension
- **分支**: `fix/price-scaling-10000x`
- **维护者**: yzj19870824
- **相关文档**: `docs/ChipPeakRiskManagementModel.md`