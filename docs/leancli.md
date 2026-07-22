# QuantConnect CLI 可视化界面查看指南

本文档介绍如何使用 QuantConnect Lean CLI 查看回测和 Live Paper 的可视化界面。

---

## 安装 Lean CLI

### 前提条件

- Python 3.8+
- Docker Desktop
- QuantConnect 付费组织账号

### 安装命令

```bash
pip install lean
```

### 验证安装

```bash
lean --version
```

---

## 回测可视化

### 方法一：运行回测并自动打开报告

```bash
# 运行回测并自动生成 HTML 报告
lean backtest "path/to/your/project" --output ./backtests
```

### 方法二：单独生成报告

```bash
# 使用回测结果生成 HTML 报告
lean report --backtest-results ./backtests/result.json --report-destination ./report.html
```

### 报告内容

生成的 HTML 报告包含：

| 章节 | 说明 |
|------|------|
| Strategy Equity | 权益曲线图 |
| Benchmark | 基准收益对比 |
| Drawdown | 回撤曲线 |
| Annual Return | 年化收益 |
| Sharpe Ratio | 夏普比率 |
| Sortino Ratio | 索提诺比率 |
| Win Rate | 胜率 |
| Profit/Loss | 盈亏分布 |
| Trade List | 交易明细 |

### 命令选项

```bash
# 完整选项示例
lean report \
  --backtest-results ./backtests/result.json \
  --report-destination ./my-report.html \
  --strategy-name "My Strategy" \
  --strategy-version "1.0.0" \
  --strategy-description "这是一个均值回归策略" \
  --pdf  # 同时生成 PDF
```

### 常用选项

| 选项 | 说明 |
|------|------|
| `--backtest-results` | 回测结果 JSON 文件路径 |
| `--live-results` | Live 结果文件路径 |
| `--report-destination` | 输出报告路径（默认 ./report.html） |
| `--strategy-name` | 策略名称 |
| `--strategy-version` | 策略版本 |
| `--strategy-description` | 策略描述 |
| `--pdf` | 同时生成 PDF 版本 |
| `--overwrite` | 覆盖已存在的报告 |

---

## Live Paper 可视化

### 方法一：启动 Live 并实时查看

```bash
# 启动 Live Paper 交易
lean live deploy "path/to/your/project" \
  --brokerage "Paper Trading" \
  --data-provider-historical QuantConnect
```

这将：
1. 在 Docker 中启动 LEAN 引擎
2. 实时输出日志到终端
3. 通过 ZeroMQ 推送实时数据

### 方法二：查看 Live 结果

```bash
# Live 交易结束后生成报告
lean report \
  --live-results ./live-results.json \
  --report-destination ./live-report.html
```

### 方法三：比较回测与 Live

```bash
# 生成包含回测和 Live 对比的报告
lean report \
  --backtest-results ./backtests/result.json \
  --live-results ./live-results.json \
  --report-destination ./comparison.html
```

---

## 实时监控

### 本地实时监控（需要配置）

Lean CLI 运行 Live 时会通过 ZeroMQ 推送实时数据到本地端口。

#### 1. 配置端口

```json
// lean.json
{
  "desktop-http-port": 19999
}
```

#### 2. 使用 Python 订阅数据

```python
import zmq
import json
import threading

def listen_live_data(port=19999):
    context = zmq.Context()
    socket = context.socket(zmq.PULL)
    socket.connect(f"tcp://localhost:{port}")
    
    print(f"监听端口 {port}...")
    
    while True:
        try:
            message = socket.recv_string()
            data = json.loads(message)
            
            # 处理实时数据
            if data.get('type') == 'live-result':
                results = data.get('results', {})
                holdings = results.get('holdings', {})
                charts = results.get('charts', {})
                print(f"持仓: {holdings}")
                print(f"权益: {charts.get('Strategy Equity', {})}")
                
        except Exception as e:
            print(f"错误: {e}")

# 启动监听线程
thread = threading.Thread(target=listen_live_data, daemon=True)
thread.start()

# 保持主线程运行
input("按 Enter 退出...\n")
```

---

## 常用命令速查

### 回测命令

```bash
# 基本回测
lean backtest "path/to/project"

# 带输出目录
lean backtest "path/to/project" --output ./my-backtests

# 带数据下载
lean backtest "path/to/project" --download-data

# 带参数
lean backtest "path/to/project" --parameter symbol SPY --parameter period 20
```

### Live 命令

```bash
# 启动 Live Paper
lean live deploy "path/to/project" --brokerage "Paper Trading"

# 启动带实时监控
lean live deploy "path/to/project" \
  --brokerage "Paper Trading" \
  --data-provider-historical QuantConnect

# 查看运行中的 Live
lean live status

# 停止 Live
lean live stop "project-name"

# 平仓
lean liquidate "project-name"
```

### 报告命令

```bash
# 生成回测报告
lean report --backtest-results ./result.json

# 生成 Live 报告
lean report --live-results ./live-result.json

# 生成对比报告
lean report --backtest-results ./backtest.json --live-results ./live.json

# 生成 PDF
lean report --backtest-results ./result.json --pdf
```

---

## 输出文件位置

### 回测输出

```
project/
└── backtests/
    └── 2024-01-15_10-30-45/
        ├── result.json           # 完整结果
        ├── orders.json           # 订单列表
        └── charts/               # 图表数据
```

### Live 输出

```
project/
└── live/
    └── 2024-01-15_10-30-45/
        ├── result.json           # 实时结果
        └── logs/                # 日志文件
```

---

## 常见问题

### Q: 报告生成需要付费吗？

A: 是的，Lean CLI 需要 QuantConnect 付费组织账号才能使用。

### Q: 可以离线生成报告吗？

A: 可以，只需提供回测/ Live 的 result.json 文件即可离线生成。

### Q: 如何自定义报告样式？

A: 使用 `--css` 选项指定自定义 CSS 文件：

```bash
lean report --backtest-results ./result.json --css ./custom.css
```

### Q: Live 监控可以不用 CLI 吗？

A: 可以，自行编写 ZeroMQ 订阅程序连接到本地端口。

---

## 总结

| 场景 | 命令 | 可视化方式 |
|------|------|------------|
| 回测 | `lean backtest` → `lean report` | HTML 报告 |
| Live Paper | `lean live deploy` | 终端日志 + ZeroMQ |
| Live 报告 | `lean report --live-results` | HTML 报告 |
| 对比 | `lean report --backtest --live` | 对比报告 |
