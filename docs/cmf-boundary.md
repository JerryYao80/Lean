# 筹码峰方法边界声明

**日期：** 2026-07-07
**配套文档：** `docs/cmf-audit.md`（数据与原理审计）、`docs/cmf-audit2.md`（方法审计与改进意见）

---

## 本质局限：本项目用的是"集中度代理"，不是"真实峰检测"

本项目所谓的"筹码峰"基于 tushare `cyq_perf` 的 5 档成本分位（cost_5/15/50/85/95pct + weight_avg + winner_rate），用**带宽法**定义峰：

```
spread = (cost_95 - cost_5) / weight_avg
concentration = exp(-spread)   # [0,1]，越窄越集中
```

**这是"集中度代理"，不是真实峰检测。** 下游策略作者必须清楚这条边界。

### 数据源天花板

`cyq_perf` 只给 5 个分位点，tushare 没有提供真正的多桶直方图（`cyq_chips` 已实测为单点位标量并弃用）。这意味着：

1. **无法探测双峰/多峰分布。** 一只票如果因跳空缺口在两个价位各聚集一批筹码（常见于大幅拉升或跳水后），90% 带宽可能：
   - 因两簇靠得近而显得"窄" → 误判为单峰集中
   - 因尾部厚而显得"宽" → 误判为发散
   - **带宽宽窄和"是否单峰"并不是同一件事，只是相关。**

2. **`cost_50pct` 当"峰位"用有同样问题。** 双峰分布下中位数常常落在两峰之间的**谷底**，而不是任何一个真实的峰上。路径 B/C 用 `CostProximity = |close/cost_50 - 1|` 衡量支撑强度，在双峰票上可能给出误导性的"强支撑"。

3. **`cost_15pct` / `cost_85pct` 的偏度因子（本次新增）也是离散代理。** `cost_skewness = (cost_85-cost_50) - (cost_50-cost_15)` 在 5 档分位下是粗粒度的方向性提示，非完整分布的真实三阶矩；双峰分布下同样可能失真。

### 不要把它当成真峰位来用

- ❌ 不要假设 `cost_50pct` 是"最密集的那个价位"——它只是中位成本
- ❌ 不要假设 `concentration >= 0.6` 等价于"存在单一筹码峰"——它只意味着 90% 成本带宽较窄
- ✅ 可以把它当作"筹码集中度/支撑压力代理"——这是它的真实语义
- ✅ 在策略文档/参数命名里用"集中度代理"而非"筹码峰"更准确

### 要做到真正的峰检测

需要更细粒度的成本分布数据（逐笔成交 + 换手率重建 CYQ，这是传统同花顺/通达信筹码分布的做法）。**当前 tushare 中没有这样的数据，不要寻找其他数据源**（项目原则：不引入新数据源）。

是否值得做取决于当前带宽代理在实盘/回测里的边际信息量——如果 auto_optimize 的 Bayesian optimization 已能验证这几个筹码因子的样本外 Sharpe 贡献，可能不值得为"更真实的峰"重新建管线。

---

## 本次（cmf-audit2）已落地的改进

| # | 改进 | 文件 | 状态 |
|---|---|---|---|
| 1 | VaRFactor chip_concentration 死线修复 | `Common/Factors/Risk/VaRFactor.cs` | ✅ 真正 Compute() 并参与 Outlier 判定 |
| 2 | MC 因子暴露列错位 | `AShareDataDrivenMultiFamilyAlgorithm.cs:2753` | ✅ 加注释说明槽位语义（数学无错，名字误导） |
| 3 | 风险门 fail-closed | `Algorithm.Python/ChipPeakRiskManagementModel.py` | ✅ 默认 `fail_closed=True` |
| 4 | 阈值横截面标准化（可选） | `Algorithm.Python/ChipPeakFactors.py:classify_peak` | ✅ 支持 `conc_mean/conc_std/conc_zscore_threshold` |
| 5 | 偏度因子（cost_15/85pct） | `Algorithm.Python/ChipPeakFactors.py:cost_skewness` | ✅ 新增 + 接入 composite_score |
| 6 | 复权口径自动校验 | `Algorithm.Python/ChipPeakFactors.py:cost_deviation` | ✅ 量级差异 >5x 返回 0 |
| 7 | 文档标注带宽法边界 | 本文档 | ✅ |

**测试：** `Tests/Python/Scripts/ChipPeakFactorsTests.py` 24 测试全过。

---

## 未落地（待办）

- **MC 槽位语义重命名**：`StrategyMonteCarloFactorExposure` 的 10 个槽位（Beta/Momentum/.../NonLinearSize）继承自 Barra MC schema，名字与实际填入的家族不对应。本次只加注释说明，未重命名——因为重命名会牵动 `StrategyMonteCarloStatistics.ToVector()` 和外部消费。如需重命名，需单独评估影响面。
- **walk-forward 阈值校准**：`single_concentration=0.6` 等绝对阈值的样本外校准未做，本次只提供了横截面 z-score 的可选路径（`conc_zscore_threshold`），调用方需自行算全市场 conc_mean/conc_std 传入。
- **`cost_15/85pct` 偏度因子的 IC 验证**：新增了因子，但未跑 IC/IR 验证其样本外贡献。建议用 `ToolBox/ChipPeakICValidator.py` 跑一次。
- **真实峰检测管线**：受数据源限制，不在本次范围。

---

## 来源文件

- `docs/cmf-audit.md`（数据与原理审计）
- `docs/cmf-audit2.md`（方法审计与改进意见）
- `Algorithm.Python/ChipPeakFactors.py`（concentration / cost_skewness / cost_deviation / classify_peak / composite_score）
- `Algorithm.Python/ChipPeakRiskManagementModel.py`（fail_closed）
- `Common/Factors/Risk/VaRFactor.cs`（chip_concentration 死线修复）
- `Algorithm.CSharp/AShareDataDrivenMultiFamilyAlgorithm.cs`（MC 槽位注释）
- `Tests/Python/Scripts/ChipPeakFactorsTests.py`（24 测试全过）
