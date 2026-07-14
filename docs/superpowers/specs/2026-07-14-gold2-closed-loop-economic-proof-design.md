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

- `effective`：完整闭环各已激活增量均通过；
- `partially_effective`：只有部分阶段产生稳定增益；
- `unproven_refuted`，并强制附加 `disposition=unproven|refuted`。

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
4. 没有证明盲测结果未回流到候选生成；
5. 没有 live-paper champion/challenger 证据。

## 3. 实验架构

采用**滚动样本外四阶段消融 + live-paper champion/challenger**。

建议窗口：

| 窗口 | 训练 | 复盘/重构 | 冻结盲测 |
|---|---|---|---|
| W1 | 2015-2019 | 2020 | 2021 |
| W2 | 2016-2020 | 2021 | 2022 |
| W3 | 2017-2021 | 2022 | 2023 |
| W4 | 2018-2022 | 2023 | 2024 |
| W5 | 2019-2023 | 2024 | 2025 |

正式结论至少要求三个独立测试年份。已知 518880 约从 2018 年开始有可用覆盖，按五年训练加一年复盘可能只支持 W4/W5。因此在补齐 518880、AU、VIX、DFII10 至足够窗口前，实验必须以 `BLOCKED_INSUFFICIENT_TEST_WINDOWS` 失败关闭，不能缩短窗口或降低门槛来迁就结果。

每个窗口的所有候选必须在任何盲测结果解封前全部冻结。较早测试年的**原始市场数据**可按后续窗口日期进入训练，但其历史测试指标、报告、交易、归因和 verdict 不得进入后续候选构建；否则该窗口标记 `dependent=true`，不计入独立窗口数。

## 4. 独立模块与不可变输入

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

它是实验根目录的唯一写入者。现有 optimizer/review/feedback/inspiration 通过显式适配器调用，不能自行发现或改写全局 `Results`、生产 manifest、共享 generation 或 layer state。

实验不得修改：

- `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml`；
- `Launcher/config/config-gold2-beta-vol-target-backtest.json`；
- 生产 inspiration generation/layer-state 文件；
- 已有 Gold2 策略行为。

实验使用独立快照。为获得严格决策/订单/成交/持仓轨迹，新增 proof-specific Gold2 算法实现，并先用行为等价回归证明其 G0 与现有 Gold2 一致，不能静默改变成熟策略。

## 5. 预注册与生命周期

拆分为两个命令：

```text
run_experiment.py preregister --input <draft.yaml> --experiment-id <id>
run_experiment.py execute --experiment-root <sealed-root> [--resume]
```

`preregister` 必须在任何优化、复盘区策略运行、反馈生成或盲测前：

1. 验证 CLI ID 与 YAML ID 一致；
2. 拒绝已存在根目录；
3. 规范化并复制预注册；
4. 写哈希；
5. 原子创建 `PREREGISTERED`。

生命周期、有效性和 verdict 分离：

```json
{
  "lifecycle_status": "SEALED",
  "validity_status": "VALID",
  "historical_verdict": "PARTIALLY_EFFECTIVE"
}
```

核心生命周期：

```text
NEW → PREREGISTERED
PREREGISTERED → DATA_VALIDATED | BLOCKED_* | INVALID_*
DATA_VALIDATED → RUNNING_CONSTRUCTION
RUNNING_CONSTRUCTION → CANDIDATES_FROZEN | FAILED_EXECUTION | INVALID_*
CANDIDATES_FROZEN → RUNNING_BLIND_TEST
RUNNING_BLIND_TEST → BLIND_TEST_COMPLETE | FAILED_EXECUTION | INVALID_*
BLIND_TEST_COMPLETE → SEALED | FAILED_ACCEPTANCE_GATES | INVALID_*
```

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
2. 复盘区生成一次冻结反馈，并构建 G2/G3；
3. G2 候选目标评估默认仍只使用训练数据，复盘数据不得再次排名候选；
4. 盲测仅评价被冻结候选；
5. 盲测之后禁止生成、重排、修复或重试候选；任何逻辑修改必须使用新实验 ID。

## 7. 四阶段生成规则

### G0

原始 Gold2 结构和预注册默认参数。目的不是争取最好成绩，而是定义“无闭环”的反事实基线。

### G1

只改变 manifest 已声明的参数。每个试验，包括失败、无交易、超时和 prune，必须进入候选事件登记并消耗搜索预算。候选先满足最低交易数、回撤、DSR、参数边界和训练子窗口稳定性，再按预注册排序选择。

### G2

冻结 G1 在复盘区生成 formal review 和 feedback artifact。允许的反馈次数、候选预算、shaping term、参数边界、目标与 tie-break 必须预注册。不得修改 C# 结构。

### G3

触发条件固定为：连续至少三代 attribution gap 超阈值，且 shaping 权重不收敛或顶格 3.0，并且不存在待审候选。生成证据使用实验窗口专属 generation，不读取全局历史。

区分两种决策：

- `G3_CONSTRUCTION_ELIGIBLE`：训练/复盘允许信息证明目标层及整体经济结果改善；
- `G3_HISTORICAL_INCREMENT_ACCEPTED`：冻结候选随后通过盲测聚合门槛。

无持续不收敛或候选被拒绝时，G3 是 G2 的哈希别名，不复制结果文件，也不得宣称重构收益。

## 8. Formal trace

正式模式不能使用当前 permissive JSONL loader。proof-specific Gold2 至少输出：

- `DECISION`；
- `ORDER_INTENT`；
- `FILL`；
- `HOLDINGS_SNAPSHOT`。

每条记录包含 schema、experiment/window/stage/run/candidate ID，决策时间、源数据可用时间、参数快照、代码/配置哈希和关联 ID。`w_realized` 必须来自成交后的持仓快照。轨迹写入失败必须使正式 LEAN 运行失败，不能静默降级。

strict loader 拒绝 malformed/unknown、重复或非递增时间、缺失 eligible bar、身份/哈希不符、越界、未关联订单成交持仓、非有限值和成本/权重矛盾。

只有完整望远镜归因可用于正式重构；residual fallback 仅允许诊断。

## 9. LEAN 运行隔离

每次运行有唯一绝对 `run_dir`。适配器必须：

1. 显式设置 `results-destination-folder`；
2. 在 run_dir 保存冻结配置；
3. 设置确定性 run ID/结果名；
4. 精确要求预期 result packet；
5. 返回带路径、哈希、退出码、时间和 LEAN statistics 的 typed result；
6. 禁止按全局 Results 的 mtime 或 glob 选择结果。

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

所有正式交易、持仓、费用和原生统计来自 LEAN。额外统计只可使用 LEAN 导出的日收益/权益序列做诊断。

指标：Total Net Profit、CAGR、Sharpe、Sortino、Calmar、MDD、downside deviation、最差日/月、恢复期、win rate、profit-loss ratio、expectancy、fees、slippage、turnover、order count、alpha/beta/information ratio。

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

- DSR；
- 对 paired daily excess returns 做 block bootstrap；
- 窗口级配对检验；
- 全部候选和失败登记；
- 报告 `attempted_trial_count` 与有效收益序列相关矩阵产生的 `effective_trials`，使用预注册保守公式；
- 不机械宣称小样本 `p<0.05`，结论分为已证明、较强证据、初步迹象、未证明和已否证。

## 13. 成功门槛

正式 Gold2 闭环有效必须同时满足：

1. 至少三个独立盲测窗口；
2. G3 在至少 60% 有效窗口战胜 G0；
3. 聚合样本外 CAGR 和 Sharpe 高于 G0；
4. 聚合回撤恶化不超过 0.10；
5. 费用、滑点和 A 股规则已计入；
6. 相对 518880 的 information ratio 为正；
7. 结果不依赖单一窗口或单笔交易；
8. DSR、bootstrap 和配对证据支持；
9. G1-G0、G2-G1 及被激活的 G3-G2 分别通过，完整结论才可为 `effective`。

某阶段无贡献时必须使用 `partially_effective`。无效隔离、窗口不足或缺失证据属于 `unproven`；在有效实验中经济增量非正属于 `refuted`。

## 14. 失败处理

- 数据不达标：整个窗口 `invalid_data_window`，规则对所有版本一致；
- G1 无候选：G1 alias G0；
- G2 被拒绝：G2 alias G1；
- G3 未触发或失败：G3 alias G2；
- 代码/配置/数据语义修复：新实验 ID；
- 合格 infrastructure retry 仅按预注册政策、且结果未暴露时允许；
- 盲测负结果只能保存，不能触发同一实验的自动修复。

## 15. 证据包与 sealing

```text
Results/gold2-closed-loop/<experiment_id>/
├── historical/
│   ├── preregistration.yaml
│   ├── data_snapshot.json
│   ├── windows/W1...W5/
│   ├── aggregate/
│   └── seal.json
└── live-paper/
    ├── epochs/
    └── seals/
```

historical 一次性封存；live-paper 使用独立 append-only epoch seal。`seal.json` 由证据根目录之外的密钥签名，并把根哈希发布到外部 append-only 位置，包含签名算法、公钥指纹、可信时间和 parent seal hash。

## 16. Live-paper

历史门槛通过后，现有 Gold2 是 champion，G3 是 challenger。两者冻结源代码、参数、LEAN、环境、适配器和交易模型；使用同一不可变事件流，记录 receipt/decision/submission/ack/fill 时间和 event ID；使用独立 paper 账户、现金和持仓账本，禁止 order netting。

预注册：最少三个月、最低独立 signal episode、最大期限、唯一最终分析时点，以及仅安全用途的 interim check。不得因中间收益改变期限、候选或门槛。

paper fill 必须覆盖 bid/ask、延迟、参与率、partial/reject、停牌、涨跌停和 stale quote；否则只能称为 operational simulation。

结论分离：`historical_economic_verdict`、`live_operational_verdict`、`production_promotion_verdict`。短期 live-paper 不得把历史未证明的闭环升级为已证明。

## 17. 最终报告

报告展示：

- 每窗口 G0/G1/G2/G3 全部指标和负结果；
- G1-G0、G2-G1、G3-G2、G3-G0 增量；
- 现金和 518880 基准；
- 费用前后、原始与等波动率诊断；
- 最差窗口、单笔依赖、候选数量、失败原因；
- DSR、bootstrap、paired tests；
- 历史、live 与部署 verdict；
- 每个数字对应的 LEAN artifact/hash。

合法报告语句：

- **有效**：多窗口冻结样本外稳定提升风险调整收益，扣费后成立，live-paper 进一步通过运营验证；
- **部分有效**：明确指出哪些阶段有效、哪些应删除；
- **未证明/已否证**：软件闭环能运行，但无充分或正向经济证据，维持原 champion。

## 18. 测试要求

- Python schema、状态机、事件登记、原子写、hash/seal、指标、bootstrap、DSR、泄漏门禁和 failure injection；
- resume/idempotency、并发 writer、stale artifact、blind-open 后逻辑变化拒绝；
- C# formal trace 序列化和 order/fill/holdings reconciliation；
- proof-specific G0 与现有 Gold2 行为等价回归；
- 使用极小 synthetic 数据做一次真实 LEAN integration；
- historical seal 与 live-paper append-only seal 共存测试；
- 正式验收通过唯一 runner 外部接口，不依赖手工拼接文件。
