# SoloQuant Pipeline 可视化与任务管理

## 问题现状

随着策略增多、阶段细分，`soloquant_pipeline_runner.py` 的 `while True` 循环式任务管理已成为黑盒：

- 14 个阶段的依赖关系隐含在代码执行顺序中，无法直观看到
- 阶段状态（成功/失败/跳过）只有日志文本，无可视化
- 任务失败后无自动重试，需人工检查日志
- 并发控制（LLM 最多 N 个并行）靠手动 Popen + PID 文件
- 定时调度逻辑手写 cron 解析器，无调度 UI
- 重启后状态恢复依赖 JSON 文件，无持久化保证

## 技术选型分析

### Prometheus？— 不适合

Prometheus 是监控/告警系统，解决的是「指标采集和报警」，不是「任务编排和调度」。

| 需求 | Prometheus | 应该用 |
|------|:----------:|--------|
| DAG 可视化（阶段依赖关系） | ❌ | Prefect / Dagster |
| 任务启停/重试/跳过 | ❌ | Prefect / Dagster |
| 任务状态持久化（成功/失败/排队） | ❌ | Prefect / Dagster |
| 定时调度（cron） | ❌ | Prefect / Dagster |
| 任务日志查看 | ❌ | Prefect / Dagster |
| CPU/内存/延迟指标 | ✅ | Prometheus（但已有 InfluxDB） |

且项目已有 InfluxDB + Grafana 做指标监控，Prometheus 完全重叠。

### Grafana？— 能做一半

Grafana 可以做**可视化展示和告警**，但做不了**编排和调度**。

#### Grafana 能做的（可视化 + 告警）

已有 InfluxDB + Grafana，只需把每个阶段的状态写入 InfluxDB：

```bash
# 每个 stage 完成后写入
influx write -b quant 'soloquant_stage,stage=prepare_iv_data status=ok,duration_s=42.3,run_date=2026-06-11'
influx write -b quant 'soloquant_stage,stage=reproduce_one status=failed,duration_s=120.5,error="compile_fail"'
```

Grafana 就能实现：

| 能力 | 实现方式 |
|------|----------|
| 阶段状态表（✅/❌/⏳） | Table panel + status 字段颜色映射 |
| 执行时间线 | Timeline panel（每个 stage 一行） |
| 失败告警 | Alert rule → 微信/邮件 |
| 历史趋势 | Time series（每个 stage 耗时） |
| 手动触发 | Button panel → 调 API |

这是现有 dashboards 的延伸，**成本很低**。

#### Grafana 做不到的（编排 + 调度）

| 需求 | Grafana | 原因 |
|------|:-------:|------|
| DAG 依赖图 | ❌ | 没有图执行引擎 |
| 任务失败自动重试 | ❌ | 没有 retry 语义 |
| 条件跳过（interval 未到则跳） | ❌ | 没有条件分支 |
| 任务间数据传递 | ❌ | 没有参数流 |
| 并发控制（LLM 最多 3 个并行） | ❌ | 没有并发池 |
| 状态持久化恢复 | ❌ | 重启后丢失 |

这些必须由 Python 侧的编排引擎负责。

### Prefect — 推荐方案

Prefect 是 Python 原生的工作流编排框架，自托管，与现有代码兼容性好：

```python
# 现在的代码（黑盒 while True 循环）
while True:
    prepare_iv_data(config)
    reproduce_one(config)
    optimize_backtests(config)
    ...

# Prefect 改造后（每个阶段变为可视化任务）
from prefect import flow, task

@task(retries=2, retry_delay_seconds=300)
def prepare_iv_data(config):
    ...

@task
def reproduce_one(config):
    ...

@flow
def soloquant_pipeline():
    iv = prepare_iv_data(config)
    reproduced = reproduce_one.wait_for(iv)
    optimized = optimize_backtests.wait_for(reproduced)
    ...
```

Prefect 自带 Web UI，提供：

- DAG 依赖图可视化
- 点击任意任务查看日志/状态/重试
- 定时调度（替代手写 cron 解析器）
- 任务失败自动重试/告警
- 完全自托管：`pip install prefect && prefect server start`

## 推荐架构：两层分离

```
┌─────────────────────────────────────────┐
│              Grafana (展示层)             │
│  阶段状态表 · 时间线 · 耗时趋势 · 告警    │
│              ↑ 写入状态                    │
├─────────────────────────────────────────┤
│          Prefect (编排层)                 │
│  DAG依赖 · 重试 · 调度 · 并发 · 日志      │
│              ↑ 替代 while True            │
├─────────────────────────────────────────┤
│         现有脚本 (执行层)                  │
│  prepare_iv_data · reproduce_one · ...   │
└─────────────────────────────────────────┘
```

### 各层职责

| 层 | 工具 | 职责 | 不负责 |
|----|------|------|--------|
| 展示层 | Grafana + InfluxDB | 状态表、时间线、趋势图、告警 | 编排、调度、重试 |
| 编排层 | Prefect | DAG 依赖、重试、调度、并发、日志 | 指标采集、图表展示 |
| 执行层 | 现有 Python 脚本 | 实际业务逻辑 | 调度、状态管理 |

### 改造路径

1. **Grafana 增强层**（成本最低，立即见效）
   - 在每个 stage 完成时写入 InfluxDB status 指标
   - 新建 Pipeline Stage Status dashboard
   - 配置失败告警

2. **Prefect 编排层**（渐进式替换 `while True`）
   - 将每个 stage 方法包装为 `@task`
   - 将 pipeline 主循环改为 `@flow`
   - 用 Prefect 调度替代手写 cron
   - 利用 Prefect UI 做 DAG 可视化和日志查看

3. **执行层不变**
   - 现有脚本（`export_ashare_implied_volatility_data.py` 等）基本不用改
   - 只是被 Prefect task 函数包装调用

### 为什么是 Prefect 而不是 Dagster/Temporal

| 维度 | Prefect | Dagster | Temporal |
|------|---------|---------|----------|
| Python 原生 | ✅ | ✅ | ❌（Go 核心，Python SDK） |
| 自托管 | ✅ | ✅ | ✅ |
| 改造成本 | 低（加装饰器） | 中（需定义 asset） | 高（需写 proto） |
| 动态 DAG | ✅ | 弱（偏静态 asset） | ✅ |
| 适合现有 while True 替换 | ✅ 自然映射 | ❌ 范式不同 | ⚠️ 过重 |
| 社区活跃度 | 高 | 高 | 高 |

Prefect 改造成本最低：现有 `while True` 循环中的每个 stage 方法加个 `@task` 装饰器，主循环加个 `@flow`，就完成了核心迁移。
