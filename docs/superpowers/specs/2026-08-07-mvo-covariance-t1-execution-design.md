# P2-B: 真 MVO + 协方差 + T1 顺序执行 设计

> **日期**: 2026-08-07
> **子项目**: P2-B (3351 框架 P2 五层架构缺口)
> **前置**: P2-A (IC/IR 报告回灌 C# 运行时) 已完成
> **状态**: 设计已确认，待写实现计划

## 1. 背景与动机

### 1.1 现状问题

当前 L3 `AlphaWeightedMVOPortfolioConstructionModel` **名不副实**——它只是把 alpha 分数归一化成权重 + 10% 上限 + 重分配，没有协方差 Σ、没有 μ'w − λ/2·w'Σw 目标函数、没有风险预算。这不是 MVO。

同时，端到端回测暴露 **45,560 次 "Insufficient buying power" 拒单**（vs 45,823 成交）。根因：
1. `CreateTargets` 一次返回 30 个 `PortfolioTarget.Percent` 目标，权重和≈1.0（部署 100% 资金）
2. A 股 T+1 结算 + 现有持仓占用资金 → 可用现金 < 买单总和 → `AShareStockBuyingPowerModel` (1x 杠杆) 真实判定现金不足 → 拒单
3. `AShareLotSizeExecutionModel` 一次性提交所有目标，不区分买卖顺序、不逐笔

### 1.2 两个独立子问题

- **真 MVO + 协方差** → 改善权重**质量**（风险调整后收益），但权重和仍是 1.0，单独解决不了拒单
- **45K 拒单** → 是**执行顺序/结算**问题（L5），需"先卖后买 + 逐笔"

P2-B 一并解决两者。

### 1.3 与 P2-C 的关系

P2-B 的协方差来源用**历史收益率样本协方差**，不依赖 Barra 因子数据，与 P2-C (Barra 因子风险分解) **解耦**。优化器通过 `ICovarianceProvider` 接口隔离 Σ 来源，P2-C 可注入 Barra 结构化协方差，不影响 P2-B 独立验证。

## 2. 设计目标

1. **真 MVO**：用 μ'w − λ/2·w'Σw 目标函数 + 约束（Σw=1, 0≤w≤max）求解权重，替换 alpha-weighting
2. **协方差**：120 日后复权收益率样本协方差 + Ledoit-Wolf 收缩
3. **优化器与策略解耦**：MVO 产物（JSON 权重）通用，任何策略可读；Σ 来源可替换
4. **拒单治理**：L5 先卖后买 + 逐笔提交，显著降低 "Insufficient buying power" 拒单
5. **零侵入**：不动 `AlphaWeightedMVOPortfolioConstructionModel`、`AShareLotSizeExecutionModel`、V2 策略等成熟功能
6. **热路径无 pythonnet**：C# runtime 只读 JSON，scipy 求解在 offline
7. **无前视**：MVO 权重日只用该日之前的数据（expanding-window，与 P2-A 一致）
8. **LEAN-native**：权重→`PortfolioTarget.Percent`→`MarketOrder`，不自算成交

## 3. 架构

```
┌─ Offline (Python, 月度) ─────────────────────────────┐
│ export_mvo_weights.py                                 │
│   IC报告(alpha) + 历史收益率(Σ) → scipy SLSQP MVO     │
│   → result/mvo-weights/mvo_weights_YYYY-MM-DD.json    │
│   (expanding-window: end=month_end-1, 无前视)         │
└───────────────────────────────────────────────────────┘
                        ↓ JSON
┌─ Runtime (C#, 月度再平衡) ────────────────────────────┐
│ MVOAlphaPortfolioConstructionModel (新 L3)            │
│   读 mvo_weights_*.json → PortfolioTarget.Percent     │
│ AShareT1SequentialExecutionModel (新 L5)              │
│   先卖后买 + 按 margin impact 逐笔 + 整手             │
└───────────────────────────────────────────────────────┘
```

### 3.1 数据流

1. **Offline**（月度，对每个再平衡日 t）：
   - 读 t 时刻最新 IC 报告 → 取 alpha 分数（48 因子加权合成）作为 μ 源
   - 取 t 向前 120 日后复权日线收益率 → 样本协方差 Σ + Ledoit-Wolf 收缩
   - alpha rank 百分位线性映射 → μ（top→+高, bottom→+低/负）
   - scipy SLSQP 解 `min -μ'w + λ/2·w'Σw`，约束 `Σw=1, 0≤w≤0.10`
   - 输出 `mvo_weights_{t}.json`

2. **Runtime**（C# 月度再平衡）：
   - `MVOAlphaPortfolioConstructionModel.CreateTargets` 读 JSON → `PortfolioTarget.Percent(symbol, weight)`
   - `AShareT1SequentialExecutionModel.Execute` 先卖后买逐笔提交

## 4. 组件设计

### 4.1 `Scripts/factor_zoo/export_mvo_weights.py`（新建）

**职责**: offline 月度 MVO 权重生成器

**输入**:
- IC 报告目录（`result/ic-reports/`，P2-A 产物）
- 后复权日线数据（`tushare_data_v2/daily/` + `adj_factor/`）
- 再平衡日列表（月末交易日）

**核心逻辑**:
```python
class MVOWeightExporter:
    def __init__(self, ic_report_dir, daily_data_dir, adj_factor_dir,
                 cov_window=120, max_weight=0.10, risk_aversion=1.0):
        ...

    def export_for_dates(self, rebalance_dates: list[str], output_dir: Path):
        for dt in rebalance_dates:
            weights = self._compute_weights_for_date(dt)
            self._write_json(dt, weights, output_dir)

    def _compute_weights_for_date(self, as_of: str) -> dict:
        # 1. 读最新 IC 报告 (≤ as_of), 取 alpha 分数
        alpha_scores = self._load_alpha_scores(as_of)
        # 2. 取 as_of 前 120 日后复权收益率
        returns = self._load_historical_returns(as_of, self.cov_window)
        # 3. 对齐 symbols (alpha ∩ returns)
        # 4. μ = rank 百分位线性映射
        mu = self._alpha_to_mu(alpha_scores)
        # 5. Σ = 样本协方差 + Ledoit-Wolf 收缩
        sigma = self._estimate_covariance(returns)
        # 6. scipy SLSQP 求解
        weights = self._solve_mvo(mu, sigma)
        return weights

    def _load_alpha_scores(self, as_of) -> pd.Series:
        """读 ≤ as_of 的最新 IC 报告, 取 factor_weight × factor_value 合成 alpha"""

    def _load_historical_returns(self, as_of, window) -> pd.DataFrame:
        """as_of 前 window 日后复权日收益率 (dates x symbols)"""

    def _alpha_to_mu(self, alpha) -> np.ndarray:
        """rank pct 线性映射: (pct - 0.5) * 0.40 → ±20% 年化区间"""

    def _estimate_covariance(self, returns) -> np.ndarray:
        """样本协方差 + Ledoit-Wolf 收缩 + 1e-6 ridge"""

    def _solve_mvo(self, mu, sigma) -> dict:
        """scipy SLSQP: min -μ'w + λ/2 w'Σw, s.t. Σw=1, 0≤w≤max_weight"""
```

**expanding-window 无前视约束**:
- `end_date = month_end_dt - timedelta(days=1)`（与 P2-A 一致）
- IC 报告选 `≤ as_of` 的最新一份
- 历史收益率窗口 `[as_of - cov_window, as_of]`，不含 as_of 当日

**JSON schema** (`mvo_weights_YYYY-MM-DD.json`):
```json
{
  "as_of": "2024-01-31",
  "symbols": [
    {"ts_code": "600519.SH", "weight": 0.0987},
    {"ts_code": "000858.SZ", "weight": 0.0854}
  ],
  "sum": 1.0,
  "lambda": 1.0,
  "cov_window": 120,
  "n_assets": 30,
  "ic_report_used": "ic_report_2024-01-31.json"
}
```

**降级策略**:
- IC 报告缺失 → 跳过该日，记 warning
- 某 symbol 历史收益率不足 120 日 → 用可用天数（≥60 才纳入），不足 60 剔除
- 协方差非正定 → 加大 ridge
- SLSQP 不收敛 → 回退等权（在 JSON 标记 `"fallback": "equal_weight"`）

### 4.2 `Algorithm.CSharp/Models/Portfolio/MVOAlphaPortfolioConstructionModel.cs`（新建）

**职责**: 读 MVO JSON 权重 → PortfolioTarget

**关键设计**:
- 镜像 `ICWeightedAlphaModelV2` 的 JSON 加载模式（`LoadLatestWeights(asOf)` 选 ≤ asOf 最新文件）
- `CreateTargets` 不再从 insight.Magnitude 算权重——权重来自 JSON；insight 只提供 symbol + direction
- 对每个 insight symbol 查 JSON 权重表 → `PortfolioTarget.Percent(algorithm, symbol, weight)`

```csharp
public class MVOAlphaPortfolioConstructionModel : PortfolioConstructionModel
{
    private readonly string _mvoWeightsDir;
    private readonly decimal _minWeight;
    private readonly Resolution _rebalanceResolution;
    private DateTime? _lastRebalanceTime;
    private Dictionary<string, decimal> _weightsCache;
    private DateTime _weightsAsOf;

    public MVOAlphaPortfolioConstructionModel(
        string mvoWeightsDir,
        decimal minWeight = 0.0m,
        Resolution rebalanceResolution = Resolution.Daily) { ... }

    public override List<PortfolioTarget> CreateTargets(QCAlgorithm algorithm, Insight[] insights)
    {
        // 1. ShouldRebalance 检查
        // 2. LoadLatestWeights(algorithm.Time) → _weightsCache
        // 3. 对每个 insight.Symbol: weight = _weightsCache[symbol]; PortfolioTarget.Percent
    }

    private void LoadLatestWeights(DateTime asOf) { ... }  // 选 ≤ asOf 最新 JSON
}
```

**与 P2-A 的一致性**: JSON 加载、`≤ asOf` 选择、fallback 到最早文件——完全复用 `ICWeightedAlphaModelV2` 的模式。

### 4.3 `Algorithm.CSharp/Models/Execution/AShareT1SequentialExecutionModel.cs`（新建）

**职责**: 先卖后买 + 逐笔 + 整手，治理 T+1 现金不足拒单

**关键设计**:
- 继承 `ExecutionModel`，**不动** `AShareLotSizeExecutionModel`
- 两阶段处理 `PortfolioTargetCollection`:
  1. **先卖**: 所有 `quantity < 0` 的目标，按 margin impact 排序，逐笔提交
  2. **后买**: 所有 `quantity > 0` 的目标，按 margin impact 排序，逐笔提交；买不起（`AboveMinimumOrderMarginPortfolioPercentage` false）的跳过，不报错
- 整手化复用 `AShareStock.LotSize`（100 股）

```csharp
public class AShareT1SequentialExecutionModel : ExecutionModel
{
    private const int LotSize = 100;
    private readonly PortfolioTargetCollection _targetsCollection = new();

    public override void Execute(QCAlgorithm algorithm, IPortfolioTarget[] targets)
    {
        _targetsCollection.AddRange(targets);
        if (_targetsCollection.IsEmpty) return;

        // 阶段1: 先卖 (释放 T+1 资金)
        ExecuteSells(algorithm);
        // 阶段2: 后买 (利用释放的资金逐笔)
        ExecuteBuys(algorithm);

        _targetsCollection.ClearFulfilled(algorithm);
    }

    private void ExecuteSells(QCAlgorithm algorithm)
    {
        foreach (var target in _targetsCollection.OrderByMarginImpact(algorithm))
        {
            var qty = OrderSizing.GetUnorderedQuantity(algorithm, target, ...);
            if (qty >= 0) continue;  // 只处理卖出
            var rounded = (int)(qty / LotSize) * LotSize;
            if (rounded == 0) continue;
            algorithm.MarketOrder(security, rounded, Asynchronous, target.Tag);
        }
    }

    private void ExecuteBuys(QCAlgorithm algorithm)
    {
        foreach (var target in _targetsCollection.OrderByMarginImpact(algorithm))
        {
            var qty = OrderSizing.GetUnorderedQuantity(algorithm, target, ...);
            if (qty <= 0) continue;  // 只处理买入
            var rounded = (int)(qty / LotSize) * LotSize;
            if (rounded == 0) continue;
            // 逐笔检查 buying power, 不足则跳过 (不报错)
            if (security.BuyingPowerModel.AboveMinimumOrderMarginPortfolioPercentage(...))
                algorithm.MarketOrder(security, rounded, Asynchronous, target.Tag);
        }
    }
}
```

**为什么有效**: 卖出先成交释放 T+1 占用资金 → 后续买单可用现金增加 → 拒单减少。逐笔提交让每笔买入都能反映前一笔卖出释放的资金（LEAN 回测同步成交）。

### 4.4 `Algorithm.CSharp/AShareCSI300MVOStrategy.cs`（新建）

镜像 `AShareCSI300EnhancedV2Strategy`，差异:
- L3: `MVOAlphaPortfolioConstructionModel`（替换 `AlphaWeightedMVOPortfolioConstructionModel`）
- L5: `AShareT1SequentialExecutionModel`（替换 `AShareLotSizeExecutionModel`）
- 加 `mvoWeightsDir` 参数

### 4.5 `Launcher/config_mvo.json`（新建）

指向 `AShareCSI300MVOStrategy` + `mvoWeightsDir` 参数。

## 5. 关键约束与原则

| 约束 | 实现 |
|---|---|
| 优化器与策略解耦 | MVO JSON 通用产物；`ICovarianceProvider` 接口隔离 Σ 来源 |
| 零侵入 | 不动 `AlphaWeightedMVOPortfolioConstructionModel`、`AShareLotSizeExecutionModel`、V2 策略 |
| 无前视 | `end_date = month_end - 1`；IC 报告 `≤ as_of`；收益率窗口不含 as_of 当日 |
| 热路径无 pythonnet | C# 只读 JSON，scipy 在 offline |
| LEAN-native | `PortfolioTarget.Percent` → `MarketOrder`，不自算成交 |
| 不绕开硬阻塞 | 拒单根因（T+1 顺序）正面解决，不用 fallback 掩盖 |

## 6. 验证标准

### 6.1 Offline (pytest, ≥8)
- expanding-window 无前视（end_date < rebalance_date）
- 权重和 = 1.0（±1e-6）
- 单股权重 ≤ max_weight
- Σ 正定
- 缺数据降级（IC 报告缺失跳过、收益率不足剔除）
- SLSQP 不收敛回退等权
- JSON schema 完整
- 与 P2-A IC 报告读取一致

### 6.2 Runtime (NUnit, ≥5)
- JSON 加载（≤ asOf 选最新、fallback 最早）
- 权重→PortfolioTarget.Percent 映射
- 缺权重 symbol 跳过
- T1 先卖后买顺序
- 整手化

### 6.3 端到端回测
- 成交率显著上升：拒单 < 5000（基线 45,560）
- portfolio value 不低于 V2 基线（1,450,705 CNY）
- 无回归（V2 策略仍 0 订单 bug 已修，不受影响）

## 7. 不做（YAGNI）

- Barra 结构化协方差（留 P2-C，通过接口预留）
- 多策略 MVO 注册表（YAGNI，当前只有 CSI300）
- 在线 pythonnet scipy（破坏热路径原则）
- 修改 `AShareLotSizeExecutionModel`（成熟功能不动）
- turnover 约束（暂不需要，留接口）
