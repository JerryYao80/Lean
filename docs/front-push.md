# LEAN WebSocket 实时推送前端界面

本文档介绍 LEAN 通过 WebSocket（TCP）实时推送数据的前端界面选项及条件要求。

---

## 推送机制概述

LEAN 使用 **NetMQ (ZeroMQ)** 通过 TCP 协议实时推送数据，而非传统 WebSocket。

### 推送架构

```
LEAN Engine
    ↓
MessagingHandler (ZeroMQ TCP)
    ↓
{Protocol} → 前端界面
```

### 两种消息处理器

| 处理器 | 说明 | 用途 |
|--------|------|------|
| `Messaging` | 写入本地日志 | 本地回测/开发 |
| `StreamingMessageHandler` | TCP 推送 | 实时推送至前端 |

---

## 可用的前端界面

### 1. QuantConnect Cloud (官方云端)

**推送地址**：QuantConnect 官方云端服务器

**配置**：
```json
{
  "messaging-handler": "QuantConnect.Messaging.Messaging"
}
```

**条件**：
- 需要 QuantConnect.com 账号
- 需要有效的 API Token
- 在云端运行算法

**推送内容**：完整实时数据（订单、持仓、图表、统计等）

---

### 2. Lean CLI (本地命令行)

**推送地址**：`localhost:{port}`（默认 19999）

**配置**：
```json
{
  "messaging-handler": "QuantConnect.Messaging.StreamingMessageHandler",
  "desktop-http-port": "19999"
}
```

**条件**：
- 安装 Lean CLI：`pip install lean`
- 使用 `lean live` 命令运行
- 本地启动 GUI 或 CLI 监控

---

### 3. 自定义前端应用

可以通过 ZeroMQ 订阅实时数据：

**配置**：
```json
{
  "messaging-handler": "QuantConnect.Messaging.StreamingMessageHandler",
  "desktop-http-port": "19999"
}
```

**连接示例 (Python)**：

```python
import zmq
import json

# 连接 LEAN ZeroMQ 端口
context = zmq.Context()
socket = context.socket(zmq.PULL)
socket.connect("tcp://localhost:19999")

while True:
    message = socket.recv_string()
    data = json.loads(message)
    print(data)  # 处理接收到的数据
```

---

### 4. Lean GUI (桌面应用)

LEAN 官方提供的桌面监控应用：

**条件**：
- 下载 Lean 桌面客户端
- 配置 `desktop-http-port` 与算法一致
- 启动桌面应用并连接

---

## 推送条件要求

### 必需配置

#### 1. 启用 StreamingMessageHandler

```json
{
  "messaging-handler": "QuantConnect.Messaging.StreamingMessageHandler",
  "desktop-http-port": "19999"
}
```

#### 2. 设置端口

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `desktop-http-port` | `19999` | ZeroMQ 推送端口 |

#### 3. 算法类型

必须使用 **实时算法**（Live Mode）：

```json
{
  "environment": "live-paper"  // 或 "live-interactive"
}
```

---

## 推送的数据类型

### 实时推送数据

| 数据类型 | 说明 | 频率 |
|----------|------|------|
| `Charts` | 图表增量更新 | ~3秒 |
| `Holdings` | 当前持仓 | ~3秒 |
| `Orders` | 订单状态 | 实时 |
| `OrderEvents` | 订单事件 | 实时 |
| `RuntimeStatistics` | 运行时统计 | ~3秒 |
| `ServerStatistics` | 服务器状态 | ~1分钟 |

### 消息类型 (Packet Types)

```csharp
// Messaging.cs 中定义的消息类型
PacketType.Debug        // 调试消息
PacketType.SystemDebug  // 系统调试
PacketType.Log         // 日志消息
PacketType.RuntimeError // 运行时错误
PacketType.HandledError // 处理过的错误
PacketType.LiveResult // 实时结果
PacketType.AlgorithmStatus // 算法状态
PacketType.OrderEvent  // 订单事件
```

---

## 本地开发配置

### 最小配置示例

```json
{
  "environment": "live-paper",
  "algorithm-type-name": "MyAlgorithm",
  "algorithm-language": "CSharp",
  "algorithm-location": "./Algorithm.dll",
  "live-mode": true,
  "live-mode-brokerage": "PaperBrokerage",
  "messaging-handler": "QuantConnect.Messaging.StreamingMessageHandler",
  "desktop-http-port": "19999",
  "result-handler": "QuantConnect.Lean.Engine.Results.LiveTradingResultHandler"
}
```

### 启动命令

```bash
# 使用默认端口
dotnet run --project Launcher

# 或指定配置
dotnet run --project Launcher --config config.json
```

### 接收数据 (Python 示例)

```python
import zmq
import threading
import json

def listen_for_messages(port=19999):
    context = zmq.Context()
    socket = context.socket(zmq.PULL)
    socket.connect(f"tcp://localhost:{port}")
    
    while True:
        try:
            message = socket.recv_string()
            data = json.loads(message)
            
            # 根据消息类型处理
            packet_type = data.get('type', '')
            
            if packet_type == 'live-result':
                handle_live_result(data)
            elif packet_type == 'debug':
                handle_debug(data)
            # ... 其他类型
                
        except Exception as e:
            print(f"Error: {e}")

def handle_live_result(data):
    result = data.get('results', {})
    charts = result.get('charts', {})
    holdings = result.get('holdings', {})
    orders = result.get('orders', {})
    print(f"Equity: {charts.get('Strategy Equity', {})}")
    print(f"Holdings: {holdings}")

# 启动监听线程
threading.Thread(target=listen_for_messages, daemon=True).start()
```

---

## 消息格式

### LiveResultPacket 结构

```json
{
  "type": "live-result",
  "results": {
    "charts": {
      "Strategy Equity": { ... },
      "Benchmark": { ... },
      "Drawdown": { ... }
    },
    "orders": { ... },
    "holdings": {
      "SPY": {
        "symbol": "SPY",
        "quantity": 100,
        "averagePrice": 450.50,
        "marketValue": 45050.00,
        "unrealizedProfit": 500.00
      }
    },
    "cashbook": { ... },
    "statistics": { ... },
    "runtimeStatistics": {
      "Equity": "$100,500.00",
      "Free Cash": "$95,000.00",
      "Total Unrealized Profit": "$500.00"
    }
  }
}
```

---

## 总结

| 前端界面 | 必需条件 | 复杂度 |
|----------|----------|--------|
| QuantConnect Cloud | 账号 + API Token | 低 |
| Lean CLI | 安装 lean 库 | 低 |
| 自定义应用 | ZeroMQ 订阅代码 | 中 |
| Lean GUI | 下载桌面客户端 | 低 |

**核心要求**：
1. 配置 `StreamingMessageHandler`
2. 设置 `desktop-http-port`
3. 使用 Live Mode 运行
4. 前端/客户端订阅对应端口
