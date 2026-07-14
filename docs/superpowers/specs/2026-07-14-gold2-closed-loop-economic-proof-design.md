# Gold2 闭环经济收益验证设计

## 1. 目标与结论边界

本实验验证以下链条是否在扣除费用与执行成本后产生真实、稳定的样本外经济增益：

`Gold2 构建 → 参数优化 → 回测复盘与反馈 → 启发性结构重构`

四个阶段：

- **G0 baseline**：预注册的原始 Gold2 结构和默认参数。
- **G1 parameter**：仅优化既有参数，不改变结构。
- **G2 feedback**：使用复盘区生成的反馈信息调整预注册的 shaping/参数边界，不做结构重构。
- **G3 redesign**：只有某层持续不收敛时才创建结构替代；否则是 G2 的不可变别名。

必要比较：`G1-G0`、`G2-G1`、`G3-G2`、`G3-G0`。

允许的最终结论只有：

- `EFFECTIVE`：完整闭环各已激活增量均通过；
- `PARTIALLY_EFFECTIVE`：只有部分阶段产生稳定增益；
- `NOT_EFFECTIVE + REFUTED`：实验有效完成，但经济增量非正；
- `NOT_EVALUATED + UNPROVEN`：窗口、隔离、轨迹或其他正式证据不足，未执行经济裁决。

软件通路可运行、测试通过或单次 Sharpe 为正，均不能代替经济收益证明。

## 2. 当前证据与缺口

现有 LEAN 原生 Gold2 回测是历史参考：

- Sharpe 0.521；
- 最大回撤 16.5%；
- 610 个订单；
- 产物：`Results/gold2-betavol/Gold2BetaVolTargetStrategy-summary.json`。

现有证据不足以宣称闭环提高收益：

1. 没有同一冻结样本外窗口的 G0/G1/G2/G3 对照；
2. 当前复盘缺少 Gold2 `state_trace.jsonl`，正式归因不能使用 residual fallback；
3. 没有完整滚动窗口的候选、失败和代际登记；
4. 没有证明盲测结果未回流到候选生成。

## 2.5 Phase 0：实现前可行性门禁

完整证明机器很重。在实现 `schemas.py`、proof-specific Gold2、formal trace、blind runner 或 seal 设施之前，必须先运行只读的数据与预算可行性检查，确认实验值得建。proof-local adapter 完成后、`preregister` 之前，再执行接口就绪门禁。

新增只读命令：

```text
run_experiment.py assess-feasibility --input <draft.yaml>
```

它不生成候选、不运行训练/复盘/盲测、不创建 sealed root、不打开盲测数据。它输出三类独立判断与一份接口就绪检查：

- `feasibility/data_coverage_audit.json`
- `feasibility/window_inventory.json`
- `feasibility/g3_observability.json`
- `feasibility/interface_readiness.json`

### 2.5.1 历史窗口可行性

逐项审计 518880、AU、VIX、DFII10 实际起止日期、内部缺失区间、时间戳语义、复权规则和数据源版本。输出 `eligible_blind_window_count`。可用性不是四市场日期的简单内连接，而是验证每个中国交易日能否按预注册的时区、发布日期、as-of join 和最大陈旧期限构造不含未来信息的完整特征。

已核实的本地 Tushare 原始存储事实：

- `tushare_data_v2/fund_daily/ts_code=518880.SH/data.parquet` 覆盖 2017-11-10 至 2026-06-23；
- 共 2,083 行和 2,083 个不同交易日，无重复日期；
- 2013—2016 年没有 `fund_daily` 行情，2017 年只有 36 个交易日；
- `fund_basic` 显示成立日 2013-07-18、上市日 2013-07-29；`fund_nav` 从 2013-07-18 开始，但净值不得替代可交易 OHLC 行情；
- 当前 LEAN 518880 日线覆盖 2018-01-02 至 2025-12-31；
- AU 约从 2008 年开始，VIX/DFII10 约从 2015 年开始。

因此正式窗口改为三个完整自然年训练、一个完整自然年复盘和一个完整自然年盲测，并固定使用 2018—2025。Phase 0 须按预注册交易日历和 as-of 规则复核 W1—W4 的特征可构造性、内部 gap 和快照哈希，不得通过移动窗口首尾来迁就缺失。若少于三个窗口有效，结果为 `BLOCKED_INSUFFICIENT_TEST_WINDOWS`。

### 2.5.2 G3 可观测性就绪

Phase 0 只能静态验证 proof-local generation 配置具备 `minimum_required_generation_count = 3`、每代预算、review 输入、失败预算政策和记录 schema，输出 `g3_observability_readiness`；它不能在不运行构建的情况下预言三代都会有效。运行时若某窗口无法形成三代有效记录，则该窗口标记 `G3_ALIAS_TRIGGER_UNOBSERVABLE`。至少三个最终有效窗口必须具备完整 G3 可观测性，否则结论为 `NOT_EVALUATED + UNPROVEN`。

### 2.5.3 证明范围边界

本实验只验证可交易的历史冻结样本外收益。live-paper 和真实资金实盘属于后续独立实验，不参与本次 go/no-go、acceptance gate 或 verdict。

### 2.5.4 接口就绪

现有生产接口不满足正式 proof 协议，因此不得直接复用：

- `Scripts/auto_optimize/evolution_scheduler.py:187-194` 的 `fire_optimization` 把 CQL trace/policy 路径硬编码为 OptionVolArb；
- 同文件 `:199-203` 的 ONNX `obs-dim` 硬编码为 8，而 Gold2 manifest observation fields 为 13；
- `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml` 仍标记 `rl_state_completeness: partial`，且 `dim_hint: 15` 与 feedback observation 13 不一致；
- generation count 在局部 state 递增后未再持久化（同文件 `:251-283`），且 `Scripts/inspiration/generations.py:9-16` 使用覆盖写，非 append-only、非 collision-reject；
- `inspired_layers` 写入内存 state 后 `fire_optimization` 重新从磁盘读 state（同文件 `:141-158` 与 `:208-214`），触发信息不保证传到 hypothesize。

### 2.5.5 go/no-go 矩阵

只有数据与预算可行性 PASS 才值得实现完整证明设施。proof-local adapter 完成后，接口就绪检查必须确认所有禁止路径均不可达，才能进入 `preregister`。不得使用 `fund_nav` 代替 518880 可交易行情，也不得用 AU 期货或其他代理拼接上市前价格。

### 2.5.6 窗口不足时的结论

若 Phase 0 确认少于三个有效窗口，实验以 `BLOCKED_INSUFFICIENT_TEST_WINDOWS` 停止，结论固定为 `unproven`。不得使用 `fund_nav`、上市前代理价格、缩短单个窗口或删除表现不佳年份来补足窗口。

## 3. 实验架构

采用**滚动样本外四阶段消融**，证明目标限于可交易的历史样本外经济收益。live-paper 与实盘收益不属于本次验收范围，不得用于升级历史 verdict。

窗口固定为三个完整自然年训练、一个完整自然年复盘/重构和一个完整自然年冻结盲测：

| 窗口 | 训练 | 复盘/重构 | 冻结盲测 |
|---|---|---|---|
| W1 | 2018-01-02—2020-12-31 | 2021-01-01—2021-12-31 | 2022-01-01—2022-12-31 |
| W2 | 2019-01-01—2021-12-31 | 2022-01-01—2022-12-31 | 2023-01-01—2023-12-31 |
| W3 | 2020-01-01—2022-12-31 | 2023-01-01—2023-12-31 | 2024-01-01—2024-12-31 |
| W4 | 2021-01-01—2023-12-31 | 2024-01-01—2024-12-31 | 2025-01-01—2025-12-31 |

自然年度边界固定，不因数据缺失移动。数据审计按中国交易日历、预注册 as-of join 和最大陈旧期限检查四类必要输入；区间内部 gap 超过预注册阈值时窗口失效，不得人工补写 bar。2017 年仅 36 条 518880 行情，不进入正式窗口。

正式结论至少要求三个互不重叠的年度盲测区间。训练区和复盘区滚动重叠，因此窗口不能表述为四次完全概率独立实验；统计必须使用窗口级配对、paired block bootstrap、窗口间相关性和 leave-one-window-out。Phase 0 若判定少于三个有效窗口，实验以 `BLOCKED_INSUFFICIENT_TEST_WINDOWS` 关闭，不得缩短单个窗口或降低门槛迁就结果。`execute` 阶段必须重新验证 Phase 0 artifact hash 与当前数据快照一致。

采用**全局一次冻结**：W1—W4 的所有 G0/G1/G2/G3 候选均须在任何盲测结果解封前完成并冻结，然后一次性打开四个盲测年度。construction 进程只挂载 train/review partition 的只读快照，blind partition 不可见，并记录路径白名单与访问日志。较早盲测年的原始市场数据可按日历自然进入后续窗口训练，但其测试指标、报告、交易、归因和 verdict 不得进入后续候选构建；违反时设置 `invalid_reason=INFORMATION_LEAKAGE`。窗口边界须预注册包含/排除规则、warm-up、节假日、缺失 bar、资本重置与拼接、阶段间收益对齐、无交易阶段和重叠训练信息的处理。

## 4. 独立模块与不可变输入

实验按阶段止损实现，避免先建设通用证明平台再发现经济增量不成立：

1. 数据/窗口/泄漏 feasibility；
2. 单窗口 G0/G1 的 LEAN-native 最小垂直切片和行为等价；
3. 四窗口 G0/G1/G2；
4. 仅在 G3 readiness 与触发路径可观测后实现 redesign；
5. 完成全局冻结、聚合统计与最终 sealing。

任何阶段无法通过其门禁即停止，不提前实现后续阶段。

正式证据由独立模块拥有：

```text
Scripts/gold2_closed_loop/
├── run_experiment.py
├── schemas.py
├── lean_artifacts.py
├── statistical_tests.py
├── evidence.py
└── schema/
```

它是实验根目录的唯一写入者。现有 optimizer/review/feedback/inspiration 不直接复用，而是通过两级适配器接口调用：

```text
ProofRunner
  → ParameterOptimizerAdapter
  → FeedbackConstructionAdapter
  → InspirationConstructionAdapter
  → LeanBlindEvaluator
```

每个 adapter 必须显式接受 experiment/window/stage/candidate ID、train/review partition、frozen manifest snapshot、trace path、output run_dir、observation fields、shaping config、seed、candidate budget，并返回 typed result。adapter 禁止：

- 调用生产 daemon；
- 读写生产 evolution state；
- 硬编码 OptionVolArb trace/policy 路径；
- 读取 manifest 的硬编码 ONNX `obs-dim`；
- glob 全局 `Results` 或按 mtime 选择产物；
- blind-open 后重建候选；
- 自行递增或覆盖 generation。

实验不得修改：

- `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml`；
- `Launcher/config/config-gold2-beta-vol-target-backtest.json`；
- 生产 inspiration generation/layer-state 文件；
- 已有 Gold2 策略行为。

实验使用独立快照。为获得严格决策/订单/成交/持仓轨迹，新增 proof-specific Gold2 算法实现，并先用行为等价回归证明其 G0 与现有 Gold2 一致，不能静默改变成熟策略。等价比较字段固定为 decision、order intent、fill、holdings 和 portfolio value；时间键必须相同，decimal 金额/数量精确一致，double 派生权重使用 preregistration 中的绝对与相对容差。仅允许忽略 trace ID、run directory 和序列化格式等非经济字段；任何经济字段不一致均阻断正式实验。

## 5. 预注册与生命周期

拆分为两个命令：

```text
run_experiment.py preregister --input <draft.yaml> --experiment-id <id>
run_experiment.py execute --experiment-root <sealed-root> [--resume]
```

`preregister` 必须在任何优化、复盘区策略运行、反馈生成或盲测前，并且 Phase 0 已 PASS：

1. 验证 CLI ID 与 YAML ID 一致；
2. 拒绝已存在根目录；
3. 规范化并复制预注册；
4. 写哈希；
5. 原子创建 `PREREGISTERED`。

生命周期、有效性和 verdict 分离，全部使用 closed enum：

- `lifecycle_status`：`NEW`、`PREREGISTERED`、`DATA_VALIDATED`、`RUNNING_CONSTRUCTION`、`CANDIDATES_FROZEN`、`RUNNING_BLIND_TEST`、`BLIND_TEST_COMPLETE`、`SEALED`、`BLOCKED`、`INVALID`、`FAILED_EXECUTION`。`BLOCKED` 携带 `block_reason`。
- `validity_status`：`VALID`、`INVALID`。
- `invalid_reason`：`INFORMATION_LEAKAGE`、`MISSING_FORMAL_TRACE`、`EVIDENCE_CAPTURE_FAILED`、`HASH_MISMATCH`、`POST_BLIND_MUTATION`、`DATA_INVALID`。
- `block_reason`：`BLOCKED_INSUFFICIENT_TEST_WINDOWS`、`BLOCKED_INTERFACE_NOT_PROOF_READY`、`BLOCKED_INSUFFICIENT_DATA`。
- `execution_status`：`PENDING`、`RUNNING`、`SUCCEEDED`、`FAILED_STRATEGY`、`FAILED_INFRASTRUCTURE`、`FAILED_EVIDENCE_CAPTURE`、`TIMED_OUT`、`NO_TRADES`。
- `historical_verdict`：`EFFECTIVE`、`PARTIALLY_EFFECTIVE`、`NOT_EFFECTIVE`、`NOT_EVALUATED`。
- `disposition`：`UNPROVEN` 仅与 `NOT_EVALUATED` 配对；`REFUTED` 仅与 `NOT_EFFECTIVE` 配对；其他 verdict 必须为 null。

```json
{
  "lifecycle_status": "SEALED",
  "validity_status": "VALID",
  "historical_verdict": "PARTIALLY_EFFECTIVE",
  "disposition": null
}
```

acceptance-gate 失败不进入 lifecycle：完成且有效的负结果实验仍为 `SEALED + VALID + NOT_EFFECTIVE/REFUTED`，而不是 `FAILED_ACCEPTANCE_GATES`。

核心生命周期：

```text
NEW → PREREGISTERED
PREREGISTERED → DATA_VALIDATED | BLOCKED | INVALID
DATA_VALIDATED → RUNNING_CONSTRUCTION
RUNNING_CONSTRUCTION → CANDIDATES_FROZEN | FAILED_EXECUTION | INVALID
CANDIDATES_FROZEN → RUNNING_BLIND_TEST
RUNNING_BLIND_TEST → BLIND_TEST_COMPLETE | FAILED_EXECUTION | INVALID
BLIND_TEST_COMPLETE → SEALED | INVALID
```

`BLIND_ACCESS_OPENED` 之后任何代码、配置、候选或选择逻辑变化都使实验失效为 `INVALID`，不触发重跑。

## 6. 数据、市场条件与泄漏门禁

G0-G3 在同一窗口必须共用：

- 日期、初始资金和基准；
- 518880、AU、VIX、DFII10 数据快照；
- LEAN 程序集；
- 经纪、费率、成交、购买力、结算、整手、市场时间模型；
- 缺失数据和复权规则；
- 预注册随机种子集合。

`data_audit.json` 必须验证时间覆盖、时间戳、未来数据访问、复权、数据哈希和缺失处理。任何版本不得使用不同数据规则。

信息流：

1. 训练区选择 G1；
2. 复盘区生成**一次冻结**的 formal review、attribution 输入和 feedback evidence bundle；G2/G3 在该冻结 bundle 上执行预注册数量的 proof-local construction generations，不再二次进入复盘数据；
3. G2 候选目标评估默认仍只使用训练数据，复盘数据不得再次排名候选；
4. 盲测仅评价被冻结候选；
5. 盲测之后禁止生成、重排、修复或重试候选；任何逻辑修改必须使用新实验 ID。

§7 的“连续三代 attribution gap”必须明确是同一冻结 evidence bundle 上对不同 generation state 的诊断，而不是重复窥视复盘区对候选排名；如果“代”需要按时间更新，则把复盘期预注册切分为顺序子期，每代只能看到截至该代 cutoff 的复盘数据，并在 spec 中写死该选择。

## 7. 四阶段生成规则

### G0

原始 Gold2 结构和预注册默认参数。目的不是争取最好成绩，而是定义“无闭环”的反事实基线。

### G1

只改变 manifest 已声明的参数。每个试验，包括失败、无交易、超时和 prune，必须进入候选事件登记并消耗搜索预算。候选先满足最低交易数、回撤、DSR、参数边界和训练子窗口稳定性，再按预注册排序选择。

### G2

冻结 G1 在复盘区生成 formal review 和 feedback artifact。允许的反馈次数、候选预算、shaping term、参数边界、目标与 tie-break 必须预注册。不得修改 C# 结构。

### G3

触发条件固定为：连续至少三代 attribution gap 超阈值，且 shaping 权重不收敛或顶格 3.0，并且不存在待审候选。生成证据使用实验窗口专属 generation，不读取全局历史。

#### Generation semantics

每个 generation 记录：`generation_id`、`generation_index`、`parent_generation_id`、`generation_cutoff`、`input_evidence_sha256`、`candidate_set_sha256`、`attribution_gap`、`shaping_weight`、`pending_candidate_count`、`convergence_status`、`trigger_observation_valid`、`terminal_reason`。“连续三代”是三个相邻且有效的 generation records；任一代失败、缺失或输入 hash 变化是否中断连续性，attribution gap 阈值、shaping 权重顶格 3.0 的边界比较、“不存在待审候选”的检查时点、每窗口最小/最大代数、generation budget 是否计入候选搜索预算、generation 何时停止、是否允许触发后继续生成更多代，都必须预注册。

区分两种决策：

- `G3_CONSTRUCTION_ELIGIBLE`：训练/复盘允许信息证明目标层及整体经济结果改善；
- `G3_HISTORICAL_INCREMENT_ACCEPTED`：冻结候选随后通过盲测聚合门槛。

无持续不收敛或候选被拒绝时，最终冻结版本可复用 G2 的哈希而不复制结果文件，但 `ΔRedesign` 必须为 `NOT_APPLICABLE` 或明确负结果，不得伪装成零增量通过。`g3_alias_reason` 是 closed enum：

- `NOT_TRIGGERED`：有至少三代有效 generation 观测，但条件未持续满足；这是“重构不必要”的证据，允许完整闭环 verdict，但报告必须写成“最终闭环版本有效，重构未被需要”，不得称重构有效；
- `TRIGGER_UNOBSERVABLE`：窗口内无法形成三代有效 generation，该窗口 redesign 不可识别；至少三个窗口出现此状态时结论为 `NOT_EVALUATED + UNPROVEN`；
- `CONSTRUCTION_FAILED`：触发后候选构建失败，属于执行失败，不能获得 `EFFECTIVE`；
- `CANDIDATE_REJECTED`：触发并构建但训练门禁拒绝，是有效负阶段结果，最终最多 `PARTIALLY_EFFECTIVE`。

`convergence_status` 固定为 `CONVERGED|NON_CONVERGED|UNOBSERVABLE`；`generation_terminal_reason` 固定为 `BUDGET_EXHAUSTED|TRIGGERED|CONVERGED|EXECUTION_FAILED|INVALID_EVIDENCE`。边界公式、阈值、检查时点和失败是否打断连续性均为 preregistration schema 的必填字段，缺失时拒绝预注册。

## 8. Formal trace

正式模式不能使用当前 permissive JSONL loader。当前 Gold2 的 `RL_TRACE_PATH` / `SerializeRlState`（`Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs:144-201`）只序列化 per-bar 状态快照、`File.AppendAllText` 写入且失败仅记日志后继续，只能用于 feedback 诊断，**不能**作为正式证据。

proof-specific Gold2 必须使用新的版本化事件 schema 与独立路径，至少输出：

- `DECISION`；
- `ORDER_INTENT`；
- `FILL`；
- `HOLDINGS_SNAPSHOT`。

每条记录包含 `schema_version`、experiment/window/stage/run/candidate ID，决策时间、源数据可用时间、参数快照、代码/配置哈希和关联 ID。`w_realized` 必须来自成交后的 `HOLDINGS_SNAPSHOT`，禁止 review adapter 用 P&L 反推数量（当前 `Scripts/review/adapters/gold2.py:71-84` 的 `profit_loss / dp` fallback 仅诊断，正式模式不可用）。trace sink 是 required dependency：任意写入失败、flush 失败或 reconciliation 失败都使 LEAN run 终态为 `FAILED_EVIDENCE_CAPTURE`，不能静默降级。

formal loader 拒绝当前 RL JSONL，并拒绝 malformed/unknown、重复或非递增时间、缺失 eligible bar、身份/哈希不符、越界、未关联订单成交持仓、非有限值和成本/权重矛盾。现有 permissive loader（`Scripts/review/artifacts.py`、`Scripts/feedback/signals.py`）可保留为诊断用途，但正式模式必须不可达。

feedback adapter 在无 formal trace 时正式模式必须返回 `validity_status=INVALID, invalid_reason=MISSING_FORMAL_TRACE`，而不是 shaping=0 后继续（当前 `Scripts/feedback/adapters/gold2.py:17-28` 的 graceful degradation 仅诊断）。

只有完整望远镜归因可用于正式重构；residual fallback 仅允许诊断。

## 9. LEAN 运行隔离

每次运行有唯一绝对 `run_dir`。适配器必须：

1. 显式设置 `results-destination-folder` 到该 `run_dir`；
2. 在 run_dir 保存冻结配置；
3. 设置确定性 run ID/结果名；
4. 精确要求预期 result packet；
5. 返回带路径、哈希、退出码、时间和 LEAN statistics 的 typed result；
6. 禁止按全局 Results 的 mtime 或 glob 选择结果（当前 `Scripts/auto_optimize/lean_runner.py:69-79` 的 mtime 选择在正式模式不合规）。

## 10. 候选、失败与恢复

候选使用 append-only 事件而非可变行：

```text
CANDIDATE_REGISTERED
EXECUTION_STARTED
EXECUTION_FINISHED
EXECUTION_FAILED
CANDIDATE_PRUNED
CANDIDATE_INVALIDATED
CANDIDATE_SELECTED
CANDIDATE_FROZEN
BLIND_ACCESS_OPENED
```

候选终态包括：`SUCCEEDED`、`FAILED_STRATEGY`、`FAILED_INFRASTRUCTURE`、`TIMED_OUT`、`PRUNED`、`NO_TRADES`、`INVALID`、`DUPLICATE`、`DOMINATED`、`REJECTED`。

单文件使用同目录临时文件、flush/fsync、`os.replace`；JSONL 由单 writer 或文件锁写入，每条含 sequence、event_id、previous_event_sha256。

resume 规则：操作 ID 由 experiment/window/stage/partition/candidate/scenario/attempt 确定；只有完整、哈希有效的终态操作可跳过；不完整目录隔离；候选冻结后先原子创建 `blind_opened.json`，之后任何代码、配置、候选或选择逻辑变化都使实验失效。

## 11. 经济指标与基准

LEAN-native 边界：

- 订单、成交、持仓、现金、费用、组合价值、基准值和日收益均源自 LEAN；
- Python 可编排运行、校验产物、计算预注册 paired 统计并封存证据；
- Python 禁止重建 fills/holdings/fees/portfolio value；
- G0-G3 必须共用同一 LEAN assembly 与 market-model hash；
- proof-specific Gold2 wrapper 可加 instrumentation，但必须先通过行为等价回归。

区分三类统计来源：

- accounting/交易/持仓/费用/原生统计为 LEAN-native（formal）；
- LEAN 导出收益/权益序列上的预注册推断变换（DSR、block bootstrap、downside deviation、paired inference）为 **formal proof statistics**，不是“仅诊断”——这与 §12 的 bootstrap/DSR 一致，纠正原 spec 的措辞冲突；
- 独立重建组合会计禁止。

canonical metric registry 分为两组。`acceptance_metrics` 只包含 Total Net Profit、CAGR、预注册风险调整主指标（固定为 Sharpe）、MDD、fees 和相对 518880 的 information ratio；每项映射到确切 LEAN JSON path、单位、方向和 null 策略。`diagnostic_metrics` 包含 Sortino、Calmar、downside deviation、最差日/月、恢复期、win rate、profit-loss ratio、expectancy、slippage、turnover、order count 和 alpha/beta，不得事后升级为验收指标。

已知歧义需在 registry 中固定：summary `Win Rate`（0.87）与 trade `winRate`（≈0.7679）不同；summary `Sharpe Ratio` 与 trade-level `sharpeRatio` 不同；`Net Profit` 与 `totalNetProfit` 表示不同；Alpha/Beta 当前为 0 但 IR 非零，须先做 benchmark 完整性校验；Calmar 须明确定义来源字段；slippage 来自 formal trace 的参考价与成交价，LEAN summary 不直接提供。

基准：现金/中国无风险利率和 518880 buy-and-hold。

差值：

```text
ΔParameter = G1-G0
ΔFeedback  = G2-G1
ΔRedesign  = G3-G2
ΔLoop      = G3-G0
```

百分比统一存 fraction。MDD 存 `[0,1]` 的正损失比例：

```text
drawdown_deterioration = candidate_mdd - parent_mdd
pass iff drawdown_deterioration <= 0.10
```

单窗口胜出要求净收益更高且满足回撤限制。聚合资本拼接、窗口重置、零波动 IR、censored recovery 和边界比较规则必须预注册。

## 12. 统计与多重试验

- 窗口内对 paired daily excess returns 做时间 block bootstrap；
- 聚合推断以年度盲测窗口为 cluster，不把池化日收益当作独立重复实验；
- 窗口级配对方向、窗口间相关性和 leave-one-window-out；
- DSR；
- 全部候选和失败登记；
- 报告 `attempted_trial_count` 与有效收益序列相关矩阵产生的 `effective_trials`，使用预注册保守公式；
- 仅有三至四个相邻年度窗口，不机械宣称常规 `p<0.05` 或独立重复实验；结论分为正式 verdict 与辅助证据强度，后者不得覆盖前者。

## 13. 成功门槛

所有阈值均由 preregistration schema 强制数值化，不允许自然语言判定：

- `max_single_trade_contribution_fraction`：单笔交易对总净增量的最大允许贡献；
- `max_single_month_contribution_fraction`：单月对总净增量的最大允许贡献；
- `leave_one_window_out_min_delta`：删除任一窗口后允许的最小闭环净增量；
- `paired_bootstrap_min_probability` 与 `paired_bootstrap_ci_level`；
- G3 attribution gap、不收敛、浮点容差和 generation 连续性规则。

字段缺失、非有限或边界关系不合法时，`preregister` 必须失败。正式 Gold2 闭环有效必须同时满足：

1. 至少三个有效且盲测区间互不重叠的年度窗口；
2. G3 在至少 60% 有效窗口战胜 G0：四窗口须至少 3/4，三窗口须至少 2/3；
3. 聚合样本外 CAGR、Sharpe 和扣费净收益均高于 G0；
4. 聚合回撤恶化不超过 0.10；
5. 费用、滑点和 A 股规则已计入；
6. 相对 518880 的 information ratio 为正，且 benchmark 完整性校验通过；
7. 单笔与单月贡献分别不超过预注册的 `max_single_trade_contribution_fraction` 和 `max_single_month_contribution_fraction`；
8. 每次 leave-one-window-out 的闭环净增量均不低于 `leave_one_window_out_min_delta`；
9. paired bootstrap 达到预注册概率/区间门槛，且窗口级配对方向不与其冲突；
10. G1-G0、G2-G1 及被激活的 G3-G2 分别通过，完整结论才可为 `effective`。

单窗口阶段获胜至少要求候选扣费净收益高于父版本、Sharpe 改善、最大回撤恶化不超过 0.10，并通过预注册的单笔和单月贡献上限。仅 Sharpe 上升但净收益下降不得判为获胜。

某阶段无贡献时必须使用 `PARTIALLY_EFFECTIVE`。无效隔离、窗口不足或缺失证据使用 `NOT_EVALUATED + UNPROVEN`；有效完成的实验中经济增量非正使用 `NOT_EFFECTIVE + REFUTED`。

## 14. 失败处理

- 数据不达标：整个窗口 `invalid_data_window`，规则对所有版本一致；
- G1 无候选：G1 alias G0；
- G2 被拒绝：G2 alias G1；
- G3 `NOT_TRIGGERED`：最终版本复用 G2，`ΔRedesign=NOT_APPLICABLE`，允许评价其他闭环阶段但不得宣称重构有效；
- G3 `TRIGGER_UNOBSERVABLE`：该窗口重构增量未证明；至少三个有效可观测窗口不足时，整体 `NOT_EVALUATED + UNPROVEN`；
- G3 `CONSTRUCTION_FAILED`：执行失败，不能获得 `EFFECTIVE`；
- G3 `CANDIDATE_REJECTED`：保留有效负结果并复用 G2，最终最多 `PARTIALLY_EFFECTIVE`；
- 代码/配置/数据语义修复：新实验 ID；
- 合格 infrastructure retry 仅按预注册政策、且结果未暴露时允许；
- 盲测负结果只能保存，不能触发同一实验的自动修复。

## 15. 证据包与 sealing

```text
Results/gold2-closed-loop/<experiment_id>/
├── preregistration.yaml
├── feasibility/
├── snapshots/
├── candidate-events.jsonl
├── windows/
│   ├── W1/
│   ├── W2/
│   ├── W3/
│   └── W4/
├── aggregate/
└── seal.json
```

历史实验一次性封存。`seal.json` 覆盖预注册，数据、代码、LEAN assembly 和市场模型哈希，全部候选及失败，四窗口 G0—G3 产物，聚合统计与 verdict。签名密钥位于证据根目录之外，并把根哈希发布到外部 append-only 位置；seal 记录签名算法、公钥指纹和可信时间。必须先封存完整负结果，再解释结果。

## 16. 范围外后续验证

live-paper 与真实资金实盘不属于本设计的验收范围。若历史实验通过，可另立新实验，以冻结的 Gold2 champion/challenger、实时不可变事件流、独立 paper 账户和预注册期限验证运营可执行性；该结果不得回写或升级本实验的历史 verdict。

## 17. 最终报告

报告展示：

- 每窗口 G0/G1/G2/G3 全部指标和负结果；
- G1-G0、G2-G1、G3-G2、G3-G0 增量；
- 现金和 518880 基准；
- 费用前后、原始与等波动率诊断；
- 最差窗口、单月/单笔依赖、候选数量和失败原因；
- DSR、paired block bootstrap、窗口级配对、窗口间相关性和 leave-one-window-out；
- 历史 economic verdict；
- 每个数字对应的 LEAN artifact/hash。

合法报告语句：

- **有效**：多窗口冻结样本外稳定提升风险调整收益，扣费后成立；
- **部分有效**：明确指出哪些阶段有效、哪些阶段应删除；
- **未证明/已否证**：软件闭环能运行，但无充分或正向经济证据，维持原 champion。

## 18. 测试要求

- Python schema、状态机、事件登记、原子写、hash/seal、指标、bootstrap、DSR、泄漏门禁和 failure injection；
- 四窗口边界、四数据集交集、共同有效交易日和少于三窗口时的阻断；
- resume/idempotency、并发 writer、stale artifact、blind-open 后逻辑变化拒绝；
- C# formal trace 序列化和 order/fill/holdings reconciliation；
- proof-specific G0 与现有 Gold2 行为等价回归；
- G1 参数类型、预算和失败登记，G2 禁止复盘二次排名，G3 三代触发及 alias 原因；
- 使用极小 synthetic 数据做一次真实 LEAN integration；
- 正式验收通过唯一 runner 外部接口，不依赖手工拼接文件。
