# Termux + Debian Claude Code 音频通知系统设计

**日期**: 2026-06-26
**状态**: 设计草案

## 1. 背景与目标

### 1.1 场景描述

用户在 Android 手机上通过 Termux 使用 mosh 连接到 Debian 虚拟机，Debian 上运行 Claude Code。需要通过不同声音告知 Claude Code 的运行状态。

**技术约束**:
- Termux → Debian: 单向 mosh/SSH 连接
- Debian 无法直接访问 Termux 的音频设备
- 需要手机端播放声音（而非 Debian 端）

### 1.2 设计目标

1. **实时性**: 状态变化后 1 秒内发出声音
2. **可靠性**: 网络抖动时自动重连，不丢失通知
3. **可扩展**: 支持未来添加更多状态类型
4. **低侵入**: 不修改 Claude Code 核心代码，通过钩子集成

## 2. 系统架构

### 2.1 整体架构

```
┌─────────────────────── Android 手机 ───────────────────────┐
│  Termux                                                     │
│   WebSocket 客户端 (Python, 持久连接)                       │
│        │  收到 {"event":"stop"}                             │
│        ▼                                                    │
│   termux-media-player play stop.mp3  → 扬声器发声           │
└─────────────────────────┬───────────────────────────────────┘
                          │ WebSocket  ws://debian:8765
┌─────────────────────────┴───────────────────────────────────┐
│  Debian 虚拟机                                              │
│   Claude Code 钩子 (settings.json)                          │
│     Stop / Notification / SubagentStop 事件                 │
│        │  调用 notify.sh                                    │
│        ▼                                                    │
│   WebSocket 服务端 (Python, asyncio)                        │
│     广播事件给所有已连接的 Termux 客户端                     │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 为什么选 WebSocket 而非 HTTP 轮询

| 维度 | WebSocket | HTTP 轮询 |
|------|-----------|-----------|
| 延迟 | <100ms（推送） | 取决于轮询间隔 |
| 资源 | 一条长连接 | 反复建连 |
| 丢通知 | 不会 | 间隔内变化会丢 |
| 实现复杂度 | 中 | 低 |

由于状态通知的核心诉求是"事件发生即听到"，延迟敏感，WebSocket 推送模型最契合。

## 3. 状态事件定义

Claude Code 通过钩子（hooks）暴露以下生命周期事件，每个事件映射一种声音：

| 事件 | 钩子 | 含义 | 建议声音 |
|------|------|------|----------|
| `prompt` | UserPromptSubmit | 用户提交新任务，Claude 开始工作 | 短促"叮"（上升音） |
| `stop` | Stop | Claude 完成响应，等待用户 | 柔和"咚"（下降音） |
| `notification` | Notification | 需要权限确认或长时间无输入 | 急促"滴滴滴" |
| `subagent_stop` | SubagentStop | 子任务完成 | 轻微"咔" |

## 4. 组件设计

### 4.1 Debian 端：WebSocket 服务端

**职责**: 接收 Claude Code 钩子发来的事件，广播给所有连接的客户端。

**技术栈**: Python + `websockets` 库（纯 asyncio，无外部依赖）。

**关键设计**:
- 单文件脚本，约 100 行
- 维护一个 `connected_clients` 集合
- 收到事件后 `asyncio.gather` 广播，单客户端失败不影响其他
- 监听 `0.0.0.0:8765`

**事件接收接口（简化决策）**: 服务端额外监听一个本地 FIFO（命名管道），钩子脚本 `echo stop > /tmp/claude-events.fifo` 即可把事件喂给服务端，无需再开 HTTP 端口。

### 4.2 Debian 端：钩子脚本

**职责**: 被 Claude Code 调用，把事件名写入 FIFO。

脚本 `~/.claude/hooks/notify.sh`:
- 接收事件名作为参数
- 写入 `/tmp/claude-events.fifo`
- 静默失败（钩子绝不能阻塞 Claude Code）

`settings.json` 配置片段示例：
```json
{
  "hooks": {
    "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "~/.claude/hooks/notify.sh stop"}]}],
    "Notification": [{"matcher": "", "hooks": [{"type": "command", "command": "~/.claude/hooks/notify.sh notification"}]}],
    "UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command", "command": "~/.claude/hooks/notify.sh prompt"}]}]
  }
}
```

### 4.3 Termux 端：WebSocket 客户端

**职责**: 持久连接 Debian 服务端，收到事件后调用 `termux-media-player` 播放对应音频。

**依赖**:
- `pkg install termux-api` + 安装 Termux:API app（提供 `termux-media-player`）
- `pip install websockets`

**关键设计**:
- 重连退避：失败后 1s → 2s → 5s → 10s 封顶
- 事件→音频文件映射表，集中配置
- 缺少音频文件时降级为 `termux-notification`（系统通知带默认声）

**音频文件**: 存放于 `~/.claude/sounds/`，命名与事件一致：`prompt.mp3`、`stop.mp3` 等。可从手机系统铃声或 freesound.org 获取短音效。

### 4.4 Termux 端：守护进程

通过 Termux 的 `termux-wake-lock` + `nohup` 或 `tmux` 保持客户端常驻：
- `termux-wake-lock` 防止 Android 杀进程
- 客户端脚本内部循环重连，崩溃自动重启

## 5. 数据流（以"任务完成"为例）

1. Claude Code 响应结束 → 触发 `Stop` 钩子
2. Claude Code 执行 `notify.sh stop`
3. `notify.sh` 把 `stop` 写入 `/tmp/claude-events.fifo`
4. Debian 服务端从 FIFO 读到 `stop` → 广播 `{"event":"stop"}` 给所有 WebSocket 客户端
5. Termux 客户端收到 → 查映射表 → 执行 `termux-media-player play stop.mp3`
6. 手机扬声器播放"咚"声，用户得知任务完成

端到端延迟预期：< 500ms。

## 6. 错误处理

| 故障 | 行为 |
|------|------|
| Termux 客户端断连 | 服务端移出客户端集合；客户端按退避重连 |
| FIFO 写入失败 | 钩子脚本静默退出（不影响 Claude Code） |
| 音频文件缺失 | 降级为 `termux-notification` 弹通知 |
| 服务端崩溃 | 客户端持续重连；用 systemd/tmux 守护服务端 |
| 无连接客户端时 | 事件入 FIFO，服务端无客户端则丢弃（可加环形缓冲暂存最近 N 条） |

## 7. 测试方案

1. **单元**: Debian 端单独测试 FIFO → WebSocket 广播链路（用 `wscat` 模拟客户端）
2. **集成**: 真实触发 Claude Code 钩子，观察 Termux 是否发声
3. **故障注入**: 杀掉客户端进程，验证自动重连；杀掉服务端，验证守护重启
4. **延迟基准**: 从钩子触发到声音响起的实际耗时测量

## 8. 部署清单

**Debian 端**:
- `pip install websockets`
- 创建 FIFO `/tmp/claude-events.fifo`
- 部署服务端脚本 + systemd/tmux 守护
- 部署 `notify.sh` 并赋予执行权限
- 配置 `~/.claude/settings.json` 钩子

**Termux 端**:
- `pkg install termux-api` + 安装 Termux:API app
- `pip install websockets`
- 准备音频文件至 `~/.claude/sounds/`
- 部署客户端脚本 + `termux-wake-lock` 常驻

## 9. 未来扩展（YAGNI 暂不实现）

- 多设备同步通知
- 状态可视化（Termux 通知栏常驻图标）
- 声音音量按时段调节（夜间静音）
- webhook 转发到 Telegram/微信作为备份通道
