# CLI 手动启停 live-paper 量化策略

**日期**：2026-06-24
**涉及**：`Scripts/strategy_control_api.py`（FastAPI :5000）、`Results/soloquant/live-paper-control.json`

## 三条核心命令

```bash
cd /home/project/hope/Lean
TOKEN=$(cat Results/soloquant/.strategy-api-token)

# 1) 列出全部 live-paper 策略（strategy_id / 状态 / pid / 内存 / 粘性）
curl -s "http://localhost:5000/api/strategies?group=soloquant-lp" | python3 -m json.tool

# 2) 停止特定策略（粘性：pipeline 后续 tick 不会自动重启，真正释放资源）
curl -s -X POST "http://localhost:5000/api/strategies/<SID>/stop" -H "Authorization: Bearer $TOKEN"

# 3) 重新启动（清除粘性，pipeline 恢复维护）
curl -s -X POST "http://localhost:5000/api/strategies/<SID>/start" -H "Authorization: Bearer $TOKEN"
```

`<SID>` 替换为命令 1 输出里的 `strategy_id`。

## 一次停掉全部 running 策略（释放内存）

```bash
curl -s "http://localhost:5000/api/strategies?group=soloquant-lp" \
 | python3 -c "import json,sys;[print(s['strategy_id']) for s in json.load(sys.stdin)['strategies'] if s['status']=='running']" \
 | while read sid; do
     curl -s -X POST "http://localhost:5000/api/strategies/$sid/stop" -H "Authorization: Bearer $TOKEN" >/dev/null
     echo "stopped $sid"
   done
```

## 全部恢复运行（清掉所有粘性标志）

```bash
echo '{}' > Results/soloquant/live-paper-control.json
```

下一个 pipeline tick 会把所有 `serving` 且不处于 `retired` 的策略重新拉起。

## 停单一策略示例

```bash
curl -s -X POST "http://localhost:5000/api/strategies/2002-04304-timing-excess-returns-a-cross-universe-approach-to-alpha/stop" \
  -H "Authorization: Bearer $TOKEN"
```

返回 `{"success":true,"action":"stop",...}` 即成功。

## 机制说明

| 概念 | 说明 |
|------|------|
| **粘性 stop** | `POST /stop` 在杀进程后写入 `manual_hold=true` 到 `Results/soloquant/live-paper-control.json`。pipeline 的 `run_live_paper` 阶段在启动策略前会检查该标志——若为 true 则跳过不拉起（sticky stop）。 |
| **start 清除** | `POST /start` 会将 `manual_hold` 设为 `false`，随后启动 LEAN 进程，pipeline 恢复维护。 |
| **向后兼容** | 没有 `live-paper-control.json` 文件（或其中无某 strategy_id 条目）时，`manual_hold` 视为 `false`——行为与改之前完全相同。 |
| **资源可见性** | `GET /api/strategies` 每项含每进程 `cpu_percent`（瞬时，/proc 双采样）、`rss_mb`、`manual_hold`。 |
