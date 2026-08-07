# 策略控制：从 Grafana 启动/停止/重启策略

## 概述

通过 FastAPI 控制服务器 + Grafana Infinity 数据源，在 Grafana 仪表板中直接控制量化策略的启动、停止、重启，无需登录服务器执行命令。

## 架构

```
Grafana Dashboard (Infinity Datasource)
    ↓ HTTP (proxy mode, server-side)
FastAPI Control API (host:0.0.0.0:5000)
    ↓ subprocess / direct Python
sqctl.sh / process table / PID files
    ↓
LEAN dotnet processes + bridge scripts
```

- Grafana（Docker, 172.20.0.4）→ API via Docker gateway `http://172.20.0.1:5000`
- Infinity 数据源使用 proxy 模式（服务器端调用，无 CORS 问题，token 保存在 Grafana 中）

## 文件清单

### 已创建的文件

| 文件 | 用途 |
|------|------|
| `Scripts/strategy_control_api.py` | FastAPI 控制服务器（策略状态查询 + 启停操作） |
| `monitoring/grafana/dashboards/lean/strategy-control.json` | 策略控制中心 Grafana 仪表板 |
| `monitoring/grafana/install_strategy_control.sh` | 一键安装脚本 |

### 已修改的文件

| 文件 | 修改内容 |
|------|----------|
| `sqctl.sh` | 新增 `strategy-api` 到 CORE_PROCS，`start_core` 中增加 API 启动逻辑 |

## 一键部署

```bash
cd /home/project/hope/Lean
bash monitoring/grafana/install_strategy_control.sh
```

安装脚本自动完成6个步骤：

1. **生成 API Token** → 保存到 `Results/soloquant/.strategy-api-token`
2. **安装 Infinity 插件** → `grafana-cli plugins install yesoreyeram-infinity-datasource`
3. **部署 Infinity 数据源** → 写入 Grafana 的 bind-mounted provisioning 目录
4. **部署控制仪表板** → 复制到 Grafana 的 bind-mounted dashboards 目录
5. **注入控制行** → 在 `barra-cne5-live-paper` 和 `ashare-multi-family` 仪表板顶部插入"策略控制"行（**不修改现有 panel**）
6. **启动 API 服务器** → uvicorn 在 0.0.0.0:5000

## API 端点

| 端点 | 方法 | 说明 | 认证 |
|------|------|------|------|
| `/api/health` | GET | 健康检查，返回运行中/已停止策略数 | 无需 |
| `/api/strategies` | GET | 列出所有策略状态（支持 `?group=` 过滤） | 无需 |
| `/api/strategies/by-algorithm/{alg_id}` | GET | 按 algorithm_id 查找（用于 Grafana `$algorithm_id` 变量） | 无需 |
| `/api/strategies/{id}/start` | POST | 启动策略（同时启动匹配的 bridge） | Bearer token |
| `/api/strategies/{id}/stop` | POST | 停止策略（同时停止匹配的 bridge） | Bearer token |
| `/api/strategies/{id}/restart` | POST | 重启策略（停止 → 启动） | Bearer token |
| `/api/groups/{group}/start` | POST | 分组启动（委托 sqctl.sh） | Bearer token |
| `/api/groups/{group}/stop` | POST | 分组停止（委托 sqctl.sh） | Bearer token |
| `/api/groups/{group}/restart` | POST | 分组重启（委托 sqctl.sh） | Bearer token |

### 策略响应格式

```json
{
  "strategy_id": "AShareBarraCNE5V2Algorithm",
  "algorithm_id": "AShareBarraCNE5V2Algorithm",
  "group": "legacy-lp",
  "status": "running",
  "pid": 12345,
  "uptime_seconds": 3600,
  "config_path": "/home/project/hope/Lean/Launcher/config/config-barra-cne5v2-live-paper.json",
  "bridge": "barra-cne5v2",
  "started_at_utc": "2026-06-18T00:00:00Z"
}
```

### 分组列表

| 分组 | 包含组件 |
|------|----------|
| `core` | pipeline-runner, crawl-scheduler |
| `livepaper` | SoloQuant live-paper LEAN 进程 |
| `legacy` | Legacy live-paper LEAN 进程（Launcher/config/） |
| `bridge` | Live bridge 守护进程 |
| `tushare` | Tushare 数据源 |
| `llm` | LLM 后台任务 |
| `all` | 全部 |

## Grafana 仪表板

### 策略控制中心（独立仪表板）

- **URL**: `http://84.8.248.198:3000/d/strategy-control/`
- **内容**:
  - 控制总览：运行中策略数、已停止策略数、API 运行时间、策略总数
  - Legacy Live Paper 策略表：策略名称、状态、PID、运行时间、Bridge
  - SoloQuant Live Paper 策略表：策略名称、状态、PID、运行时间
  - Bridge 进程表：Bridge 名称、状态、PID、运行时间
  - 核心进程表：进程名称、状态、PID、运行时间
  - 分组操作按钮：启动/停止 Live Paper、Bridge、Core

### 注入到现有仪表板的控制行

在以下仪表板顶部插入了"策略控制"折叠行（**不修改任何现有 panel**）：

- **Live Paper**: `http://84.8.248.198:3000/d/barra-cne5-live-paper/`
- **回测**: `http://84.8.248.198:3000/d/ashare-multi-family/`

控制行包含：
- 策略状态指示器（根据 `$algorithm_id` 变量查询当前策略运行状态）
- 启动按钮（▶ 启动）
- 停止按钮（⏹ 停止）
- 重启按钮（🔄 重启）

## 策略发现机制

与 `sqctl.sh` 一致，以**进程表为真实来源**：

1. 扫描 `Results/soloquant/live-paper-processes/*.pid.json` → SoloQuant live-paper 策略
2. 扫描 `Launcher/config/config-*live-paper*.json` + 进程表 → Legacy live-paper 策略
3. 扫描进程表匹配 bridge 脚本 → Bridge 进程
4. 扫描进程表匹配核心守护脚本 → Core 进程

缓存策略：内存缓存 5 秒刷新，控制操作后立即失效。

## 安全机制

- **Bearer Token 认证**：所有 POST 端点需要 `Authorization: Bearer <token>` 头
- Token 存储在 `Results/soloquant/.strategy-api-token`（权限 600）
- GET 端点（状态查询）无需认证，方便仪表板显示
- Infinity 数据源使用 `secureJsonData` 存储 token，不会发送到浏览器
- 无命令注入风险：strategy_id 验证注册表，group 验证固定白名单
- 审计日志：每次操作记录到 `Results/soloquant/strategy-control-audit.log`

## Docker 网络

- API 服务器：host 网络，`0.0.0.0:5000`
- Grafana 容器（172.20.0.4）→ API at `http://172.20.0.1:5000`（Docker bridge gateway）
- Infinity 数据源配置为 `proxy` 模式 → Grafana 服务器发起 HTTP 调用，不是浏览器
- proxy 模式无需 CORS 配置

## 日常运维

```bash
# 启动 API
./sqctl.sh start api

# 停止 API
./sqctl.sh stop api

# 查看 API 状态
./sqctl.sh status

# 手动测试 API
curl http://localhost:5000/api/health
curl http://localhost:5000/api/strategies

# 启动单个策略
TOKEN=$(cat Results/soloquant/.strategy-api-token)
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:5000/api/strategies/AShareBarraCNE5V2Algorithm/start

# 停止单个策略
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:5000/api/strategies/AShareBarraCNE5V2Algorithm/stop

# 分组操作
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:5000/api/groups/bridge/start

# 查看审计日志
cat Results/soloquant/strategy-control-audit.log
```

## 故障排查

| 问题 | 排查方法 |
|------|----------|
| API 无法启动 | 检查端口 5000 是否被占用：`lsof -i :5000`；查看日志：`cat Results/soloquant/strategy-control-api.log` |
| Grafana 控制面板无数据 | 检查 Infinity 数据源是否配置：Grafana → Configuration → Data sources → Strategy Control API |
| 控制按钮点击无响应 | Infinity 数据源使用 proxy 模式，按钮需要 Grafana 服务器能访问 `http://172.20.0.1:5000` |
| Token 无效 | 检查 `Results/soloquant/.strategy-api-token` 是否与 Grafana 数据源中 `secureJsonData` 一致 |
| 注入的控制行消失 | 仪表板文件被覆盖时需要重新运行注入：`bash monitoring/grafana/install_strategy_control.sh`（只执行 Step 5） |
