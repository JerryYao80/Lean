# 多策略统一量化框架架构设计

**文档版本**: 1.0  
**创建日期**: 2026-03-27  
**目的**: 设计高内聚、低耦合、模块化、可扩展的多策略量化框架

---

## 1. 现状分析与问题定位

### 1.1 当前架构回顾

基于 `docs/past-done.txt` 的记录，当前架构分为两层：

```
┌─────────────────────────────────────────────────────────────────┐
│                     策略层（Strategy Layer）                     │
│  Barra / LLM / ETF T0 / T1 / 行业轮动各自独立的 bridge + live_paper │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                     共享层（Shared Layer）                        │
│  ashare_live_market_cache.py + live_paper_runner.py              │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 当前问题清单

| 问题 | 描述 | 根因 |
|------|------|------|
| **重复代码** | 每个策略都有独立的 bridge.py 和 live_paper.py | 未抽象共性 |
| **流程碎片化** | 各策略的数据处理逻辑散落在不同脚本 | 缺乏统一抽象 |
| **配置不一致** | 每个策略的配置文件格式各异 | 缺乏配置Schema |
| **扩展困难** | 新增策略需要复制大量模板代码 | 架构缺乏可扩展性 |
| **维护成本** | 修改共性逻辑需要在多个文件同步 | 耦合度过高 |

### 1.3 核心矛盾

- **业务矛盾**: 策略调优是核心差异化工作，但流程杂事（数据获取、结果输出）占据大量精力
- **技术矛盾**: 各策略的流程高度相似（数据源 → 过滤 → 特征生成 → LEAN执行），但实现各自为政

---

## 2. 设计目标与原则

### 2.1 核心目标

1. **策略调优优先**: 框架承担流程杂事，用户专注策略逻辑
2. **一次编写，多次复用**: 新增策略时，通过配置而非代码复制实现
3. **统一入口**: 单一命令行接口，支持所有策略类型
4. **可追溯**: 策略参数、执行结果、代码版本可关联

### 2.2 设计原则

| 原则 | 含义 |
|------|------|
| **配置驱动** | 策略行为通过参数配置，而非硬编码分支 |
| **插件式扩展** | 新增策略类型只需实现接口，无需修改核心代码 |
| **单入口原则** | backtest / live-paper 共用同一入口，只通过配置区分 |
| **接口隔离** | 各模块通过明确定义的接口通信，模块间无直接依赖 |
| **可测试性** | 每个核心模块可独立单元测试 |

---

## 3. 整体架构

### 3.1 分层架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        应用层（Application Layer）                       │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐│
│  │ BacktestCLI     │  │ LivePaperCLI    │  │ StrategyTunerGUI        ││
│  └────────┬────────┘  └────────┬────────┘  └────────────┬────────────┘│
└───────────┼─────────────────────┼───────────────────────┼──────────────┘
            │                     │                       │
            ▼                     ▼                       ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                        核心层（Core Layer）                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │
│  │ StrategyEngine  │  │ DataPipeline    │  │ ExecutionEngine         │ │
│  │ (策略执行引擎)   │  │ (数据处理管道)   │  │ (回测/实盘执行)         │ │
│  └────────┬────────┘  └────────┬────────┘  └────────────┬────────────┘ │
│           │                    │                        │              │
│  ┌────────▼────────────────────▼────────────────────────▼────────────┐ │
│  │                     Configuration Manager                        │ │
│  │                     (统一配置管理)                                  │ │
│  └───────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
            │                     │                       │
            ▼                     ▼                       ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                        基础设施层（Infrastructure Layer）               │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │
│  │ MarketDataSource │  │ BrokerageGateway│  │ PersistenceLayer       │ │
│  │ (市场数据源)     │  │ (券商网关)       │  │ (持久化层)              │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.2 模块职责

| 模块 | 职责 | 边界 |
|------|------|------|
| **StrategyEngine** | 策略生命周期管理、信号生成、组合优化 | 不涉及数据获取和执行细节 |
| **DataPipeline** | 数据订阅、清洗、转换、特征计算 | 不涉及策略逻辑 |
| **ExecutionEngine** | 回测模拟或实盘订单执行 | 不涉及策略收益来源 |
| **Configuration Manager** | 配置加载、验证、参数注入 | 单一配置源 |
| **MarketDataSource** | 历史/实时数据抽象 | 只读数据 |
| **BrokerageGateway** | 券商接口抽象 | 只读订单状态 |

---

## 4. 核心模块设计

### 4.1 配置系统（Configuration Manager）

#### 4.1.1 设计理念

所有策略共享同一套配置 Schema，通过 `strategy-type` 区分不同策略类型。配置分为三层：

```
全局配置（Framework） → 策略配置（Strategy） → 运行时配置（Runtime）
```

#### 4.1.2 配置Schema

```yaml
# config-framework.yaml - 框架级配置（所有策略共用）
framework:
  version: "1.0"
  data:
    source: "tushare"                    # 数据源类型
    cache-dir: "/tmp/market-cache"        # 缓存目录
    realtime-mode: "rt_k"                 # 实时模式
  execution:
    mode: "backtest" | "live-paper"      # 执行模式
    dotnet-path: "/usr/local/dotnet/dotnet"
    lean-launcher: "Launcher/bin/Debug/QuantConnect.Lean.Launcher.dll"
  output:
    format: "native" | "custom"           # 输出格式
    results-dir: "Results"

# config-strategy-{name}.yaml - 策略配置
strategy:
  type: "barra" | "llm" | "etf_t0" | "t1" | "industry-rotation" | "custom"
  parameters:
    # 策略特定参数（由策略实现定义）
    initial-capital: 1000000
    rebalance-frequency: "monthly"
    universe-filter: "market_cap > 1e9"

# config-runtime.yaml - 运行时覆盖（CLI参数）
runtime:
  start-date: "2024-01-01"
  end-date: "2025-12-31"
  verbose: true
```

#### 4.1.3 配置加载流程

```
CLI参数 → runtime.yaml → 覆盖策略配置 → 合并框架配置 → 验证Schema → 注入各模块
```

### 4.2 数据管道（Data Pipeline）

#### 4.2.1 设计理念

数据管道是策略无关的，只负责：将原始数据转换为策略可用的格式。

#### 4.2.2 核心接口

```python
class DataPipeline(Protocol):
    """数据处理管道协议"""
    
    def subscribe(self, symbols: list[Symbol], resolution: Resolution) -> None:
        """订阅数据"""
        
    def get_snapshot(self, timestamp: datetime) -> DataSnapshot:
        """获取指定时刻的数据快照"""
        
    def get_history(self, symbol: Symbol, start: datetime, end: datetime) -> pd.DataFrame:
        """获取历史数据"""
        
    def register_feature(self, name: str, calculator: FeatureCalculator) -> None:
        """注册特征计算器"""
        
    def compute_features(self, timestamp: datetime) -> FeatureSet:
        """计算特征集"""
```

#### 4.2.3 管道阶段

```
┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│ DataSource  │ → │  Normalizer │ → │  Filter    │ → │  Feature   │
│ (原始数据)   │   │  (标准化)    │   │  (Universe)│   │  (特征计算)  │
└─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘
```

| 阶段 | 功能 | 策略相关度 |
|------|------|------------|
| DataSource | 从 Tushare / parquet 获取原始数据 | 无关 |
| Normalizer | 统一数据格式（symbol转换、时间时区） | 无关 |
| Filter | 按策略 universe 过滤股票 | 低相关 |
| Feature | 计算策略所需特征 | **高相关** |

#### 4.2.4 扩展点：特征计算器

```python
class FeatureCalculator(Protocol):
    """策略特征计算器"""
    
    @property
    def name(self) -> str:
        """特征名称"""
        
    def compute(self, snapshot: DataSnapshot) -> pd.Series:
        """计算单条特征"""
        
    def dependencies(self) -> list[str]:
        """依赖的其他特征"""
```

**内置特征计算器**:
- MarketCapFeature (市值)
- TurnoverFeature (换手率)
- MomentumFeature (动量)
- VolatilityFeature (波动率)

**自定义特征**: 策略可通过配置注册自定义特征计算器

### 4.3 策略引擎（Strategy Engine）

#### 4.3.1 设计理念

策略引擎负责策略的核心逻辑（信号生成、组合优化、风险管理），与数据获取和执行解耦。

#### 4.3.2 策略接口

```python
class Strategy(Protocol):
    """策略协议"""
    
    @property
    def name(self) -> str:
        """策略名称"""
        
    def initialize(self, config: StrategyConfig) -> None:
        """初始化策略"""
        
    def generate_signals(self, features: FeatureSet, timestamp: datetime) -> SignalSet:
        """生成交易信号"""
        
    def optimize_portfolio(self, signals: SignalSet, current_holdings: Holdings) -> PortfolioTarget:
        """组合优化"""
        
    def validate_signal(self, signal: Signal, position: Position) -> bool:
        """信号验证（风控）"""
```

#### 4.3.3 内置策略类型

| 策略类型 | 类名 | 适用场景 |
|----------|------|----------|
| Barra多因子 | BarraStrategy | Barra CNE5 因子模型选股 |
| LLM选股 | LlmStrategy | LLM 选股信号 |
| ETF T+0 | EtfT0Strategy | ETF 日内T+0 |
| T+1择时 | T1Strategy | A股 T+1 择时 |
| 行业轮动 | IndustryRotationStrategy | 行业轮动调仓 |
| 自定义 | CustomStrategy | 用户自定义策略 |

#### 4.3.4 自定义策略注册

```yaml
strategy:
  type: "custom"
  class: "my_strategy_module.MyCustomStrategy"
  parameters:
    signal-threshold: 0.7
    max-positions: 20
```

### 4.4 执行引擎（Execution Engine）

#### 4.4.1 设计理念

执行引擎统一处理 backtest 和 live-paper 的执行细节，对策略层透明。

#### 4.4.2 执行模式抽象

```python
class ExecutionMode(Protocol):
    """执行模式协议"""
    
    def initialize(self, config: ExecutionConfig) -> None:
        """初始化执行环境"""
        
    def execute_orders(self, orders: list[Order]) -> list[Fill]:
        """执行订单（回测模拟 或 实盘提交）"""
        
    def get_current_prices(self, symbols: list[Symbol]) -> dict[Symbol, decimal]:
        """获取当前价格"""
        
    def get_buy_power(self) -> decimal:
        """获取可用购买力"""
```

#### 4.4.3 执行模式实现

| 模式 | 实现类 | 功能 |
|------|--------|------|
| Backtest | BacktestExecution | 基于历史数据模拟成交 |
| Live-Paper | LivePaperExecution | 对接 LEAN 实盘模拟 |
| Paper Trading | PaperExecution | 模拟下单，纸面盈亏 |

#### 4.4.4 与 LEAN 对接

执行引擎内部封装 LEAN Launcher 调用，对外提供统一接口：

```python
class LeanExecutionAdapter:
    """LEAN 执行适配器"""
    
    def __init__(self, config: LeanConfig):
        self._config = config
        
    def execute(self, strategy: Strategy, start: datetime, end: datetime) -> ResultBundle:
        """执行策略并返回结果"""
        # 1. 生成 LEAN 算法参数
        # 2. 启动 LEAN Launcher
        # 3. 解析输出结果
        # 4. 返回结构化结果
```

---

## 5. 统一入口设计

### 5.1 命令行接口

```bash
# 框架主入口
quant-framework --config <config-file> [options]

# 选项说明
--config FILE           # 配置文件路径 (必需)
--mode backtest|live   # 覆盖配置中的执行模式
--start-date DATE      # 覆盖起始日期
--end-date DATE        # 覆盖结束日期
--verbose              # 详细输出
--show-config          # 显示合并后的配置并退出
```

### 5.2 策略注册与发现

```python
# 框架内置策略自动注册
STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "barra": BarraStrategy,
    "llm": LlmStrategy,
    "etf_t0": EtfT0Strategy,
    "t1": T1Strategy,
    "industry-rotation": IndustryRotationStrategy,
}

# 自定义策略通过配置注册
def load_custom_strategy(config: dict) -> Strategy:
    module_path = config["strategy"]["class"]
    return import_and_instantiate(module_path)
```

---

## 6. 数据流设计

### 6.1 Backtest 数据流

```
配置文件
    │
    ▼
┌────────────────┐
│ Config Manager │ ─── 验证并合并配置
└────────┬───────┘
         │
         ▼
┌────────────────┐    ┌────────────────┐    ┌────────────────┐
│ Data Pipeline  │ →  │ StrategyEngine │ →  │ ExecutionEngine│
│ 加载历史数据    │    │ 生成信号        │    │ 模拟订单成交    │
└────────────────┘    └────────────────┘    └────────┬───────┘
                                                     │
                                                     ▼
                                            ┌────────────────┐
                                            │   ResultBundle │
                                            │ (绩效报告/交易记录)│
                                            └────────────────┘
```

### 6.2 Live-Paper 数据流

```
配置文件
    │
    ▼
┌────────────────┐
│ Config Manager │
└────────┬───────┘
         │
         ▼
┌────────────────┐    ┌────────────────┐    ┌────────────────┐
│ Market Cache   │ →  │ Data Pipeline  │ →  │ StrategyEngine │
│ 实时日线缓存    │    │ 实时数据处理    │    │ 实时信号生成    │
└────────────────┘    └────────────────┘    └────────┬───────┘
                                                     │
                                                     ▼
                                        ┌────────────────┐
                                        │ ExecutionEngine│
                                        │ LEAN Live-Paper│
                                        └────────┬───────┘
                                                 │
                                                 ▼
                                        ┌────────────────┐
                                        │   Real-time    │
                                        │   Output       │
                                        └────────────────┘
```

### 6.3 核心数据模型

```python
@dataclass(frozen=True)
class Signal:
    symbol: Symbol
    direction: OrderDirection      # Buy / Sell / Hold
    confidence: float              # 0.0 - 1.0
    reason: str                    # 信号原因（因子/规则）

@dataclass(frozen=True)
class PortfolioTarget:
    symbol: Symbol
    target_weight: decimal          # 目标权重 -1.0 到 1.0
    target_quantity: int           # 目标股数
    reason: str                     # 调仓原因

@dataclass(frozen=True)
class Fill:
    symbol: Symbol
    direction: OrderDirection
    quantity: int
    price: decimal
    timestamp: datetime
    fee: decimal

@dataclass(frozen=True)
class ResultBundle:
    returns: pd.Series
    positions: pd.DataFrame
    trades: pd.DataFrame
    statistics: PerformanceStats
    config_hash: str               # 配置哈希（可追溯）
    version: str                    # 框架版本
```

---

## 7. 扩展机制

### 7.1 扩展点清单

| 扩展点 | 接口 | 说明 |
|--------|------|------|
| 数据源 | `MarketDataSource` | 添加新的市场数据来源 |
| 特征计算 | `FeatureCalculator` | 添加新的因子/特征 |
| 策略 | `Strategy` | 实现自定义策略 |
| 执行模式 | `ExecutionMode` | 添加新的执行方式 |
| 输出格式 | `ResultFormatter` | 添加新的输出格式 |

### 7.2 扩展注册方式

```yaml
# 扩展通过配置文件注册
extensions:
  data-sources:
    - class: "mymodule.MyDataSource"
      priority: 100
  features:
    - class: "mymodule.MyFeature"
      params:
        window: 20
  output-formatters:
    - type: "csv"
    - type: "json"
    - type: "custom"
      class: "mymodule.MyFormatter"
```

---

## 8. 与现有系统的迁移

### 8.1 复用现有组件

| 现有组件 | 复用方式 | 封装层 |
|----------|----------|--------|
| ashare_live_market_cache.py | 作为 `MarketDataSource` 实现 | DataPipeline |
| live_paper_runner.py | 作为 `LeanExecutionAdapter` 内部实现 | ExecutionEngine |
| 各策略 bridge.py | 迁移为数据过滤逻辑 | DataPipeline.Filter |
| LEAN Launcher | 通过适配器调用 | ExecutionEngine |
| LEAN 算法 | 作为策略实现 | StrategyEngine |

### 8.2 迁移路径

```
Phase 1: 配置抽象
  - 定义统一配置Schema
  - 实现 Config Manager
  - 将现有配置迁移为统一格式

Phase 2: 数据管道抽象
  - 封装 ashare_live_market_cache.py
  - 实现 DataPipeline 接口
  - 各策略通过 DataPipeline 获取数据

Phase 3: 执行引擎抽象
  - 封装 live_paper_runner.py
  - 实现 ExecutionEngine 接口
  - 统一 backtest/live-paper 入口

Phase 4: 策略引擎
  - 实现 Strategy 接口
  - 迁移现有策略为 Strategy 实现
  - 实现策略注册与发现机制
```

---

## 9. 目录结构设计

```
quant-framework/
├── config/
│   ├── framework.yaml              # 框架级配置
│   ├── strategies/                 # 策略配置目录
│   │   ├── barra.yaml
│   │   ├── llm.yaml
│   │   ├── etf_t0.yaml
│   │   ├── t1.yaml
│   │   └── industry_rotation.yaml
│   └── templates/                  # 配置模板
│       └── strategy-template.yaml
│
├── src/
│   ├── framework/
│   │   ├── __init__.py
│   │   ├── config.py               # 配置管理
│   │   ├── registry.py             # 策略注册
│   │   └── cli.py                  # 命令行入口
│   │
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── base.py                 # Pipeline 基类
│   │   ├── sources/                # 数据源实现
│   │   │   ├── __init__.py
│   │   │   ├── tushare_source.py
│   │   │   └── parquet_source.py
│   │   ├── filters/                # 数据过滤
│   │   │   └── __init__.py
│   │   └── features/               # 特征计算
│   │       ├── __init__.py
│   │       └── builtins.py
│   │
│   ├── strategy/
│   │   ├── __init__.py
│   │   ├── base.py                 # Strategy 基类
│   │   ├── signals.py              # 信号定义
│   │   ├── portfolio.py             # 组合优化
│   │   └── implementations/        # 策略实现
│   │       ├── __init__.py
│   │       ├── barra.py
│   │       ├── llm.py
│   │       ├── etf_t0.py
│   │       ├── t1.py
│   │       └── industry_rotation.py
│   │
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── base.py                 # ExecutionMode 基类
│   │   ├── backtest.py             # 回测执行
│   │   ├── live_paper.py           # Live-Paper执行
│   │   └── lean_adapter.py         # LEAN 适配器
│   │
│   └── output/
│       ├── __init__.py
│       ├── formatters.py            # 输出格式化
│       └── reporters.py             # 报告生成
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── docs/
│   ├── architecture.md
│   ├── strategy-developer-guide.md
│   └── configuration-reference.md
│
├── pyproject.toml
└── README.md
```

---

## 10. 关键设计决策

### 10.1 为什么不用子类实现策略？

**方案A**: 策略作为基类的子类  
**方案B**: 策略作为接口实现（当前推荐）

选择方案B的理由：
- 策略行为差异大（信号生成、组合优化、风控），难以用单一继承树覆盖
- 插件式架构便于运行时加载策略
- 配置驱动比代码修改更灵活

### 10.2 为什么数据管道要分层？

当前设计中，数据管道分为 DataSource → Normalizer → Filter → Feature 四层。

**每层职责**:
- DataSource: 原始数据获取（策略无关）
- Normalizer: 数据标准化（策略无关）
- Filter: Universe 过滤（低策略相关）
- Feature: 特征计算（**高策略相关**）

**好处**:
- 前两层可完全复用
- 策略只需关注特征计算
- 便于缓存优化

### 10.3 执行引擎如何兼容 LEAN？

两种方案：

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| 嵌入 | 将框架代码嵌入 LEAN 仓库 | 紧耦合，性能好 | 与 LEAN 升级同步困难 |
| **适配器（推荐）** | 框架作为外部进程调用 LEAN | 解耦，可独立演进 | 进程间通信开销 |

推荐使用适配器模式：框架作为 Python 进程，通过子进程调用 LEAN Launcher，通过 stdout/stderr 传递数据。

---

## 11. 实施路线图

### 11.1 Phase 1: 统一配置层（1周）

- [ ] 设计并实现统一配置 Schema
- [ ] 实现 Config Manager（加载、验证、合并）
- [ ] 迁移现有策略配置文件

**验收标准**: 单一配置文件可启动任意已有策略

### 11.2 Phase 2: 数据管道抽象（2周）

- [ ] 封装 ashare_live_market_cache.py 为 MarketDataSource
- [ ] 实现 DataPipeline 接口
- [ ] 实现特征计算器注册机制

**验收标准**: 新增数据源/特征只需配置，无需代码修改

### 11.3 Phase 3: 执行引擎抽象（2周）

- [ ] 封装 live_paper_runner.py 为 ExecutionEngine
- [ ] 统一 backtest 和 live-paper 的调用方式
- [ ] 实现 ResultBundle 结构化输出

**验收标准**: 同一入口可执行 backtest 和 live-paper

### 11.4 Phase 4: 策略引擎（3周）

- [ ] 实现 Strategy 接口
- [ ] 迁移现有策略为 Strategy 实现
- [ ] 实现策略注册与发现
- [ ] 开发策略调优工具（参数扫描、因子挖掘）

**验收标准**: 新策略可通过配置注册，无需复制代码

### 11.5 Phase 5: 完善与文档（1周）

- [ ] 单元测试覆盖核心模块
- [ ] 性能优化（缓存、并行）
- [ ] 编写开发者指南
- [ ] 配置参考文档

---

## 12. 预期收益

| 指标 | 当前状态 | 目标状态 | 改善 |
|------|----------|----------|------|
| 新策略接入时间 | 2-3天 | 30分钟 | 95%+ |
| 代码重复率 | ~60% | <10% | 大幅降低 |
| 配置变更复杂度 | 修改多个文件 | 修改单一配置 | 简化 |
| 故障定位时间 | 数小时 | 数分钟 | 提升可观测性 |
| 策略调优专注度 | ~40% | ~80% | 聚焦核心工作 |

---

## 附录

### A. 配置示例：Barra 策略

```yaml
framework:
  version: "1.0"
  data:
    source: "tushare"
    cache-dir: "/tmp/market-cache"
    realtime-mode: "rt_k"
  execution:
    mode: "backtest"
    dotnet-path: "/usr/local/dotnet/dotnet"
    lean-launcher: "Launcher/bin/Debug/QuantConnect.Lean.Launcher.dll"

strategy:
  type: "barra"
  parameters:
    initial-capital: 10000000
    model: "CNE5"
    factors:
      - "size"
      - "book_to_price"
      - "earnings_yield"
      - "momentum"
    universe: "all_a_share"
    rebalance-frequency: "monthly"
    risk-model: "CNE5"
    optimization: "risk_parity"

runtime:
  start-date: "2020-01-01"
  end-date: "2024-12-31"
  verbose: true
```

### B. 配置示例：行业轮动策略

```yaml
framework:
  version: "1.0"
  data:
    source: "tushare"
    cache-dir: "/tmp/market-cache"
    realtime-mode: "rt_k"
  execution:
    mode: "live-paper"
    dotnet-path: "/usr/local/dotnet/dotnet"
    lean-launcher: "Launcher/bin/Debug/QuantConnect.Lean.Launcher.dll"

strategy:
  type: "industry-rotation"
  parameters:
    initial-capital: 10000000
    plan-file: "data/alternative/ashare-industry-rotation/rotation_resonance.csv"
    benchmark-file: "data/alternative/ashare-industry-rotation/benchmark/000300.SH.csv"
    target-weight-buffer: 0.985
    monte-carlo-enabled: true
    monte-carlo-trials: 500

runtime:
  verbose: true
  output:
    trade-report: "results/trades.csv"
    daily-summary: "results/daily.csv"
```

---

**文档结束**

*本文档定义多策略统一量化框架的架构设计与实施路线，实现策略调优与流程执行的分离。*
