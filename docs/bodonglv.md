# A股期权相关量化策略盘点

> 日期: 2026-07-02
> 范围: Lean 项目中包含期权的量化策略

---

## 一、A股期权相关策略（6个）

| 策略文件 | 类型 | 期权用法 |
|---|---|---|
| **AShareOptionVolatilityArbitrageAlgorithm** | 期权波动率套利 | 实际交易期权合约（IV/HV 偏离套利） |
| **AShareIVExportAlgorithm** | IV 数据导出 | 计算并导出 IV/VIX CSV（非交易） |
| **AShareETFVolatilityTimingStrategy** | ETF 择时 | 读 IV CSV 信号，交易 ETF（不交易期权） |
| **AShareETFVolatilityTimingMultiAlgo** | ETF 多标的择时 | 同上，多 ETF 轮动 |
| **AShareImpliedVolatilityRotationAlgorithm** | IV 轮动 | IV 信号轮动 |
| **AShareVolatilitySkewRotationAlgorithm** | Skew 轮动 | IV 偏度轮动 |

---

## 二、真正交易期权合约的策略

**只有 1 个**：`AShareOptionVolatilityArbitrageAlgorithm.cs`

- **Config**: `config-ashare-option-vol-arb-backtest.json` / `live-paper.json` / `pipeline.json`
- **逻辑**: IV-HV 偏离套利，实际开仓期权合约
- **数据**: A股 510050/510300/510500 ETF 期权

---

## 三、其他都是"期权信号 ETF 交易"

大部分策略（5个）只**读取预计算的 IV CSV 信号**，实际交易的是 **ETF 现货**，不碰期权合约。这种设计是因为：

- 期权交易门槛高（资格/资金）
- IV 信号比方向信号更有价值
- ETF 流动性更好

---

## 四、期权数据基础设施

| 类/文件 | 功能 |
|---|---|
| `AShareImpliedVolatilityData.cs` | IV CSV 数据类 |
| `AShareImpliedVolatilitySignalModel.cs` | IV 信号模型 |
| `AShareVixIndicator` | 30天插值 VIX 指标 |
| `AShareVixHelper` | 远期价格 + model-free 方差（CBOE 白皮书） |
| `AShareOptionChainProvider` | 期权链提供者 |

**数据文件**: `Data/alternative/ashare-implied-volatility/sse/daily/`

| 文件 | 数据量 | 日期范围 |
|---|---|---|
| 510050.csv | 49天 | 2024-02-08 ~ 2024-06-18 |
| 510300.csv | 1005天 | 2020-01-02 ~ 2025-04-01 |
| 510500.csv | 343天 | 2022-09-19 ~ 2025-04-01 |

**CSV 字段**: `trade_date, atm_iv, iv_call_25delta, iv_put_25delta, skew, iv_skew_surface_minus, vix, sigma_near, sigma_next, ...`

---

## 五、与因子动物园的集成

上述 IV/VIX 数据已被因子动物园封装为因子：

| 因子动物园因子 | 数据来源 | 类型 |
|---|---|---|
| `IVPercentileFactor` (`iv_pct_252d`) | IV CSV atm_iv 列 | Precomputed |
| `HVFactor` (`hv_20d`) | ETF 价格历史 | Runtime |
| `IVHVSpreadFactor` (`iv_hv_spread`) | IV + HV 组合 | Runtime |
| `VIXFactor` (`vix`) | IV CSV vix 列 | Precomputed |
| `IVSkewFactor` (`iv_skew_252d`) | IV CSV skew 列 | Precomputed |
| `IVTermStructureFactor` (`iv_term_structure`) | IV CSV sigma_near/next | Precomputed |

---

## 六、总结

- 项目中**真正交易期权**的策略仅 `AShareOptionVolatilityArbitrageAlgorithm` 一个
- 其余 5 个是"期权信号驱动的 ETF 策略"（读 IV CSV，交易 ETF 现货）
- 1 个是数据导出工具（`AShareIVExportAlgorithm`）
- 期权数据基础设施完善（VIX 指标 + 期权链 + IV CSV）
- 已通过因子动物园统一封装为可复用因子
