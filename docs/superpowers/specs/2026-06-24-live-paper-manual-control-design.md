# live-paper 策略手动启停控制（Grafana 按钮接入既有控制 API）

**日期**：2026-06-24
**状态**：设计
**涉及**：`Scripts/strategy_control_api.py`、`Scripts/soloquant_orchestrator.py`、`monitoring/grafana/dashboards/lean/barra-cne5-live-paper.json`、新增 `Results/soloquant/live-paper-control.json`
**动机**：SoloQuant 策略增多，服务器资源吃紧，需对 live-paper 策略做**手动启停**控制。控制核心（`strategy_control_api.py` 的 start/stop 端点）已存在；缺口是 stop 的"粘性"（防 orchestrator 重启）、每进程资源可见性、Grafana 按钮。

---

## 1. 硬约束（不可违背）

- **`barra-cne5-live-paper.json` 看板现有 43 个 panel、`algorithm_id` 变量、所有布局/内容/查询一字不改。** 只**新增**控制面板。
- 不改 `strategy-registry.json` schema（用独立控制状态文件）。
- 不改既有 Grafana 面板（遵循"新功能用新面板/新 measurement"约定）。
- 不做自动资源轮转（本次纯手动）。

---

## 2. 现状（已探查）

- **控制 API 已存在**：`strategy_control_api.py`（FastAPI, :5000, token 鉴权 + audit log）
  - `POST /api/strategies/{id}/start` → `start_strategy_by_config` + 启 bridge
  - `POST /api/strategies/{id}/stop` → `graceful_stop` PID + 停 bridge
  - `POST /api/strategies/{id}/restart`、`GET /api/strategies?group=`
  - status 由 `pid_alive` 派生（**非持久 desired-state**）
- **stop 不粘**：`stop_strategy` 只杀 PID，无"别再启动"标志 → orchestrator `run_live_paper` 下个 tick 会重拉。
- **Grafana 数据源**：`yesoreyeram-infinity-datasource`（uid `strategy-control-api`）已用于 `strategy-control.json` 做 GET 视图。Infinity 支持 `url_options.method: POST`。
- **button 面板插件未装**（volkovlabs-button-panel）。
- **barra 看板**：43 panel，`algorithm_id` 变量，uid `barra-cne5-live-paper`，title `Live Paper Trading`。

---

## 3. 设计

### 3.1 粘性控制状态文件（新增，独立）

`Results/soloquant/live-paper-control.json`：
```json
{
  "<strategy_id>": {
    "manual_hold": true,
    "last_action": "stop",
    "last_action_at": "2026-06-24T15:00:00+08:00",
    "last_actor": "grafana"
  }
}
```
- `manual_hold=true` 表示"操作员手动停了，orchestrator 别自动拉起"。
- 读不到/无条目 = `manual_hold=false`（默认，向后兼容）。

### 3.2 strategy_control_api.py 改动（控制 API 本职，扩展非改既有逻辑）

1. 加 `_load_control_state() / _save_control_state(strategy_id, action)` 读写上述文件。
2. `stop_strategy`：在 `graceful_stop` 成功后 → `_save_control_state(id, "stop")`（置 `manual_hold=true`）。
3. `start_strategy`：启动前 → `_save_control_state(id, "start")`（置 `manual_hold=false`）。
4. `GET /api/strategies` 响应每项增加资源字段：`cpu_percent`、`rss_mb`（读 `/proc/<pid>/stat` 累计 utime+stime 算 CPU%、`/proc/<pid>/status` VmRSS 算内存）、`manual_hold`（布尔）。
   - 既有字段（strategy_id/status/pid/bridge/config_path...）不变，仅**新增**字段。

### 3.3 orchestrator 守卫（run_live_paper，1 行守卫，默认向后兼容）

在 `Scripts/soloquant_orchestrator.py` 的 live-paper 启动循环里，启动某 serving 策略前加：
```python
if control_state.get(strategy_id, {}).get("manual_hold"):
    LOGGER.info("skip %s: manual_hold (operator stopped)", strategy_id)
    continue
```
- 默认无条目 = false → 现有行为完全不变（不破坏成熟功能）。
- 仅当操作员经 API stop 过的策略才被跳过。

### 3.4 Grafana：给 barra 看板新增控制面板（不动既有 43 panel）

**机制**：装 `volkovlabs-button-panel` 插件 + 复用既有 Infinity 数据源（uid `strategy-control-api`）做 POST。
- 在 `barra-cne5-live-paper.json` **新增 1 个 panel**（type `volkovlabs-button-panel`），置于新 row 或 dashboard 末尾空白区，**不动任何既有 panel 的 gridPos**。
- 按钮内容：依赖既有 `algorithm_id` 变量构造 URL → `POST /api/strategies/${algorithm_id}/stop` 与 `/start`，header 带 `Authorization: Bearer <STRATEGY_API_TOKEN>`（由 Infinity 数据源代理注入，复用 strategy-control.json 的鉴权配置）。
- 按钮反馈：POST 后刷新面板（Infinity 查询 `GET /api/strategies?...` 显示新 status/manual_hold）。

**既有面板保护验证**：改完 JSON 后用脚本断言 `panels` 数 ≥ 43 且原 43 个 panel 的 `(title, gridPos, type)` 集合**完全不变**。

> 备选机制（若不允许装插件）：用 Infinity 数据源 + 表格行的 **data link**（GET）+ 在 API 加 `GET /api/strategies/{id}/stop?token=...` 重定向垫片。优先 volkovlabs（真按钮体验）。

### 3.5 数据流

- 停：`Grafana Stop 按钮 → POST /api/strategies/{id}/stop → graceful_stop PID + 写 manual_hold=true → orchestrator 后续 tick 跳过 → 看板显 stopped/hold`
- 启：`Grafana Start 按钮 → POST /api/strategies/{id}/start → 清 manual_hold=false + 启动 LEAN 进程 → orchestrator 恢复正常维护`

---

## 4. 错误处理

- `manual_hold` 文件读写异常 → API 返回 500 + audit 记录；不影响既有 start/stop 杀/启进程的主流程（manual_hold 写失败仅 log warning，不阻断 stop）。
- orchestrator 读控制状态失败 → 视为 `manual_hold=false`（保守，不误跳过），log warning。
- Grafana 按钮 POST 失败（token/网络）→ button panel 显示 HTTP 错误码；API 端 audit 记录。
- 资源指标 `/proc` 读失败 → `cpu_percent/rss_mb` 返回 null，不影响其他字段。

---

## 5. 测试

1. **API 单测**：stop 后控制文件 `manual_hold=true`；start 后 `false`；GET 响应含 cpu_percent/rss_mb/manual_hold。
2. **orchestrator 守卫单测**：`manual_hold=true` 的策略被 skip；`false`/无条目正常启动。
3. **看板回归（关键）**：改完 `barra-cne5-live-paper.json` 后，断言原 43 panel 的 `(title,gridPos,type)` 集合**逐项不变**，仅新增 1 个 button panel。
4. **端到端**：Grafana 点 Stop → PID 消失 + manual_hold=true + 下个 orchestrator tick 不重启；点 Start → 恢复。
5. **资源字段**：对一个 running 策略，GET 返回非 null 的 cpu_percent/rss_mb。

---

## 6. 不在范围

- 自动资源阈值轮转（Prefect 策略 flow）。
- 改 strategy-registry.json schema。
- 改 barra 看板既有 43 panel / 布局 / algorithm_id 变量。
- strategy-control.json 看板（既有，不动）。
