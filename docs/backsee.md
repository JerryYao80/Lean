# 3351 架构差距核对报告

> 生成日期：2026-08-05
> 核对框架：**3351** — 三个立足 · 三层管线 · 五层架构 · 一个环路
>
> - **三个立足**：A 股市场标的 · tushare 真实数据 · LEAN 系统架构
> - **三层管线**：Factor Zoo → Model Zoo → Strategy
> - **五层架构**：Universe → Alpha → Portfolio → Risk → Execution
> - **一个环路**：构建 → 优化 → 回测 → 复盘(LLM) → 重构(LLM/拣选)

核对结论一句话：**三个立足基本立住；三层管线经 2026-08-06 决策调整为 "Factor Zoo（数据+研究）→ Strategy（五层 LEAN 架构）"，Model Zoo 降级为 Strategy 内组件库（非独立层）；五层架构只有 5 个策略真正走通且 L1/L4/L5 本地化有缺口；一个环路在 Gold2 闭环验证 worktree 里已机械闭合，但未并入主分支，生产 SoloQuant 仍是开环。**

---

## 一、三个立足 核对

### 立足 1：A 股市场标的 — ✅ 基本立住
- A 股本地化已下沉到 `Common/`：`AShareStock`（涨跌停分档：主板10%/创业板科创板20%/北交所30%/ST 5%）、`AShareStockFillModel`、`AShareStockFeeModel`（佣金万3+印花税千0.5+过户费万0.1）、`AShareStockBuyingPowerModel`（1倍杠杆、T+1 卖出可用）、`DelayedSettlementModel`（T+1 09:00）、`AShareLotSizeExecutionModel`（100 股整手）。
- **缺口**：
  - ⚠️ **market-hours-database.json 的 SSE/SZSE 条目被设成 24/7 且无节假日**（`Data/market-hours/market-hours-database.json:122785/122841`）。`AShareStockFillModel.IsWithinMarketHours` 用 `security.Price > 0` 绕过了时段检查——回测能跑，**实盘会按错误时段成交，属潜在生产 Bug**。
  - ST 状态仅靠股票代码前缀规则（`*ST/ST/S*ST/SST`），无动态 ST 名单/停牌（停牌）检测加载。
  - `AShareStockSecurityInitializer` 在 Enhanced / Alpha101Composite / CrowdingFactorZoo 三个策略里**复制粘贴**，无单一来源；BarraV4 又是另一套内联写法。

### 立足 2：tushare 真实数据 — ✅ 立住
- tushare 数据根 `/home/project/tushare-downloader/tushare_data_v2`，`tushare_worker`（supervisor `autorestart`）定时增量下载。
- factor_worker 增量守护进程 RUNNING（pid 742274，uptime 2 天），freshness.json 覆盖 16 个因子族，多数 lag ≤ 3 天。
- **缺口**：
  - ⚠️ **latest_date_resolver 与磁盘布局不匹配**：resolver 扫描 `yyyy-MM-dd` **子目录**，但 alpha101/technical/Phase-5 实际是**扁平 `<date>.parquet`** 文件。resolver 返回 None → daemon 当作"从未构建"每日重算（靠 `factor_worker_state.json` 的 `last_built_date` 兜底）。功能没断但效率与可观测性受损。
  - 回填未完成：`accruals_sloan`（last 20100202）、`ivol_20d`（last 20100119）仍在从 2010 回填；technical/roe_change 进程已死需重启。

### 立足 3：LEAN 系统架构 — ✅ 立住
- 严格 LEAN-native：回测走 `dotnet QuantConnect.Lean.Launcher.dll --config`，指标由 `Engine/Results/InfluxDbResultExporter.cs` 写 InfluxDB，禁止自计算（`never-modify-lean-native` 记忆约束）。
- 五层 Framework 模型均为 LEAN 原生接口（`IAlphaModel`/`IPortfolioConstructionModel`/`IRiskManagementModel`/`IExecutionModel`/`UniverseSelectionModel`），未改 LEAN 核心。
- **缺口**：见下文五层架构核对。

---

## 二、三层管线 核对（Factor Zoo → Model Zoo → Strategy）

### 第 1 层 Factor Zoo — ✅ 主体完成，存在三类不一致

**已建成**：
- 磁盘 `result/factor-zoo/` 共 **161 个因子目录**（101 alpha + 48 tech + 12 Phase-5/Manipulation/Margin）。
- `factor-catalog.yaml` 收录 **159 个 fid**，每条带 `selection_hint`。
- C# 读路径：`FactorStore`（pull-based）+ `FactorRegistry`（35 个运行时因子）+ 三种 Adapter（`RParquetAdapter`/`RBarraAdapter`/`RRegistryAdapter`）。
- `FactorStoreConfig.RegisterDefaults` 默认注册 **126 个 fid**（101 alpha + 15 barra + 5 运行时 + 5 manipulation）。

**差距（catalog ↔ built ↔ registered 三者不一致）**：

| 类别 | 详情 |
|---|---|
| **已构建但未注册进 FactorStore** | 48 个 `tech_*`（仅有 group builder，无逐 fid 注册）；8 个 Phase-5 fid（`accruals_sloan`/`gross_profitability`/`asset_growth`/`roe_change`/`ivol_20d`/`max_ret_20d`/`short_term_reversal`/`margin_factors`）已出 parquet 但 `FactorStoreConfig` 只注册了其中 4 个 manipulation |
| **运行时因子只进 Registry 不进 Store** | 35 个 C# 运行时因子中只有 5 个经 `RRegistryAdapter` 接入 `FactorStore`，其余 30 个只能 `FactorRegistry.Get(id)` 取，`FactorStore.Get` 取不到 |
| **Forward 因子无 parquet** | 12 个 forward fid 由 `export_forward_factors.py` 只写 InfluxDB（`lean_ashare_forward_factor`），无 parquet 落盘，FactorStore 无 adapter |
| **无 C# FactorCatalog 类** | 仅有 YAML 数据文件，无代码层 `FactorCatalog`/`hypothesize` 选择机制（Phase 4 设计里的 hypothesize prompt 注入未落到 C# 运行时） |

### 第 2 层 Model Zoo — ⚠️ 已降级为组件库（2026-08-06 决策）

> **决策**：Model Zoo 不作为独立管线层，降级为 `Models/` 组件库 + `Scripts/factor_zoo/` 研究工具。`ModelRegistry` 脚手架保留（CompositeRiskModel 在用）。具体模型实现（真 MVO/Barra 风险/IC 回灌）归入 P2 推进。详见下文 P1-4 决策标注。

原断点现状（保留供参考）：

这是三层管线中**最薄弱的一层**。

**现状**：
- `Algorithm.CSharp/Models/Core/ModelRegistry.cs` + `ModelMetadata.cs` 脚手架存在，注释自称"模型动物园注册表"。
- **但 `ModelRegistry.Initialize()` 只注册 7 个模型**（1 alpha + 1 portfolio + 4 risk + 1 execution），全仓 ~23 个模型类只覆盖 7 个。
- **`ModelRegistry.Get*` 的策略调用点 = 0**。所有策略都用 `new XxxModel(...)` 直接实例化，没人查注册表。
- 唯一引用方是 `CompositeRiskModel.cs:52,56`（自身调 `Initialize()` + `GetRisk(id)`）。

**与 Factor Zoo 的对照（证明这是断点）**：

| 维度 | Factor Zoo（已成） | Model Zoo（空壳） |
|---|---|---|
| 注册表 | `FactorRegistry` 实际生效 | `ModelRegistry` 休眠，7/23 注册，零调用 |
| 存储/目录 | `FactorStore.Get(id,symbol,date)` + parquet | 无 `ModelStore`，模型不持久化、不可查 |
| 元数据 | `FactorMetadata` 丰富 | `ModelMetadata.Description/Dependencies` 从不填 |
| 发现 | `build_catalog.py` 扫盘建目录 | 无模型发现，`Models/` 只是源码树 |
| 消费 | Alpha 模型运行时 `FactorStore.Get` | 策略 `new` 直接构造，不查表 |

**其他差距**：
- **Barra 不在动物园**：Barra CNE5 V1–V4.2 是 7 个独立巨型策略类 + 6 个 signal/risk model，散在 `Algorithm.CSharp/` 根目录，未进 `Models/`，未注册。
- **Python 与 C# 模型层割裂**：`Scripts/factor_zoo/ic_weighted_alpha.py`（IC 加权）与 `Models/Alpha/ICWeightedAlphaModel.cs` 是两套并行实现，C# 版把 49 因子权重硬编码，不读 Python 的 IC 报告；`alpha_weighted_mvo.py`（带协方差真 MVO）与 `AlphaWeightedMVOPortfolioConstructionModel.cs`（**无协方差，实为 alpha 比例加权 + 上限**，名字误导）也是两套。
- 两个 alpha 模型放错目录：`ChipPeakFactorZooAlphaModel.cs` 在 `Models/` 根而非 `Models/Alpha/`；`Gold2TrendAlphaModel` 在 `Models/Gold2/`。

### 第 3 层 Strategy — ⚠️ 仅 5 个策略真正走五层

- **真正用 LEAN 五层 Framework 的 A 股策略只有 5 个**：`AShareCSI300EnhancedStrategy`、`AShareCSI300Alpha101CompositeStrategy`、`AShareCrowdingFactorZooStrategy`、`AShareBarraCNE5V4Algorithm`、`AShareBarraCNE5V4_2Algorithm`。
- 其余约 20 个 A 股策略（Barra V2/V2_1/V3/V3_2、T1均值回归、T1动量、隔夜异常、行业轮动、ETF 系列、期权波动率套利等）**全是单体 `QCAlgorithm`**，绕过 Framework 的 Alpha/Portfolio/Risk/Execution 隔离。
- 配置：一策略一 config（`Launcher/config/` 下约 70 个），Enhanced/Alpha101Composite 在根目录和 `config/` 各有一份且 `rootPath` 默认值不一致。

---

## 三、五层架构 核对（Universe → Alpha → Portfolio → Risk → Execution）

| 层 | 现状 | 缺口 |
|---|---|---|
| **L1 Universe** | `AShareCSI300UniverseSelectionModel`（000300.SH，月度刷新，pythonnet 调 `barra_cne5_data_loader.py`） | ❌ 只有 CSI300，无 CSI500/CSI1000/全市场；❌ 选股层不做 ST/停牌过滤（只在下单时拒单）；❌ 运行时依赖 pythonnet+外部 parquet，脆（漏调 `PythonInitializer.Initialize()` 即 segfault，见 quant311 删除事故） |
| **L2 Alpha** | `ICWeightedAlphaModel`(49因子) / `Alpha101CompositeAlphaModel`(8) / `CrowdingFactorZooAlphaModel` / `Var*` / `OptionVolArb*` / `ChipPeak*` / BarraV4 Alpha | ⚠️ 依赖预计算 parquet，无 live 因子计算 fallback；⚠️ 无 survivorship-bias 处理记录 |
| **L3 Portfolio** | `AlphaWeightedMVOPortfolioConstructionModel`（名 MVO 实比例加权）/ `EqualWeightPortfolioModel` / LEAN 原生 BlackLitterman（V4 用） | ❌ `AlphaWeightedMVO` **非真 MVO**（无协方差、无风险预算）；❌ 无 A 股约束（板块集中度、ST 排除、停牌目标削减） |
| **L4 Risk** | `MaximumDrawdownPercentPortfolio`(0.20) / `MaxDrawdownRiskModel` / `AShareBarraCNE5V4RiskManagementModel`(最完善：止损/追踪止损/Sharpe 曝露缩放/波动目标/Regime) / `VaRRiskModel` / `CompositeRiskModel` / `RlRiskModel` | ❌ **无 Barra 因子风险分解**（"Barra"策略的 Barra 只用在 alpha 信号，不在风险模型——无因子协方差、无曝露预算）；❌ 无停牌感知（无法强平停牌股）；❌ 无涨跌停风险（卡板无法退出）；❌ 回撤模型一 breach 全清仓，无逐持仓风险 |
| **L5 Execution** | `AShareLotSizeExecutionModel`（100 股整手 + 市价单） | ❌ 无集合竞价（开盘/收盘）处理；❌ 无 TWAP/VWAP；❌ 滑点模型 `ConstantSlippageModel(0m)`（零滑点，不真实）；❌ `OnSecuritiesChanged` 空实现 |

**横切缺陷**：
- market-hours DB 错误（见立足 1）——五层都受影响。
- 手续费注释/代码不一致（注释写万2.5/千1，代码万3/千0.5，注释陈旧）。
- 五层 Framework 只被 5/25 策略采用。

---

## 四、一个环路 核对（构建 → 优化 → 回测 → 复盘 → 重构）

### 各阶段状态

| 阶段 | 状态 | 落点 |
|---|---|---|
| **1. 构建** | ✅ EXISTS | 生产：`soloquant_orchestrator.py`（DeepSeek-v4-pro + GLM-5.1，论文→LLM 筛选→alpha 提取→代码生成）+ `create-strategy` skill（论文标题→LEAN 原生五层→3 gates→trace.md）。Gold2：G0 冻结默认 |
| **2. 优化** | ✅ EXISTS | `auto_optimize/`：d3rlpy **CQL/IQL 离线 RL**（ONNX 导出）+ `bayesian_optimizer.py` 贝叶斯参数扫 + `heldout_validation.py` 训练/测试切分 + bootstrap p 值 + CPCV/walk_forward/overfitting 守护 |
| **3. 回测** | ✅ EXISTS | LEAN-native 强制（`dotnet QuantConnect.Lean.Launcher.dll --config`）；`live_paper_runner.py` + `strategy_control_api.py`(FastAPI :5000) 管理 |
| **4. 复盘** | ⚠️ EXISTS（确定性，非 LLM） | `Scripts/review/`：4 层望远镜 PnL 归因 + TCA 滑点 + 回撤归因，产出冻结 `review.json`（反 p-hacking，可复现）。**核心复盘不是 LLM**——LLM 诊断只喂给重构阶段 |
| **5. 重构** | ⚠️ PARTIAL | LLM 重构 EXISTS：glm-5.2 生成 C# risk-model 子类（`g3_real_generator.py`）。**但只在 Gold2 闭环验证 worktree 里**。因子拣选 PARTIAL：Factor Zoo 存在但**未接入 review→重构反馈环**（`inspiration/hypothesize.py` 提及因子概念但不程序化查询 Factor Zoo catalog） |

### 闭环状态 — ❌ 生产开环，仅 Gold2 worktree 闭合

**关键发现：完整闭环活在 worktree 里，没进主分支。**

- `.claude/worktrees/gold2-closed-loop-proof/Scripts/gold2_closed_loop/` 实现了 G0→review→G1(参数搜)→G2(反馈构造)→G3(LLM 重构)→全局冻结→盲评→封印→裁决，`state_machine.py` 强制生命周期，`EventJournal`/`GenerationJournal` 哈希链。
- `evolution_scheduler.py` 有 `review_drift`(line 104) 和 `inspiration`(line 122) 两个闭环触发点，但 `Scripts/feedback/` **在主分支不存在**（只在 worktree spec 里被 `sys.path.insert` 引用）。
- `gold2_closed_loop/` 目录**主分支 `Scripts/` 下不存在**。
- 生产 SoloQuant 14 阶段是**线性**（crawl→screen→reproduce→optimize→live-paper→monitor），无 review→重构→重测的自动反馈。
- Gold2 闭环只在单标的 518880 上验证过（proof-stage），未泛化到 A 股策略。

### 可观测性 — ✅ EXISTS
- 19 个 Grafana 仪表盘，含 `csi300-alpha101-loop.json`（策略开发环路）、`strategy-review.json`（复盘）、`strategy-lifecycle.json`（生命周期）、`factor-freshness.json`。
- InfluxDB：LEAN 原生 `lean_*` + 复盘 `review_*` + 因子 `factor_freshness`。

---

## 五、差距汇总（按优先级）

### P0 — 阻断生产/实盘
1. **market-hours DB 错误**：SSE/SZSE 24/7 无节假日，实盘会按错误时段成交。修 `market-hours-database.json` + 去掉 FillModel 的 `price>0` 绕过。
2. **闭环未进主分支**：完整开发环路只在 `.claude/worktrees/gold2-closed-loop-proof/`，主分支生产 SoloQuant 开环。需把 `gold2_closed_loop/` + `Scripts/feedback/` + `inspiration/` 合并/泛化到主分支，并从单标的 518880 扩到 A 股策略。
3. **回填进程死亡**：technical（48 子因子全停 ~25%）、roe_change（18% 已死）需 `supervisorctl restart` 续跑，否则 Factor Zoo 永远不全。

### P1 — 管线断点
4. **Model Zoo 是空壳**：`ModelRegistry` 休眠（7/23 注册、零调用）。要么补全注册 + 让策略改走 `ModelRegistry.Get*`，要么明确放弃这层、把"模型"降级为"策略内组件"。当前文档宣称的三层管线中间层名存实亡。

> **【2026-08-06 决策：降级 + 聚焦实现】** 采纳方向 B。Model Zoo 重新定位为**组件库**（`Models/` 目录 + `Scripts/factor_zoo/` 研究工具），而非独立管线层。`ModelRegistry` 脚手架保留（`CompositeRiskModel` 在用，无害，不删）。**不**补全 23 模型注册、**不**让策略改走查表、**不**新建 `ModelStore`。理由：痛点是"具体模型没实现好"（`AlphaWeightedMVO` 无协方差、Barra 不进 risk、IC 报告不回灌 C#），不是"模型太多管不过来"；LEAN 五层接口（`SetAlpha`/`SetPortfolioConstruction`/...）已是天然模型组合方式，无需再加一层注册表。真 MVO + Barra 风险 + IC 回灌作为 **P2** 推进。三层管线修订为：Factor Zoo（数据+研究）→ Strategy（五层 LEAN 架构），Models/ 是 Strategy 内的组件库。
5. **catalog↔built↔registered 三不一致**：48 tech_* + 8 Phase-5 fid 已构建未注册进 FactorStore；30 运行时因子只在 Registry 不在 Store；12 forward 无 parquet。策略能用的因子 ⊂ 已建因子 ⊂ catalog 因子。
6. **Barra 不在动物园**：7 个 Barra 策略单体存在，未抽象成可复用模型，与"Model Zoo"理念冲突。
7. **Python/C# 模型层割裂**：IC 加权、MVO 各有两套实现，C# 版硬编码权重/丢协方差，研究层与运行时层不联动。

### P2 — 五层架构缺口
8. **L1 Universe 单一**：只有 CSI300，无 CSI500/CSI1000/全市场；选股层不滤 ST/停牌。
9. **L3 非 MVO**：`AlphaWeightedMVO` 名不副实（无协方差）。
10. **L4 无因子风险分解**：Barra 只进 alpha 不进 risk，无因子协方差/曝露预算。
11. **L5 执行弱**：无集合竞价、无 TWAP/VWAP、零滑点。
12. **五层 Framework 覆盖低**：仅 5/25 策略采用，其余单体绕过。

### P3 — 工程债
13. **latest_date_resolver 布局不匹配**：扫子目录 vs 扁平 parquet，daemon 每日重算。
14. **AShareStockSecurityInitializer 复制粘贴**：3 策略各一份，无单一来源。
15. **手续费注释陈旧**：注释万2.5/千1 vs 代码万3/千0.5。
16. **无 C# FactorCatalog/hypothesize**：Phase 4 的 hypothesize prompt 注入未落到运行时。

---

## 六、未打通链路清单（一句话各一条）

1. **Factor Zoo → Model Zoo**：因子建好了但 56 个 fid（48 tech + 8 Phase-5）没注册进 FactorStore，Model Zoo 的 alpha 模型取不到。
2. **Model Zoo → Strategy**：`ModelRegistry` 休眠，策略不查表直接 `new`，模型不可发现、不可替换、无版本。
3. **Python 研究层 → C# 运行时**：IC 报告/MVO 协方差不回灌 C#，C# 硬编码、丢协方差。
4. **复盘 → 重构（因子拣选）**：Factor Zoo 未接入 review→重构反馈，重构只走 LLM 代码生成一条路，不查因子目录。
5. **闭环 → 主分支**：完整环路只在 worktree，生产开环。
6. **Barra → 风险模型**：Barra 因子只做 alpha，未做风险分解（L4 断）。
7. **回测 → 实盘**：market-hours DB 错误 + 零滑点，回测能跑实盘会错。
8. **五层 Framework → 全体策略**：20/25 策略单体绕过 Framework。

---

## 七、建议的打通顺序

1. 先修 P0-1（market-hours）和 P0-3（重启回填）——低成本、解阻断。
2. 把 Gold2 闭环 worktree 合并主分支并泛化（P0-2）——这是"一个环路"目标的核心交付。
3. 统一 Factor Zoo 的 catalog↔built↔registered（P1-5）——补注册 56 个 fid + 修 resolver。
4. 决策 Model Zoo 去留（P1-4）——要么补全 `ModelRegistry` 让策略改走查表，要么从文档里降级这层，避免名实不符。
5. 补 L4 Barra 因子风险（P2-10）和 L3 真 MVO（P2-9）——让"Barra 策略"名副其实。
6. 逐步把单体策略迁到五层 Framework（P2-12）。

> 本报告基于 2026-08-05 的代码与数据快照。所有断点均有文件路径佐证（见各节）。
