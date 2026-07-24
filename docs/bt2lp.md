# SoloQuant 策略生命周期：从回测到 Live Paper

## 状态机

| 状态 | 含义 |
|------|------|
| `candidate` | 已回测，尚未上线 |
| `serving` | 服役中，正在 live paper |
| `retired` | 已除役，被降级淘汰 |

## 流程（4步）

### 1. 变体生成 + 配置生成（`materialize_variants`）

`soloquant_orchestrator.py` 为每个策略生成 3 个变体（baseline, learned-v1, learned-v2），同时生成两套 LEAN 配置：

- `config-backtest.json` — environment=backtesting
- `config-live-paper.json` — environment=live-paper, live-mode=true, PaperBrokerage, TushareDataQueue

### 2. 回测优化（`optimize_backtests`）

用 LEAN 运行每个变体的回测配置，评分公式为 `total_return - abs(max_drawdown)`，选出最优变体。更新 strategy-registry.json，status=`candidate`。

### 3. 生命周期更新（`update_lifecycle`）

`soloquant_pipeline_runner.py:366-446` 执行状态转换：

- **candidate → serving**：`live_score >= serving_score_threshold`（默认 0.0）且未降级
- **serving → retired**：`best_score - live_score > abs(best_score) * 25%`（性能退化超 25%）
- retired 策略不会被重新启动

### 4. 启动 Live Paper（`run_live_paper`）

`soloquant_pipeline_runner.py:460-538`：

1. 读取 strategy-registry.json
2. 过滤掉 `retired` 和无 `live-paper-config` 的策略
3. 检查 PID 文件（`Results/soloquant/live-paper-processes/*.pid.json`）避免重复启动
4. 启动 LEAN 进程：
   ```bash
   dotnet QuantConnect.Lean.Launcher.dll --config <config-live-paper.json>
   ```
5. 写入 PID 文件跟踪进程

## 触发方式

**自动**：pipeline daemon 每 5 分钟循环，按 stage 间隔调度执行
```bash
python Scripts/soloquant_pipeline_runner.py --daemon --poll-seconds 300
```

**手动**：
```bash
python Scripts/soloquant_orchestrator.py --optimize-strategies
python Scripts/soloquant_orchestrator.py --run-live-paper
```

## 上线前检查清单

- [ ] 编译通过
- [ ] 至少 3 个变体
- [ ] 回测成功
- [ ] 评分达标
- [ ] 未 retired
- [ ] 进程未运行（PID 文件检查）
- [ ] 市场快照数据就绪
