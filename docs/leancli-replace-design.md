# Lean CLI 免费替代方案设计文档

## 项目名称：LeanLocal CLI

---

## 1. 项目概述

### 1.1 目标

设计一个开源的本地量化交易 CLI 工具，复刻 QuantConnect Lean CLI 80% 以上的核心功能，**完全免费**，不依赖 QuantConnect 账号。

### 1.2 核心价值

| 特性 | Lean CLI (付费) | LeanLocal CLI (免费) |
|------|----------------|---------------------|
| 回测引擎 | ✅ | ✅ (本地 LEAN) |
| Live Trading | ✅ | ✅ (本地) |
| HTML 报告 | ✅ | ✅ |
| 数据下载 | QuantConnect API | 开源数据源 |
| 云端同步 | ✅ | ❌ (不需要) |
| 付费要求 | $29+/月 | **免费** |

### 1.3 目标用户

- 独立量化开发者
- 量化研究爱好者
- 学生和研究人员
- 无法负担 QuantConnect 订阅费用的用户

---

## 2. 功能覆盖分析

### 2.1 Lean CLI 功能清单

| 功能模块 | 功能项 | 优先级 | 替代方案 |
|----------|--------|--------|----------|
| **回测** | 本地回测 | P0 | 直接调用 LEAN Engine |
| | 回测报告生成 | P0 | 本地 HTML 报告生成器 |
| | 参数化回测 | P1 | 脚本循环调用 |
| **Live Trading** | Paper Trading | P0 | 本地 PaperBrokerage |
| | 实时监控 | P0 | ZeroMQ + Web UI |
| | 订单管理 | P1 | CLI 命令 |
| **数据管理** | 数据下载 | P1 | Tushare/AKDemo |
| | 数据格式转换 | P2 | 脚本工具 |
| **项目管理** | 项目创建 | P1 | 模板生成 |
| | 依赖管理 | P2 | requirements.txt |
| **研究** | Jupyter 集成 | P2 | 独立启动 |

### 2.2 目标覆盖功能（80%+）

```
核心功能 (必须实现):
├── lean backtest        → 本地回测
├── lean report          → HTML 报告生成
├── lean live deploy    → 本地 Live Paper
├── lean live stop      → 停止 Live
└── lean live status    → 查看状态

重要功能 (尽量实现):
├── lean init            → 初始化项目
├── lean data download  → 数据下载 (开源源)
├── lean logs           → 查看日志
└── lean optimize       → 参数优化 (简化版)

不需要实现 (依赖云服务):
├── lean cloud *
├── lean library *
└── lean object-store *
```

---

## 3. 系统架构

### 3.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                      LeanLocal CLI                          │
│  (Python CLI - Click/Typer)                                │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐      │
│  │ Backtest │ │  Report  │ │  Live    │ │  Data    │      │
│  │  Module  │ │  Module  │ │  Module  │ │  Module  │      │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘      │
│       │            │            │            │             │
│       └────────────┴────────────┴────────────┘             │
│                         │                                  │
│                    ┌────┴────┐                            │
│                    │  Core   │                            │
│                    │ Engine  │                            │
│                    │ Wrapper │                            │
│                    └────┬────┘                            │
├─────────────────────────┼──────────────────────────────────┤
│                    LEAN Engine                              │
│  (C# .NET - Docker 或直接二进制)                           │
├──────────────────────────────────────────────────────────────┤
│  Data Sources (Free)                                        │
│  ├── Tushare (A股)                                          │
│  ├── AkShare (A股/基金)                                     │
│  ├── Yahoo Finance (美股)                                   │
│  └── CCXT (加密货币)                                        │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 组件设计

#### 3.2.1 CLI 入口层

```python
# CLI 结构设计
leanlocal/
├── __main__.py           # 入口
├── cli.py                # CLI 定义 (Click)
├── commands/
│   ├── __init__.py
│   ├── backtest.py       # 回测命令
│   ├── report.py         # 报告命令
│   ├── live.py           # Live 命令
│   ├── data.py           # 数据命令
│   └── init.py          # 初始化命令
└── options.py            # 公共选项
```

#### 3.2.2 Engine 封装层

```python
# Engine 接口抽象
class EngineInterface(Protocol):
    """LEAN Engine 接口"""
    
    def run_backtest(self, config: BacktestConfig) -> BacktestResult:
        """运行回测"""
        ...
    
    def run_live(self, config: LiveConfig) -> LiveResult:
        """运行 Live"""
        ...
    
    def stop(self, task_id: str) -> None:
        """停止任务"""
        ...
    
    def get_status(self, task_id: str) -> TaskStatus:
        """获取状态"""
        ...

# 实现方式选择
class DockerEngine(EngineInterface):
    """Docker 方式运行 LEAN"""
    
    def __init__(self, image: str = "quantconnect/lean:latest"):
        self.image = image
        self.container = None

class DirectEngine(EngineInterface):
    """直接调用 LEAN 二进制"""
    
    def __init__(self, lean_path: str):
        self.lean_path = lean_path
```

#### 3.2.3 报告生成器

```python
# 报告生成模块
class ReportGenerator:
    """本地报告生成器"""
    
    def __init__(self, template_dir: str = "templates"):
        self.template_dir = template_dir
    
    def generate(self, 
                 result: BacktestResult,
                 output: str = "report.html",
                 template: str = "default") -> str:
        """生成 HTML 报告"""
        ...
    
    def generate_pdf(self,
                    result: BacktestResult,
                    output: str = "report.pdf") -> str:
        """生成 PDF 报告"""
        ...

# 报告模板
class ReportTemplate:
    """报告模板"""
    
    # 必须包含的图表
    CHARTS = [
        "Strategy Equity",      # 权益曲线
        "Benchmark",             # 基准对比
        "Drawdown",             # 回撤
        "Monthly Returns",       # 月度收益
        "Trade Plot",           # 交易分布
    ]
    
    # 必须包含的统计
    STATISTICS = [
        "Total Return",         # 总收益
        "Sharpe Ratio",         # 夏普比率
        "Sortino Ratio",        # 索提诺比率
        "Max Drawdown",         # 最大回撤
        "Win Rate",             # 胜率
        "Profit Factor",        # 盈利因子
        "Total Trades",         # 总交易数
    ]
```

#### 3.2.4 数据下载模块

```python
# 数据源接口
class DataSource(ABC):
    """数据源抽象"""
    
    @abstractmethod
    def download(self, 
                symbol: str, 
                start: date, 
                end: date,
                resolution: Resolution) -> pd.DataFrame:
        """下载数据"""
        ...

# 实现的数据源
class TushareDataSource(DataSource):
    """Tushare 数据源"""
    
    def __init__(self, token: str = None):
        self.token = token or os.getenv("TUSHARE_TOKEN")
    
    def download(self, symbol: str, ...):
        # 实现 Tushare 下载
        ...

class AkShareDataSource(DataSource):
    """AkShare 数据源 (完全免费)"""
    
    def download(self, symbol: str, ...):
        # 实现 AkShare 下载
        ...

class YahooDataSource(DataSource):
    """Yahoo Finance 数据源"""
    
    def download(self, symbol: str, ...):
        # 实现 Yahoo 下载
        ...
```

---

## 4. 核心命令设计

### 4.1 leanlocal backtest

```bash
# 基础用法
leanlocal backtest <PROJECT> [OPTIONS]

# 选项
--output, -o <dir>          # 输出目录
--config, -c <file>         # 配置文件
--data-provider <provider>  # 数据源 (tushare/akshare/yahoo)
--release                   # Release 模式编译
--debug <method>             # 调试模式
--parameter <key> <value>   # 策略参数
--help

# 示例
leanlocal backtest ./my-algo --output ./results
leanlocal backtest ./my-algo --parameter period 20 --parameter threshold 0.02
leanlocal backtest ./my-algo --debug pydevd
```

### 4.2 leanlocal report

```bash
# 基础用法
leanlocal report [OPTIONS]

# 选项
--backtest-results, -b <file>    # 回测结果文件
--live-results, -l <file>        # Live 结果文件
--output, -o <file>              # 输出报告路径
--template, -t <name>             # 报告模板
--css <file>                      # 自定义 CSS
--pdf                             # 生成 PDF
--strategy-name <name>           # 策略名称
--strategy-description <desc>    # 策略描述

# 示例
leanlocal report -b ./result.json -o report.html
leanlocal report -b ./backtest.json -l ./live.json -o compare.html
leanlocal report -b ./result.json --pdf
```

### 4.3 leanlocal live

```bash
# 子命令结构
leanlocal live <COMMAND>

# deploy - 启动 Live
leanlocal live deploy <PROJECT> [OPTIONS]
  --brokerage <name>              # 券商 (Paper/IB/Alpaca)
  --data-feed <provider>          # 数据源
  --output, -o <dir>              # 输出目录
  --detach                        # 后台运行
  
# stop - 停止
leanlocal live stop <PROJECT>

# status - 状态
leanlocal live status [PROJECT]

# logs - 日志
leanlocal live logs <PROJECT> [--lines N]

# 示例
leanlocal live deploy ./my-algo --brokerage Paper
leanlocal live deploy ./my-algo --detach -o ./live-output
leanlocal live status
leanlocal live stop my-algo
```

### 4.4 leanlocal init

```bash
# 初始化新项目
leanlocal init <PROJECT_NAME> [OPTIONS]

# 选项
--language <python|csharp>       # 语言
--template <name>                # 模板

# 示例
leanlocal init my-strategy --language python
leanlocal init mean-reversion --template momentum
```

### 4.5 leanlocal data

```bash
# 数据下载
leanlocal data download <SYMBOL> [OPTIONS]

# 选项
--source <provider>              # 数据源
--start <date>                  # 开始日期
--end <date>                    # 结束日期
--resolution <1min|1hour|1day>  # 分辨率
--output, -o <dir>              # 输出目录

# 示例
leanlocal data download 000001.SZ --source akshare --start 2020-01-01
leanlocal data download AAPL --source yahoo --resolution 1day
```

---

## 5. 数据流设计

### 5.1 回测数据流

```
用户执行命令
    ↓
CLI 解析参数
    ↓
生成 LEAN 配置文件 (lean.json)
    ↓
启动 LEAN Engine (Docker/二进制)
    ↓
运行回测 → 输出 result.json
    ↓
读取 result.json → 生成 HTML 报告
    ↓
用户查看报告
```

### 5.2 Live Trading 数据流

```
用户执行 deploy
    ↓
CLI 配置 PaperBrokerage
    ↓
启动 LEAN Engine (后台)
    ↓
ZeroMQ 推送实时数据
    ↓
├── 本地 Web UI (可选)
├── 文件输出 (result.json)
└── 日志输出 (logs/)
```

### 5.3 数据下载流

```
用户请求下载
    ↓
CLI 调用数据源
    ├── Tushare (需要 Token)
    ├── AkShare (免费)
    └── Yahoo Finance (免费)
    ↓
数据转换为 LEAN 格式
    ↓
保存到 Data/ 目录
```

---

## 6. 技术选型

### 6.1 CLI 框架

| 框架 | 语言 | 特点 | 选择理由 |
|------|------|------|----------|
| Click | Python | 最流行 CLI 框架 | 文档丰富，易上手 |
| Typer | Python | 现代，类型提示 | 与 FastAPI 生态兼容 |
| Fire | Python | 简单 | 快速原型 |
| **Typer** | Python | **类型友好** | **推荐** |

### 6.2 报告生成

| 库 | 用途 | 选择 |
|----|------|------|
| Jinja2 | 模板引擎 | HTML 报告 |
| Plotly/Altair | 图表 | 交互式图表 |
| WeasyPrint | HTML→PDF | PDF 生成 |
| **ECharts + Jinja2** | **图表** | **推荐 (中国开发者友好)** |

### 6.3 数据源

| 数据源 | 覆盖市场 | 费用 | 可靠性 |
|--------|----------|------|--------|
| AkShare | A股/基金/期货 | 免费 | 高 |
| Tushare Pro | A股 | 免费/付费 | 高 |
| Yahoo Finance | 美股/ETF | 免费 | 中 |
| CCXT | 加密货币 | 免费 | 高 |

### 6.4 实时监控

| 技术 | 用途 | 选择 |
|------|------|------|
| ZeroMQ | 消息队列 | 必须 (与 LEAN 一致) |
| Flask/FastAPI | Web UI | 推荐 |
| Socket.IO | WebSocket | 可选 |

### 6.5 部署方式

| 方式 | 优点 | 缺点 |
|------|------|------|
| Docker | 环境隔离，易部署 | 需要 Docker |
| 直接安装 | 无依赖 | 需要 .NET SDK |
| **Docker (推荐)** | **与 LEAN 官方兼容** | |

---

## 7. 项目结构

```
leanlocal/
├── README.md
├── LICENSE (Apache 2.0)
├── setup.py / pyproject.toml
├── requirements.txt
│
├── leanlocal/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                 # CLI 入口
│   ├── config.py              # 配置管理
│   ├── constants.py           # 常量
│   │
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── base.py            # 引擎接口
│   │   ├── docker.py          # Docker 引擎
│   │   └── direct.py          # 直接运行
│   │
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── backtest.py        # 回测命令
│   │   ├── report.py          # 报告命令
│   │   ├── live.py            # Live 命令
│   │   ├── data.py            # 数据命令
│   │   └── init.py            # 初始化命令
│   │
│   ├── report/
│   │   ├── __init__.py
│   │   ├── generator.py       # 报告生成器
│   │   ├── templates/         # HTML 模板
│   │   │   ├── base.html
│   │   │   ├── equity.html
│   │   │   ├── statistics.html
│   │   │   └── trades.html
│   │   └── charts/            # 图表生成
│   │
│   ├── data/
│   │   ├── __init__.py
│   │   ├── base.py            # 数据源接口
│   │   ├── akshare.py         # AkShare 源
│   │   ├── tushare.py         # Tushare 源
│   │   ├── yahoo.py           # Yahoo 源
│   │   └── converter.py       # 格式转换
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logger.py          # 日志
│       ├── zeromq.py          # ZeroMQ 工具
│       └── docker.py          # Docker 工具
│
├── templates/                  # 项目模板
│   ├── python/
│   │   ├── Main.py
│   │   └── requirements.txt
│   └── csharp/
│       ├── Main.cs
│       └── algorithm.csproj
│
├── docs/
│   ├── installation.md
│   ├── quickstart.md
│   ├── commands.md
│   └── data-sources.md
│
└── tests/
    ├── test_cli.py
    ├── test_engine.py
    ├── test_report.py
    └── test_data.py
```

---

## 8. 实现优先级

### Phase 1: 核心功能 (MVP)

```
Week 1-2: CLI 框架搭建
├── CLI 入口和基础结构
├── 命令框架定义
└── 配置文件生成

Week 3-4: 回测功能
├── Docker Engine 封装
├── 回测命令实现
└── 结果读取

Week 5-6: 报告功能
├── HTML 模板
├── 图表生成
└── 报告生成命令
```

**交付物**: 可以运行回测并生成 HTML 报告

### Phase 2: Live Trading

```
Week 7-8: Live 功能
├── Live deploy 命令
├── ZeroMQ 数据接收
├── 状态管理
└── stop/status 命令
```

**交付物**: 可以运行 Live Paper 并监控

### Phase 3: 数据和扩展

```
Week 9-10: 数据功能
├── AkShare 集成
├── 数据下载命令
└── 格式转换

Week 11-12: 优化和发布
├── 参数优化 (简化版)
├── 文档完善
└── 测试和发布
```

**交付物**: 完整 CLI 工具

---

## 9. 关键设计决策

### 9.1 为什么不做云端同步？

- 用户目标是**本地运行**
- 云端需要 QuantConnect 账号
- 本地存储足够满足需求
- 可以通过 Git 手动同步

### 9.2 为什么使用 Docker？

- 与 LEAN 官方环境一致
- 无需用户安装 .NET
- 环境隔离，避免冲突
- 易于分发

### 9.3 如何保证免费？

- 数据源全部使用开源/免费方案
- 不依赖任何付费 API
- 完全本地运行
- 开源许可证 (Apache 2.0)

---

## 10. 兼容性设计

### 10.1 与现有 LEAN 项目兼容

```json
// 支持现有的 lean.json 配置
{
  "lean": "local",                    // 标识为本地模式
  "data-folder": "./Data",            // 数据目录
  "results-destination-folder": "./Results",  // 结果目录
  
  // 新增: 数据源配置
  "data-source": "akshare",           // 数据源
  "data-provider": {
    "tushare": {
      "token": "${TUSHARE_TOKEN}"    // 环境变量支持
    }
  }
}
```

### 10.2 与 Docker LEAN 兼容

```bash
# 直接使用官方 LEAN 镜像
docker run -it \
  -v $(pwd)/Algorithm:/Algorithm \
  -v $(pwd)/Data:/Data \
  -v $(pwd)/Results:/Results \
  quantconnect/lean:latest \
  python Main.py
```

---

## 11. 用户旅程

### 11.1 首次使用

```bash
# 1. 安装
pip install leanlocal

# 2. 初始化项目
leanlocal init my-strategy --language python

# 3. 编辑策略
vim my-strategy/Main.py

# 4. 运行回测
leanlocal backtest my-strategy

# 5. 查看报告
# 自动打开浏览器
```

### 11.2 运行 Live Paper

```bash
# 1. 启动 Live
leanlocal live deploy my-strategy --brokerage Paper

# 2. 另一个终端查看状态
leanlocal live status

# 3. 查看日志
leanlocal live logs my-strategy

# 4. 停止
leanlocal live stop my-strategy
```

---

## 12. 风险管理

### 12.1 技术风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| LEAN 官方镜像变更 | 高 | 固定版本，定期更新 |
| 数据源 API 变更 | 中 | 抽象接口，快速适配 |
| ZeroMQ 协议变更 | 低 | 跟踪官方版本 |

### 12.2 用户风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Docker 安装困难 | 中 | 提供直接安装选项 |
| 数据获取困难 | 高 | 集成多个免费数据源 |
| 环境配置复杂 | 中 | 提供 Docker Compose 一键启动 |

---

## 13. 成功指标

### 13.1 功能覆盖

- [ ] 覆盖 Lean CLI 80%+ 命令
- [ ] 支持 Python 和 C# 算法
- [ ] 支持主流数据源

### 13.2 用户体验

- [ ] 安装时间 < 5 分钟
- [ ] 首次回测 < 10 分钟
- [ ] 报告生成 < 30 秒

### 13.3 社区目标

- [ ] 开源发布
- [ ] 文档完整
- [ ] 欢迎贡献

---

## 14. 总结

本设计文档提供了一个完整的 Lean CLI 免费替代方案 **LeanLocal CLI** 的详细规划。

**核心优势**：
- ✅ 完全免费，无需 QuantConnect 账号
- ✅ 本地运行，数据隐私安全
- ✅ 开源透明，可自定义扩展
- ✅ 覆盖 80%+ 核心功能
- ✅ 与现有 LEAN 项目兼容

**实施建议**：
1. 优先实现回测 + 报告（MVP）
2. 利用 Docker 简化部署
3. 集成多个免费数据源
4. 保持与 LEAN 官方的兼容性

---

*本文档为纯设计文档，不包含任何代码实现。*
