# Prefect vs Grafana：SoloQuant Pipeline 启停控制方案讨论

## 背景

随着 SoloQuant pipeline 中策略数量增加，计算资源（CPU、内存、磁盘 I/O）逐渐不足。需要人工控制：
- **Pipeline 阶段级别**：策略爬取、策略实现、回测、live paper 的启停
- **策略级别**：控制哪一条量化策略的实现、回测、live paper 启停

核心问题：**控制逻辑应该放在 Prefect 还是 Grafana？**

## 现状分析

### 当前架构

```
Prefect Flow (soloquant-pipeline-tick, 每 5 分钟)
    → 14 个阶段顺序执行
    → 每阶段有间隔跳过逻辑（如爬取 8h、实现 30m）
    → LLM 后台任务通过 PID 文件管理
    → 流程状态写入 soloquant-pipeline-state.json

Grafana Strategy Control API (FastAPI, :5000)
    → 控制 live paper LEAN 进程和 bridge 脚本
    → 不与 Prefect 交互
    → 不控制 pipeline 阶段

sqctl.sh
    → 进程级启停（core/livepaper/legacy/bridge/tushare/llm）
    → 不控制 pipeline 阶段内部逻辑
```

### 当前缺口

1. **无法暂停特定阶段**：Prefect 流程只能间隔跳过，不能按需暂停
2. **无法取消运行中的后台 LLM 任务**：后台进程一旦启动，Prefect 就失去可见性
3. **无法运行时调整阶段间隔**：需要重启 Prefect deployment
4. **无 Grafana→Prefect 桥接**：现有 control API 只控制 LEAN 进程
5. **无策略级粒度控制**：不能指定"只回测策略 X"或"跳过策略 Y 的 live paper"

## 方案对比

### 方案 A：在 Prefect 中实现启停

**思路**：在 Prefect 流程中增加动态配置，通过 Prefect API 或自定义状态文件控制每个阶段/策略是否执行。

**实现方式**：
- 在 `PipelineState` JSON 中新增 `paused_stages` 和 `paused_strategies` 字段
- 每个阶段执行前检查：该阶段是否被暂停？该策略是否被跳过？
- 通过 Prefect REST API 或直接修改 JSON 文件来启停
- 在 Grafana 中通过 Infinity 数据源调用 Prefect API

**优势**：
- ✅ 控制逻辑与执行逻辑同层，判断最准确
- ✅ Prefect 天然有任务状态管理（Pending/Running/Completed/Failed/Cancelled）
- ✅ Prefect UI 已有任务取消功能
- ✅ 可以利用 Prefect 的 concurrency limits 控制资源
- ✅ 后台进程的启停可以在 monitor_background_jobs 阶段实现

**劣势**：
- ❌ Prefect API 不稳定（当前使用的是自托管版，版本更新频繁）
- ❌ Prefect 的 concurrency limits 只控制同时执行的任务数，不能暂停调度
- ❌ 后台 LLM 进程脱离 Prefect 控制，取消任务 ≠ 杀死子进程
- ❌ 修改 Prefect flow 需要重启 deployment，影响在线运行
- ❌ 策略级粒度控制需要在每个阶段的 task 函数中增加过滤逻辑，侵入性强
- ❌ Prefect UI 对自定义状态展示有限，无法直观展示"哪些策略被暂停"

### 方案 B：在 Grafana 中实现启停（扩展现有 Control API）

**思路**：扩展现有 FastAPI Control API，增加 pipeline 阶段和策略级别的启停端点，通过读写配置文件影响 Prefect 流程行为。

**实现方式**：
- 在 Control API 中新增 `/api/pipeline/stages` 端点（GET 查询、POST 启停）
- 读写 `pipeline-control.json` 配置文件，记录暂停的阶段和策略
- Prefect 流程在每次 tick 开始时读取该文件，跳过被暂停的阶段/策略
- 在 Grafana 仪表板中增加阶段和策略的启停按钮

**优势**：
- ✅ 与现有 control API 架构一致（已有 FastAPI + Infinity 数据源模式）
- ✅ 不修改 Prefect flow 核心逻辑，只在 `_should_run()` 中增加一层检查
- ✅ Grafana 是用户最常用的界面，操作直觉
- ✅ 配置文件驱动，重启 Prefect 也不丢失状态
- ✅ 策略级粒度控制灵活，配置文件可定义任意过滤规则

**劣势**：
- ❌ 需要维护额外的配置文件和 API 端点
- ❌ Prefect flow 和 Control API 之间通过文件耦合，不是进程内调用
- ❌ 无法直接取消运行中的 Prefect 任务（需要调用 Prefect API）
- ❌ 后台 LLM 进程的取消仍需通过 kill 信号

### 方案 C：混合方案——Prefect 执行控制 + Grafana 操作界面

**思路**：Grafana 作为操作界面，Control API 作为中间层，Prefect 作为执行层。三层各司其职。

**实现方式**：
- **Grafana**：展示 pipeline 状态 + 操作按钮（用户交互层）
- **Control API**：接收操作请求，写入配置 + 调用 Prefect API（中间层）
- **Prefect**：读取配置，执行/跳过阶段，管理并发（执行层）

**分层职责**：
```
Grafana Dashboard (操作界面)
    ↓ 按钮点击
Control API :5000 (中间层)
    ├── 读写 pipeline-control.json (持久化启停配置)
    ├── 调用 Prefect REST API (取消运行中任务)
    └── 直接 kill 后台进程 (紧急停止)
    ↓
Prefect Flow (执行层)
    ├── 每次tick读取 pipeline-control.json
    ├── 跳过暂停的阶段/策略
    └── 执行允许的阶段/策略
```

**优势**：
- ✅ 充分利用各层优势：Grafana 的可视化、API 的灵活性、Prefect 的调度能力
- ✅ 解耦：Grafana 不直接调用 Prefect API，通过 Control API 隔离
- ✅ 配置文件驱动：即使 Prefect 重启，启停状态也不丢失
- ✅ 可以调用 Prefect API 取消运行中的任务
- ✅ 可以直接 kill 后台进程处理紧急情况
- ✅ 策略级粒度控制自然落在配置文件中

**劣势**：
- ❌ 三层架构复杂度最高
- ❌ 需要维护 Control API ↔ Prefect API 的交互逻辑
- ❌ Prefect API 版本兼容性需要关注

## 推荐方案：方案 C（混合方案）

**理由**：

1. **关注点分离**：Grafana 擅长展示和操作界面，Prefect 擅长调度和执行，Control API 擅长桥接。让每层做最擅长的事。

2. **为什么不选方案 A（纯 Prefect）**：
   - Prefect 是调度引擎，不是控制界面。用户不应该通过 Prefect UI 来启停策略——Prefect UI 是给开发者用的，不是给量化交易员用的。
   - Prefect flow 的修改需要重启 deployment，在线运行时不方便。
   - 策略级粒度控制侵入 Prefect 核心逻辑太多。

3. **为什么不选方案 B（纯 Grafana）**：
   - 无法取消运行中的 Prefect 任务——只能等待自然结束。
   - 无法利用 Prefect 的 concurrency limits 和 retry 机制。
   - 对后台 LLM 进程的控制能力有限。

4. **方案 C 的关键设计**：
   - `pipeline-control.json` 是单一真相来源，Prefect 和 Control API 都读它
   - Control API 是唯一的写入者，避免竞争条件
   - Prefect flow 只需在 `_should_run()` 中增加一行检查，侵入性最低
   - Grafana 仪表板复用现有 Infinity 数据源模式

## 配置文件设计

`Results/soloquant/pipeline-control.json`:

```json
{
  "paused_stages": [
    "crawl_research",
    "data_driven_crawl"
  ],
  "paused_strategies": {
    "reproduce_one": ["2002-04304-timing-excess-returns-*"],
    "optimize_backtests": ["AShareBarraCNE5V2*"],
    "run_live_paper": ["*momentum*"]
  },
  "stage_config": {
    "reproduce_one": {
      "max_concurrent": 2,
      "enabled": true
    },
    "optimize_backtests": {
      "max_concurrent": 1,
      "enabled": true
    }
  },
  "updated_at_utc": "2026-06-18T12:00:00Z",
  "updated_by": "grafana"
}
```

## 新增 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/pipeline/stages` | GET | 列出所有阶段及启停状态 |
| `/api/pipeline/stages/{stage}/pause` | POST | 暂停阶段 |
| `/api/pipeline/stages/{stage}/resume` | POST | 恢复阶段 |
| `/api/pipeline/strategies` | GET | 列出策略级启停配置 |
| `/api/pipeline/strategies/{stage}/{strategy}/pause` | POST | 暂停某阶段中特定策略 |
| `/api/pipeline/strategies/{stage}/{strategy}/resume` | POST | 恢复特定策略 |
| `/api/pipeline/background-jobs` | GET | 列出运行中后台任务 |
| `/api/pipeline/background-jobs/{job}/cancel` | POST | 取消运行中后台任务（kill + 清理 PID） |

## Prefect 侧修改（最小侵入）

在 `_should_run()` 函数中增加一行检查：

```python
def _should_run(stage_name, ...):
    # 现有逻辑：间隔检查
    if not PipelineState.should_run(stage_name, interval):
        return False

    # 新增：检查 pipeline-control.json
    control = load_pipeline_control()
    if stage_name in control.get("paused_stages", []):
        return False

    return True
```

在策略级阶段（如 `reproduce_one`、`run_live_paper`）中增加过滤：

```python
def reproduce_one_task(...):
    control = load_pipeline_control()
    paused = control.get("paused_strategies", {}).get("reproduce_one", [])
    # 过滤掉被暂停的策略
    candidates = [s for s in candidates if not matches_any_pattern(s, paused)]
    # ... 继续执行
```

## 结论

| 维度 | 方案 A（纯 Prefect） | 方案 B（纯 Grafana） | 方案 C（混合） |
|------|---------------------|---------------------|---------------|
| 用户友好度 | 低（Prefect UI 面向开发者） | 高（Grafana 直观） | 高（Grafana 直观） |
| 实现复杂度 | 中（侵入 Prefect 核心） | 中（需扩展 Control API） | 高（三层） |
| 运行时灵活性 | 低（需重启 deployment） | 中（文件驱动） | 高（文件 + API） |
| 取消运行中任务 | 是（Prefect 原生） | 否（只能等） | 是（API 调用 Prefect） |
| 策略级粒度 | 难（侵入性强） | 易（配置过滤） | 易（配置过滤） |
| 状态持久化 | Prefect DB | 配置文件 | 配置文件 |
| 侵入性 | 高（修改 flow 核心） | 低（只加检查） | 低（只加检查） |

**最终推荐：方案 C（混合方案）**。虽然复杂度最高，但充分利用了各层优势，侵入性最低，灵活性最高，且与现有架构一致。
