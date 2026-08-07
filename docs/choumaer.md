# 筹码峰策略实现进度评估

> **日期**: 2026-06-29
> **数据基础**: cyq_perf（5档成本分位），已从 cyq_chips 单点位重构
> **分支**: fix/price-scaling-10000x

---

## 一、进度：按 spec 的四阶段划分

| 阶段 | 状态 | 证据 |
|------|------|------|
| **Phase 1：因子库 + 数据加载** | ✅ 完成（含关键修正） | 已从错误的 `cyq_chips`（单点位不可用）重构到 `cyq_perf`（5档成本分位）。24个单元测试全通过（1.27s） |
| **Phase 2：IC 验证门控** | ⚠️ 骨架未闭环 | `ChipPeakICValidator.py` 存在，但**没有 IC>0.03 的实证记录**——spec 要求"IC通过才集成"的前置门控实际上被跳过了 |
| **Phase 3：LEAN 五层组件** | ✅ 已集成 | `ChipPeakStrategyAlgorithm` 组装了 Universe→Alpha→Portfolio→Risk→Execution 全链路，9个 feat/fix commit |
| **Phase 4：回测验证** | ❌ 跑过一次但**崩溃** | 见下 |

---

## 二、缺项（关键问题）

### A. 回测超时崩溃 —— 最严重的阻塞问题

```
RuntimeError: Algorithm took longer than 20 minutes on a single time loop.
CurrentTimeStepElapsed: 26.1 minutes (Isolator.cs:line 179)
```

回测只跑到 **2023-05-22**（2022-01 至 2025-12 的 1/3）就因单步超 20 分钟被 LEAN 强杀。

### B. 策略绩效很差（基于那次不完整回测）

| 指标 | 数值 | 说明 |
|------|------|------|
| Sharpe Ratio | **-0.732** | 负 Sharpe，风险调整后收益劣于无风险利率 |
| 年化收益 | **-13.6%** | 亏损策略 |
| 最大回撤 | **27%** | 高风险 |
| 胜率 | 46% | 略低于 50% |
| 期望值 | -0.116 | 每笔交易平均亏损 |
| 总费用 | ¥36,832 | 吃掉收益，换手过度 |
| Insight 数 | 11,420 | 过度频繁 |
| 订单数 | 2,384 | 换手率 14.57% |

### C. 未提交的增强因子悬在工作区（6个文件 +492 行未 commit）

| 文件 | 新增行数 | 内容 |
|------|---------|------|
| `ChipPeakFactors.py` | +147 | `net_moneyflow_signal` / `value_quality_score` / `turnover_risk` 三组增强因子 |
| `ChipDataLoader.py` | +204 | 并行加载 `max_workers=2` |
| `ChipPeakAlphaModel.py` | +16 | 集成增强因子 |
| `ChipPeakStrategyAlgorithm.py` | +4 | 参数调整 |
| `ChipPeakFactorsTests.py` | +122 | 增强因子测试（已通过） |
| `ChipPeakICValidator.py` | +18 | IC 验证脚本调整 |

这些改动**未验证、未回测**。

### D. 形态识别不全

`DOUBLE_PEAK` 枚举保留但注释标注"暂不检测"。

---

## 三、是否做成了量化策略？—— ✅ 是

完整的 LEAN 原生五层策略已搭好并能编译运行：

| LEAN 层 | 组件 | 状态 |
|---------|------|------|
| Universe | `AShareUniverseSelectionModel` | 全A+ETF过滤 |
| Alpha | `ChipPeakAlphaModel` | 筹码打分→Top-20 Insight |
| Portfolio | `TopNEqualWeightPCM` | 周频等权 |
| Risk | `ChipPeakRiskManagementModel` | 派发区过滤 |
| Execution | `ImmediateExecutionModel` | 立即执行 |

数据基础 `cyq_perf` 已正确接入，因子数学（带宽集中度法）逻辑健全且有测试覆盖。

---

## 四、能否回测？—— ⚠️ 能启动但**跑不完**

能配置 `config-chip-peak.json` 启动回测，但**全A股 ~5000只在单个日bar内做筹码打分**导致单时间循环超 20 分钟，被 LEAN 的 Isolator 强制终止。

本质是**性能问题**，不是逻辑错误。

---

## 五、回测输出文件清单

| 文件 | 类型 | 内容 | 当前大小 |
|------|------|------|---------|
| `Results/ChipPeakStrategyAlgorithm.json` | 最终 | 完整回测数据（订单、权益曲线、统计） | **1004KB** |
| `Results/ChipPeakStrategyAlgorithm-summary.json` | 最终 | 统计摘要（Sharpe、回撤、费用） | 8.7KB |
| `Results/ChipPeakStrategyAlgorithm-order-events.json` | 中间 | 订单事件流 | 44KB |
| `Results/ChipPeakStrategyAlgorithm-log.txt` | 中间 | 运行日志 | 201KB |
| `Results/ChipPeakStrategyAlgorithm/alpha-results.json` | **关键中间** | Alpha 模型每步的 Insight 输出 | **6.7MB** |

### alpha-results.json —— 筹码打分的核心中间结果

这是 `ChipPeakAlphaModel` 每个时间步生成的 Insight 记录：
- 每条 Insight 包含：`symbol`、`score`（筹码综合得分）、`direction`、`magnitude`、`generated_time`
- **6.7MB** 说明 Insight 数量很大（summary 显示 11,420 条）
- 可用于事后分析：哪些股票被打高分、得分分布、选股逻辑回溯

### InfluxDB 实时写入

config 中 `influxdb-enabled: true`，回测期间会向 InfluxDB 写入：
- `equity` 曲线（每分钟/每日）
- `portfolio_value`、`cash`
- 可在 Grafana 实时监控回测进度

### 日志位置

`ChipPeakStrategyAlgorithm-log.txt` 包含：
- `[ChipPeak]` 标签的自定义日志（如 `final portfolio value`）
- LEAN 框架日志（WarmUp、订单执行、错误堆栈）
- **当前 201KB**，那次超时崩溃前的完整记录

---

## 六、诊断结论

筹码峰已经是一个**架构完整、因子有测试、能编译能启动**的量化策略。但它卡在两个真实瓶颈上：

1. **性能瓶颈**（回测跑不完）—— Alpha 层对 5000 只股票逐日批量加载 parquet + 打分，单步超时
2. **绩效瓶颈**（即便跑完也是负 Sharpe）—— IC 门控未通过就集成了，因子选股能力存疑

---

## 七、后续方向选项

| 方向 | 说明 | 优先级建议 |
|------|------|-----------|
| **优先解决性能** | 缓存/预计算/缩小 universe/降频，让回测能跑完 | 🔴 高（阻塞验证） |
| **优先验证因子有效性** | 补全 IC 验证，确认筹码因子样本外是否有选股力 | 🔴 高（避免白费算力） |
| **提交/清理工作区** | 那 492 行未提交的增强因子需要先定夺（commit 还是回退） | 🟡 中（代码卫生） |

---

## 八、执行命令

```bash
/usr/local/dotnet/dotnet QuantConnect.Lean.Launcher.dll \
  --config /home/project/hope/Lean/Launcher/config/config-chip-peak.json
```

---

## 附录：相关文档

- `docs/chouma.md` — 筹码量化调研报告（原始研究）
- `docs/chip-peak-cyqperf-refactor.md` — cyq_chips → cyq_perf 重构说明
- `docs/superpowers/specs/2026-06-26-chip-peak-strategy-design.md` — 策略设计 spec
- `docs/superpowers/plans/2026-06-26-chip-peak-strategy.md` — 实现计划
- `docs/advance-factor.md` — 前瞻性量化指标 deep-research 报告（包含筹码因子验证结果）

---

**文档生成日期**: 2026-06-29