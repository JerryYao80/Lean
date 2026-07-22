# AShareOptionVolatilityArbitrageAlgorithm vs ChipPeakStrategyAlgorithm 关系对比

> 日期: 2026-07-03
> 结论: 两者是完全独立的两套策略, 没有业务关系, 只是属于同一项目、且都被改造成了因子动物园/模型动物园架构。

---

## 一、两个策略对比

| 维度 | AShareOptionVolatilityArbitrageAlgorithm | ChipPeakStrategyAlgorithm |
|---|---|---|
| **策略类型** | 期权波动率套利 | 筹码峰选股 |
| **标的** | 单一标的 510050 ETF期权 | 全A股 5539只 |
| **交易对象** | 期权合约（straddle/calendar/risk reversal） | 股票现货 |
| **核心信号** | IV-RV z-score + IV期限结构 + skew分位 | 筹码集中度 + 获利盘 + 多因子融合 |
| **数据源** | 期权链 + IV CSV | cyq_perf parquet（筹码分布） |
| **持仓周期** | 短期套利 | 周频（7天rebalance） |
| **语言** | C# | Python（AlphaModel为C#） |
| **市场范围** | 1只ETF期权 | 全市场选股 |

---

## 二、它们唯一的共同点

1. **同一项目**（Lean）中的两个独立策略
2. **都改造为因子动物园架构**（独立任务，分两次完成）：
   - ChipPeak → `ChipPeakFactorZooAlphaModel` + Chip因子（5个）
   - OptionVolArb → `OptionVolArbFactorZooAlphaModel` + 波动率因子（复用已有5个）
3. **都走三层管线**：Factor → Model → Strategy
4. **都通过 FactorRegistry 取因子**

---

## 三、数据/因子层面无交集

- ChipPeak 用 **Chip 类因子**（concentration / profit_ratio / peak_pattern / composite / cost_deviation）
- OptionVolArb 用 **Volatility 类因子**（IVPercentile / HV / IVSkew / IVTermStructure）

两套因子属于 FactorCategory 的不同分类（`Chip` vs `Volatility`），互不依赖。

---

## 四、总结

两个策略**毫无业务关联**，只是恰好在同一项目中、且都做了因子动物园化改造。

- **AShareOptionVolatilityArbitrageAlgorithm**：期权套利（单标的），交易期权合约
- **ChipPeakStrategyAlgorithm**：选股（全市场），交易股票现货

---

## 五、相关文件

### AShareOptionVolatilityArbitrageAlgorithm（期权套利）

| 文件 | 说明 |
|---|---|
| `Algorithm.CSharp/AShareOptionVolatilityArbitrageAlgorithm.cs` | 原版命令式策略 |
| `Algorithm.CSharp/OptionVolArbFactorZooStrategy.cs` | 三层管线版策略 |
| `Algorithm.CSharp/Models/Alpha/OptionVolArbFactorZooAlphaModel.cs` | 因子动物园 AlphaModel |
| `Launcher/config/config-ashare-option-vol-arb-backtest.json` | 原版回测config |
| `Launcher/config/config-option-vol-arb-factorzoo-backtest.json` | 三层管线版config |
| `docs/option-vol-arb-factorzoo.log` | 三层管线版回测日志 |

### ChipPeakStrategyAlgorithm（筹码峰选股）

| 文件 | 说明 |
|---|---|
| `Algorithm.Python/ChipPeakStrategyAlgorithm.py` | 策略主文件（使用C# AlphaModel） |
| `Algorithm.CSharp/Models/ChipPeakFactorZooAlphaModel.cs` | 因子动物园 AlphaModel |
| `Algorithm.Python/ChipPeakFactors.py` | Python因子计算库 |
| `Common/Factors/Chip/*.cs` | Chip类因子（5个） |
| `Launcher/config/config-chip-peak.json` | 回测config |
| `docs/chip-peak-final.log` | 回测日志 |
