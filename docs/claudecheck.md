# SoloQuant 代码审查报告

审查日期：2026-05-12

---

## 一、最核心的架构问题：两条策略路径的定位模糊

系统存在两条并行的策略生成路径，但它们的关系在设计上没有明确区分：

**Path A（模板路径）**：`crawl → GLM筛选 → prepare_reproduction → materialize_reproduction_summaries` → 用 `AShareT1MomentumAlgorithm` / `AShareT1MeanReversionAlgorithm` 模板生成策略包

**Path B（代码生成路径）**：`crawl → GLM筛选 → prepare_reproduction → generate_strategy_implementations（GLM生成C#代码）→ compile → materialize_generated_strategy_implementations` → 用 `SoloQuantGenerated*Algorithm` 生成策略包

**问题所在：**

1. **pipeline_runner 只走 Path B，job_runner 只走 Path A**。当前注册表里的 2 个策略全部来自 Path A（都是 `AShareT1MomentumAlgorithm`），Path B 从未成功产出过策略。这不是 bug，但意味着系统的核心价值主张——"GLM 根据爬取内容生成新策略"——在实际运行中从未被验证过。

2. **Path A 的模板推断逻辑过于粗糙**：`infer_strategy_template_from_reproduction_summary` 只用关键词匹配决定用动量还是均值回归模板，所有不含"反转/均值回归"关键词的策略都默认用动量模板。当前两个注册策略都是动量模板，说明这个推断几乎没有区分度。

3. **Path B 的 GLM 代码生成没有质量保障**：`validate_generated_strategy_code` 只检查类名格式、命名空间、禁用词，不验证代码能否编译、逻辑是否合理。GLM 生成的 C# 代码直接写入文件，compile 阶段才发现编译错误，但此时 pipeline 已经走到 compile 阶段，错误会导致整个 pipeline 停止（compile 不在 degraded-continue 集合里）。

---

## 二、策略优化逻辑是参数扰动，不是真正的优化

`build_parameter_variants` 和 `build_generated_strategy_parameter_variants` 的"优化"是固定的参数扰动：

```python
# Path A: 动量模板
learned_v1 = {lookback: 60, entry_threshold: 0.08, max_positions: 8}
learned_v2 = {lookback: 120, entry_threshold: 0.12, max_positions: 12}

# Path B: 通用
learned_v1 = {lookback: baseline//2, max_positions: baseline-2}
learned_v2 = {lookback: baseline*2, max_positions: baseline+2}
```

这不是"根据回测结果优化2个版本"，而是预设了3个参数组合跑回测，选最好的。需求里说的"根据该量化策略回测结果，至少优化2个版本"，应该是先看 baseline 回测结果，再有针对性地调整参数。当前实现是盲目扰动，与需求描述有偏差。

---

## 三、lifecycle 评分依赖 live paper 产出文件，但 live paper 从未真正运行

`update_strategy_lifecycle` 从 `live-paper-config` 指向的 `summary-file` 读取 `live_score`。但 live paper 是后台进程，summary 文件只有在 LEAN 运行结束后才写入。当前两个策略的 live paper 进程状态未知，如果 summary 文件不存在，`live_score` 会 fallback 到 `best_score`，导致所有策略都被判定为 `serving`（因为 `serving_score_threshold` 是 0.0）。这意味着 lifecycle 管理在 live paper 没有真实运行数据时是空转的。

---

## 四、数据流中的前视偏差风险未被端到端验证

需求明确要求防止前视偏差。`_base_lean_config` 里设置了 `history-provider: TushareHistoryProvider`，但：

- 生成的策略代码（Path B）由 GLM 生成，GLM 不了解 `TushareHistoryProvider` 的约束，可能生成直接读取文件的代码
- `validate_generated_strategy_code` 不检查是否有直接文件读取（只禁了 `File.Delete`，没禁 `File.ReadAllText`、`StreamReader` 等）
- 缺失字段的随机占位值（`materialize_missing_data_placeholders`）会被写入 JSON 文件，但没有机制阻止策略在回测中读取这些占位值并当作真实数据使用

---

## 五、job_runner 与 pipeline_runner 的调度存在竞争条件

`job_runner` 里有 `soloquant-full-auto-pipeline`（每5分钟，long-running）和 `soloquant-optimize`（每天4点）两个 job。`pipeline_runner` 的 `optimize_backtests` 阶段也会调用 `--optimize-strategies`。两者都会写 `strategy-registry.json`，没有任何锁机制，存在并发写入风险。

---

## 六、功能遗漏：财经情报没有反馈到策略选择

需求里说财经情报用于"指导投资"，但当前实现中：

- 财经情报被爬取、GLM 分析、构建事件图、导出 InfluxDB
- 但事件图和情报分析结果**没有任何路径影响策略的选择、参数调整或 live paper 的仓位**

这是一个功能性缺口：情报系统和策略执行系统是完全解耦的，情报只用于 Grafana 展示，没有形成闭环。

---

## 七、琐碎问题

- `glm-stage-timeout-seconds: 30` 与 GLM 实际超时 180s 冲突，导致 `--prepare-reproduction` 和 `--analyze-finance-intelligence` 在 30 秒后被强制 kill（returncode 124），这是昨天 pipeline 状态为 `degraded` 的直接原因。修复：改为 240。
- `build_finance_event_graph` 被合并在 `prepare_reproduction` 阶段，但它本身不需要 GLM，若 GLM 超时导致 prepare_reproduction 失败，事件图构建也会被跳过。
- `export_price_ohlc` 不在 pipeline 的 `export_influx` stage 中，是独立 job，文档未说明此分离设计。
- Grafana dashboard 更新依赖手动运行 `install_assets.sh`，没有程序化自动更新，`GRAFANA_TOKEN` 在 orchestrator 中只被存储未被使用。
- 策略评分 `_score_variant` 用 `total_return - abs(max_drawdown)`，没有考虑 Sharpe/Calmar ratio。
- `missing-data.jsonl` 中的缺失字段没有自动重试机制。

---

## 八、优先级汇总

| 优先级 | 问题 | 影响 |
|---|---|---|
| 高 | `glm-stage-timeout-seconds` 改为 240 | 当前 pipeline 无法正常运行 Path B |
| 高 | `validate_generated_strategy_code` 加入文件读取禁用词 | 防止生成代码绕过 history provider，引入前视偏差 |
| 高 | 明确 Path A / Path B 的职责边界，在文档和代码中体现 | 架构意图不清，维护困难 |
| 中 | `build_parameter_variants` 改为读取 baseline 回测结果后再决定扰动方向 | 让"优化"名副其实 |
| 中 | `build_finance_event_graph` 从 `prepare_reproduction` 中解耦 | 避免 GLM 超时连带跳过事件图构建 |
| 中 | 财经情报→策略选择的闭环 | 核心功能缺口，情报系统目前只用于展示 |
| 低 | `strategy-registry.json` 并发写入加锁 | 低频竞争，但数据损坏风险存在 |
| 低 | Grafana dashboard 自动更新 | 当前为手动步骤 |
| 低 | 策略评分加入 Sharpe/Calmar | 评分质量提升 |
