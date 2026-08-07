# SoloQuant 量化策略全生命周期管理——功能与阶段详解

## 1. 系统总览

SoloQuant 是构建在 LEAN 算法交易引擎之上的全自动化量化策略生命周期管理系统。它从互联网爬取量化策略论文/研报，经过筛选、复现、代码生成、编译、回测、优化、Live Paper 交易，最终实现策略的上线与下架。

**核心流程图：**

```
论文/研报爬取 → GLM筛选 → 结构化复现 → 代码生成 → 编译 → 变体参数化 → 回测优化
                                                                      ↓
Grafana监控 ← InfluxDB导出 ← Live Paper交易 ← 策略上线 ← 生命周期评估
```

**关键组件：**

| 组件 | 路径 | 职责 |
|------|------|------|
| 编排器 | `Scripts/soloquant_orchestrator.py` | 核心业务逻辑（爬取、筛选、复现、代码生成、优化、导出） |
| 管道运行器 | `Scripts/soloquant_pipeline_runner.py` | 13阶段管道调度、生命周期管理、Live Paper进程管理 |
| 爬取调度器 | `Scripts/soloquant_crawl_scheduler.py` | 定时爬取策略论文与财经情报 |
| 作业运行器 | `Scripts/soloquant_job_runner.py` | Cron作业调度 |
| 配置 | `Launcher/config/config-soloquant.json` | 全局配置（GLM、SearXNG、InfluxDB、Grafana、LEAN、调度等） |

---

## 2. 管道13阶段详解

`PIPELINE_STAGES` 定义了13个顺序执行的阶段。每5分钟（`full-auto-pipeline-cron`）自动运行一次完整tick。

```
阶段编号  阶段名称                  失败处理
──────────────────────────────────────────────────
1        crawl_research            degraded（继续）
2        prepare_reproduction      degraded（继续）
3        prepare_iv_data           degraded（继续）
4        build_event_graph         degraded（继续）
5        build_event_signals       degraded（继续）
6        generate_strategy_code    degraded（继续）
7        compile_strategies        error（中止）
8        materialize_variants      error（中止）
9        optimize_backtests        degraded（继续）
10       prepare_live_market_data  error（中止）
11       update_lifecycle          error（中止）
12       run_live_paper            error（中止）
13       export_influx             degraded（继续）
```

### 2.1 crawl_research——策略论文与财经情报爬取

**触发：** 调用 `soloquant_crawl_scheduler.py --task strategy --task finance_intelligence --force --once`

**查询构建（`build_research_query_plan`）：**

- 每个关键词匹配源前缀（如 "arXiv" → `arXiv q-fin`），映射到 `site:` 限制：
  - `arXiv q-fin` → `site:arxiv.org`
  - `SSRN` → `site:papers.ssrn.com`
  - `Quantpedia` → `site:quantpedia.com`
  - `QuantConnect Community` → `site:quantconnect.com`
  - 其他源（华泰金工、中信金工、WorldQuant、Numerai）→ 无 `site:` 限制
- 未匹配关键词生成通用查询：`{keyword} quantitative strategy backtest`
- 未覆盖源通过 `hash(f"{source}-{tick_offset}") % len(keywords)` 配对一个关键词
- `tick_offset = int(now.timestamp() / 300)`，每5分钟变化，驱动查询轮换

**搜索引擎：** SearXNG（`http://localhost:11236`），获取搜索结果列表

**内容抓取：** crawl4ai（`http://localhost:11235`），从网页提取 markdown 内容

**结果过滤：**
- 策略类：`should_crawl_strategy_result()` 拒绝 arXiv 分类页（`/list/`、`/archive/`）、GitHub 仓库首页、标题含 "Quantitative Finance - arXiv" 的条目、内容不足500字符的条目
- 财经情报类：`should_crawl_finance_intelligence_result()` 拒绝非授权来源、落地页、列表页

**健康检查：** 执行前检查 SearXNG 和 crawl4ai 可达性，不可达则标记 `degraded`

### 2.2 prepare_reproduction——策略复现与财经分析

**两步执行：**

1. **策略复现摘要**（`--prepare-reproduction`）：
   - 遍历爬取的策略论文文件
   - 跨tick去重：加载已有 `index.json`，跳过URL或标题已存在的条目
   - PDF提取：下载PDF → `pdftotext` 提取文本 → 转markdown → 质量评分（标题0-3 + 表格0-2 + 公式0-3 + 方法论0-2 - 短文惩罚）
   - GLM生成结构化复现摘要
   - GLM失败时回退到本地默认模板（`build_local_reproduction_summary`）

2. **财经情报分析**（`--analyze-finance-intelligence`）：
   - 分析爬取的财经内容
   - 跨tick去重（同上模式）
   - 输出：`key_points`、`impact_analysis`、`risk_level`、`event_type`、`signal_direction`、`affected_sectors`

### 2.3 prepare_iv_data——隐含波动率数据

运行 `export_ashare_implied_volatility_data.py`，将 Tushare 期权数据转为 LEAN 兼容格式，输出到 `Data/alternative/ashare-implied-volatility/`

### 2.4 build_event_graph——财经事件图谱

从财经情报分析结果构建事件图谱：

**节点类型：**
- `event`：事件节点（货币政策、监管政策、宏观数据、市场结构、地缘政治、公司事件、情绪）
- `source`：来源节点
- `entity`：实体节点（机构、资产、风格因子、宏观指标、宏观主题）
- `theme`：主题节点（流动性、利率、风险偏好、地缘、政策）

**边类型：**
- `SOURCE_REPORTED_EVENT`：来源 → 事件
- `EVENT_IMPACTS_ENTITY`：事件 → 实体
- `EVENT_HAS_THEME`：事件 → 主题
- `EVENT_RELATED_TO_EVENT`：事件 → 事件（共享实体或主题，`relationship_strength = 共享实体数×2 + 共享主题数`）

### 2.5 build_event_signals——事件信号上下文

将事件图谱转为策略可消费的信号上下文：

```json
{
  "composite_risk_level": "high|medium|low",
  "affected_sectors": ["string"],
  "monetary_policy_signals": [...],
  "regulatory_signals": [...],
  "macro_data_signals": [...],
  "geopolitical_signals": [...],
  "sentiment_signals": [...]
}
```

信号强度映射：high risk → 1.0，medium → 0.6，low → 0.3

### 2.6 generate_strategy_code——策略代码生成

**核心函数：** `generate_strategy_implementation_package()`

**流程：**
1. 加载复现摘要JSON
2. 计算 `strategy_id = safe_slug(title)`（如 "2409-06289-automate-strategy-finding-with-llm-in-quant-investment"）
3. 检查已有manifest，存在则跳过（`skipped_existing=True`）
4. 构建GLM请求payload
5. 调用GLM（最多2次重试，5s/10s退避）
6. 规范化响应、提取 `class_name` 和 `code`
7. CSharp代码后处理（修复LLM常见错误）
8. 安全校验（禁止密钥、网络调用、文件读取等）
9. 写入代码文件与manifest

**GLM请求结构：**

```json
{
  "task": "soloquant_generate_lean_strategy_code",
  "language": "CSharp|Python",
  "goal": "根据复现摘要生成 SoloQuantGenerated* QCAlgorithm 草案",
  "expected_schema": {
    "class_name": "SoloQuantGeneratedNameAlgorithm",
    "description": "string",
    "code": "full source code",
    "parameters": {"string": "string"},
    "risk_controls": ["string"],
    "data_requirements": [{"dataset": "string", "field": "string"}]
  },
  "source": {"title": "...", "url": "...", "source": "..."},
  "reproduction_summary": {...},
  "event_signal_context": {...}  // 可选
}
```

**代码模板约束（CSharp）：**
- 类名必须以 `SoloQuantGenerated` 开头、`Algorithm` 结尾
- 命名空间：`QuantConnect.Algorithm.CSharp`
- 必须包含 Universe Selection、Alpha Model、Portfolio Construction、Risk Management、Execution Model
- 必须考虑 A 股 T+1 结算
- 必须包含 A 股费用（佣金0.03%、印花税卖出0.1%、最低100股）
- 禁止文件I/O、网络请求、进程启动、API密钥

**代码模板约束（Python）：**
- 类名规则同上
- 必须实现 `def Initialize(self)` 和 `def OnData(self, data)`
- 禁止 `import requests`、`import subprocess`、`open()`、`pd.read_csv()`

**输出manifest：**

```json
{
  "strategy_id": "string",
  "class_name": "SoloQuantGenerated*Algorithm",
  "algorithm_language": "CSharp|Python",
  "algorithm_file": "path/to/*.cs or *.py",
  "description": "string",
  "code_file": "path/to/*.cs or *.py",
  "source_summary": "path/to/reproduction/summary.json",
  "parameters": {"string": "string"},
  "risk_controls": ["string"],
  "data_requirements": [{"dataset": "string", "field": "string"}],
  "generated_at_utc": "ISO timestamp"
}
```

**代码校验（`validate_generated_strategy_code`）：**
- 递归检查metadata中的内联密钥
- 正则检查代码中的 `sk-...`、`glsa_...`、`admin-token-...`
- 类名正则 `SoloQuantGenerated[A-Za-z0-9_]*Algorithm`
- 禁止算法名单（如 `AShareLlmQuantLeanAlgorithm`）
- CSharp：禁止 `HttpClient`、`WebRequest`、`File.ReadAllText`、`StreamReader` 等
- Python：禁止 `import requests`、`import subprocess`、`open(`、`pd.read_csv(` 等

### 2.7 compile_strategies——策略编译

- **CSharp**：运行 `dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj -c Debug`
  - 编译失败时，解析错误CS文件并删除，重试编译
- **Python**：遍历 `Algorithm.Python/SoloQuantGenerated/**/*.py`，运行 `compile()` 语法检查

### 2.8 materialize_variants——变体参数化

**核心函数：** `materialize_generated_strategy_implementation()`

为每个策略创建3个参数变体，每个变体生成回测和Live Paper两套配置：

| 变体 | lookback-period | max-positions | 特点 |
|------|----------------|---------------|------|
| baseline | 原始值 | 原始值 | LLM生成的默认参数 |
| learned-v1 | max(5, baseline÷2) | max(3, baseline-2) | 激进型：更短回看、更少持仓 |
| learned-v2 | max(10, baseline×2) | max(5, baseline+2) | 保守型：更长回看、更多持仓 |

默认参数：lookback-period=20，max-positions=10

**每个变体产生：**
- `config-backtest.json`：回测配置（含蒙特卡洛模拟：500次、63天视野、5天块大小）
- `config-live-paper.json`：Live Paper配置（含共享市场数据快照路径）

**策略包manifest：**

```json
{
  "strategy-id": "string",
  "generated-at-utc": "ISO timestamp",
  "source": {
    "generated-manifest-file": "path/to/Algorithm.*/manifest.json",
    "source-summary": "path/to/reproduction/summary.json"
  },
  "template": {"algorithm-type-name": "SoloQuantGenerated*Algorithm"},
  "metadata": {
    "description": "string",
    "risk_controls": ["string"],
    "data_requirements": [{"dataset": "string", "field": "string"}]
  },
  "variants": [
    {
      "version": "baseline|learned-v1|learned-v2",
      "algorithm-type-name": "string",
      "backtest-config": "path/to/config-backtest.json",
      "live-paper-config": "path/to/config-live-paper.json",
      "score-metric": "score",
      "parameters": {"string": "string"}
    }
  ]
}
```

### 2.9 optimize_backtests——回测优化

**核心函数：** `optimize_strategy_packages()`

**流程：**
1. 遍历所有策略包manifest
2. 校验变体数量 ≥ `min-versions`（默认3）
3. 对每个变体运行LEAN回测
4. 对回测结果打分

**评分优先级（`_score_variant`）：**

```
1. Sharpe Ratio（夏普比率）
2. Calmar Ratio（卡尔马比率）
3. 配置的 metric 名称（默认 "score"）
4. strategy_total_return
5. total_return - abs(max_drawdown)（风险调整收益）
6. -∞（不可评分）
```

5. 选择最高分变体为 `best_version`
6. 写入策略注册表

### 2.10 prepare_live_market_data——Live市场数据准备

- 判断当前交易时段（开盘/收盘/盘前/盘后）
- 开盘时：使用 Tushare 实时行情
- 非交易时段：使用 GBM 模拟行情
- 输出共享快照文件：`Results/shared-live-market/ashare-live-price-snapshot.json`

### 2.11 update_lifecycle——生命周期评估

**核心函数：** `update_strategy_lifecycle()`

**三态模型：**

```
         ┌──────────────────────────────────────────┐
         │                                          │
    candidate ──── best_score ≥ threshold ────→ serving
         ↑         且不退化                          │
         │                                          │ live_score退化 > 25%
         │                                          ↓
         └──────────────────────────────────── retired
```

**转换规则：**

| 条件 | 状态 | 说明 |
|------|------|------|
| 无 live_score | candidate | 无实时数据，无法评估 |
| live_score 退化 > 25% | retired | `retire_reason: "live_score_degraded"` |
| 不退化 且 best_score ≥ 0 | serving | 策略正常运行 |
| 不退化 且 best_score < 0 | candidate | 分数不达标 |

**退化计算：** `degradation = (best_score - live_score) / abs(best_score)`

- `serving_score_threshold`：0.0（默认）
- `degradation_threshold`：0.25（默认，即25%相对回撤触发下架）

**导出到 InfluxDB：** `strategy_lifecycle` 测量（tags: strategy_id; fields: status_code, status_text, best_score, live_score, decay_ratio, degradation）

### 2.12 run_live_paper——Live Paper 交易

- 遍历策略注册表，为每个非 retired 且未运行的策略启动 LEAN 子进程
- 每个策略独立的 PID 文件、stdout/stderr 日志
- 进程以新会话启动（`start_new_session=True`）
- Live Paper 配置注入共享市场数据快照路径

### 2.13 export_influx——数据导出

运行4个导出命令：

| 命令 | 测量名 | 内容 |
|------|--------|------|
| `--export-research-influx` | `soloquant_research_artifact` | 爬取的研究成果（category, source, title, strategy_idea, evidence） |
| `--export-finance-event-graph-influx` | `soloquant_finance_event_node/edge` | 事件图谱节点和边 |
| `--export-strategy-results-influx` | `soloquant_strategy_result` | 策略回测/Live Paper结果（score, total_return, is_best等） |
| `--export-strategy-pipeline` | `soloquant_strategy_progress` + `soloquant_pipeline_funnel` | 策略管道进度 + 漏斗聚合 |

---

## 3. 策略实现模板详解

### 3.1 LEAN 配置模板（`_base_lean_config`）

**通用配置（回测与Live Paper共用）：**

```json
{
  "algorithm-type-name": "SoloQuantGenerated*Algorithm",
  "algorithm-language": "CSharp|Python",
  "algorithm-location": "DLL路径或.py路径",
  "data-folder": "<repo>/Data",
  "history-provider": "TushareHistoryProvider",
  "influxdb-enabled": true,
  "parameters": {
    "start-date": "2020-01-01",
    "end-date": "2025-12-31",
    "tushare-data-path": "/home/project/tushare-downloader/tushare_data",
    "universe": "000001.SZ,600000.SH,300750.SZ",
    "initial-capital": "1000000",
    "fee-rate": "0.0013",
    "soloquant-strategy-id": "strategy-id",
    "soloquant-optimization-version": "baseline|learned-v1|learned-v2"
  }
}
```

**回测附加配置：**

```json
{
  "environment": "backtesting",
  "monte-carlo-enabled": "true",
  "monte-carlo-trials": "500",
  "monte-carlo-horizon-days": "63",
  "monte-carlo-block-size": "5"
}
```

**Live Paper附加配置：**

```json
{
  "environment": "live-paper",
  "live-mode": true,
  "live-mode-brokerage": "PaperBrokerage",
  "data-queue-handler": ["TushareDataQueue"],
  "live-price-source-mode": "auto",
  "shared-live-market-refresh-interval-seconds": "60"
}
```

**算法路径：**
- CSharp：`Algorithm.CSharp/bin/Debug/QuantConnect.Algorithm.CSharp.dll`
- Python：`Algorithm.Python/<ClassName>.py`

### 3.2 代码生成模板

**CSharp 模板结构：**

```csharp
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Data;
using QuantConnect.Orders;
// ... 其他 using

namespace QuantConnect.Algorithm.CSharp
{
    public class SoloQuantGeneratedXxxAlgorithm : QCAlgorithm
    {
        // 参数字段
        private int _lookbackPeriod = 20;
        private int _maxPositions = 10;

        public override void Initialize()
        {
            SetStartDate(...); SetEndDate(...); SetCash(1000000);
            // Universe Selection
            SetUniverseSelection(new ManualUniverseSelectionModel(...));
            // Alpha Model
            SetAlpha(new ...AlphaModel(_lookbackPeriod));
            // Portfolio Construction
            SetPortfolioConstruction(new EqualWeightingPortfolioConstructionModel());
            // Risk Management
            SetRiskManagement(new MaximumDrawdownPercentPerSecurity(0.15m));
            // Execution
            SetExecution(new ImmediateExecutionModel());
        }

        public override void OnData(Slice data) { ... }
        public override void OnOrderEvent(OrderEvent orderEvent) { ... }
    }
}
```

**Python 模板结构：**

```python
class SoloQuantGeneratedXxxAlgorithm(QCAlgorithm):
    def Initialize(self):
        self.SetStartDate(...)
        self.SetEndDate(...)
        self.SetCash(1000000)
        # Universe, Alpha, Portfolio, Risk, Execution

    def OnData(self, data):
        ...

    def OnOrderEvent(self, orderEvent):
        ...
```

### 3.3 策略注册表结构

`strategy-registry.json` 中的每个策略条目：

```json
{
  "strategy_id": "quantitative-finance-arxiv-org",
  "best_version": "baseline",
  "best_score": 0.12,
  "live-paper-config": "/path/to/config-live-paper.json",
  "evaluated_versions": [
    {
      "version": "baseline",
      "backtest-config": "/path/to/config-backtest.json",
      "command": ["dotnet", "..."],
      "cwd": "...",
      "score": 0.12,
      "summary": {
        "total_return": 0.12,
        "total_value": 1120000,
        "total_pnl": 120000,
        "cash": 500000,
        "market_value": 620000,
        "positions": [...],
        "score": 0.12
      }
    },
    {
      "version": "learned-v1",
      "score": 0.08,
      "summary": {...}
    },
    {
      "version": "learned-v2",
      "score": 0.05,
      "summary": {...}
    }
  ],
  "updated_at_utc": "2026-05-17T02:30:00Z",
  "status": "serving",
  "lifecycle_updated_at_utc": "2026-05-17T08:00:00Z",
  "live_score": 0.10,
  "lifecycle_score": 0.10,
  "serving_since_utc": "2026-05-15T10:00:00Z"
}
```

---

## 4. 数据需求与字段映射

### 4.1 Tushare 数据集

系统通过 `tushare_field_mapping.json` 映射策略所需数据字段到 Tushare API 数据集：

**常用数据集：**

| 数据集 | 用途 | 关键字段 |
|--------|------|----------|
| `daily` | 日线行情 | ts_code, trade_date, open, high, low, close, vol, amount |
| `daily_basic` | 日线指标 | ts_code, trade_date, total_mv, pe, pb, turnover_rate |
| `moneyflow` | 资金流向 | ts_code, buy_sm_amount, sell_sm_amount, buy_lg_amount |
| `income` | 利润表 | ts_code, ann_date, revenue, n_income, eps |
| `balancesheet` | 资产负债表 | ts_code, total_assets, total_liab |
| `fina_indicator` | 财务指标 | ts_code, roe, debt_to_assets, current_ratio |
| `index_weight` | 指数权重 | index_code, con_code, weight |
| `opt_daily` | 期权日线 | ts_code, trade_date, close, settle, vol |
| `stock_basic` | 股票基础 | ts_code, name, industry, list_date |

共140+个Tushare数据集可用。

### 4.2 字段解析流程

```
策略代码中的 data_requirements
       ↓
resolve_data_requirements()
       ↓
┌──────────────────────┐
│ field_mapping.json   │ ← 直接匹配
│ 中是否存在该字段？    │
└──────┬───────────────┘
       │ 不存在
       ↓
try_supplement_missing_field()
  → SearXNG搜索 "{field} {dataset} data source"
  → crawl4ai抓取结果
       ↓
  ┌────┴────┐
  │ 匹配到   │ 未匹配到
  ↓         ↓
supplemented  missing → 写入 missing-data.jsonl
                        → materialize_missing_data_placeholders()
                          生成随机占位数据
```

---

## 5. 制品目录结构

```
Results/soloquant/
├── strategy-registry.json                    # 策略注册表
├── soloquant-pipeline-state.json             # 管道运行状态
├── soloquant-manifest.json                   # 全局清单
├── missing-data.jsonl                         # 缺失字段日志
├── artifacts/
│   ├── crawled/
│   │   ├── strategy/YYYYMMDD/
│   │   │   ├── {source}-{hash}.json          # 爬取的策略论文
│   │   │   └── index.json                    # 去重索引
│   │   └── finance_intelligence/YYYYMMDD/
│   │       ├── {source}-{hash}.json          # 爬取的财经情报
│   │       └── index.json
│   ├── screened/
│   │   ├── strategy/YYYYMMDD/
│   │   │   └── valuable-items.json           # GLM筛选后的有价值条目
│   │   └── finance_intelligence/YYYYMMDD/
│   │       └── valuable-items.json
│   ├── pdf/
│   │   └── strategy/YYYYMMDD/
│   │       ├── {source}-{slug}.pdf           # 下载的PDF
│   │       ├── {source}-{slug}.txt           # PDF提取的文本
│   │       └── {source}-{slug}.md            # Markdown格式
│   ├── reproduction/
│   │   └── strategy/YYYYMMDD/
│   │       ├── {source}-{hash}.json          # 结构化复现摘要
│   │       └── index.json
│   ├── intelligence-analysis/
│   │   └── finance_intelligence/YYYYMMDD/
│   │       ├── {event_type}-{source}-{hash}.json  # 财经分析
│   │       └── index.json
│   ├── event-graph/
│   │   └── finance_intelligence/YYYYMMDD/
│   │       └── graph.json                    # 事件图谱
│   └── event-signals/
│       └── YYYYMMDD/
│           └── signal-context.json           # 信号上下文
├── generated-code/
│   └── {strategy_id}/
│       └── manifest.json                     # 生成代码审计清单
├── strategies/
│   └── {strategy_id}/
│       ├── manifest.json                     # 策略包清单
│       ├── baseline/
│       │   ├── config-backtest.json          # 回测配置
│       │   ├── config-live-paper.json        # Live Paper配置
│       │   ├── signals.json                  # 回测信号
│       │   ├── portfolio.json                # 组合快照
│       │   ├── daily.csv                     # 日度摘要
│       │   └── summary.json                  # 回测摘要
│       ├── learned-v1/                       # 同上
│       └── learned-v2/                       # 同上
└── shared-live-market/
    └── ashare-live-price-snapshot.json       # Live市场数据快照

Algorithm.CSharp/SoloQuantGenerated/
└── {strategy_id}/
    ├── manifest.json                          # 代码生成清单
    └── SoloQuantGenerated*Algorithm.cs       # C#策略代码

Algorithm.Python/SoloQuantGenerated/
└── {strategy_id}/
    ├── manifest.json                          # 代码生成清单
    └── SoloQuantGenerated*Algorithm.py       # Python策略代码
```

---

## 6. Grafana 监控面板

### 6.1 策略管道与进度（soloquant-strategy-pipeline）

**管道漏斗：** 7个统计面板——已爬取 → 已复现 → 已生成代码 → 已回测 → Live Paper → 运行中 → 已下架

**策略列表：** 全宽表格，列：pipeline_stage, strategy_id, title, source, language, class_name, best_version, best_score, live_score, total_return, decay_ratio, version_count, risk_controls

**策略版本结果：** 表格展示每个策略的 baseline/learned-v1/learned-v2 各版本的回测和Live Paper结果

**策略收益率趋势：** 时间序列图

**管道漏斗趋势：** 各阶段数量随时间变化

### 6.2 策略全生命周期管理（strategy-lifecycle）

策略状态、性能衰减率、回测总收益率、Sharpe Ratio、权益曲线对比、衰减率趋势、Best Score vs Live Score、已下架策略记录、回测最大回撤、回测统计指标

### 6.3 SoloQuant Research Overview（soloquant-research-overview）

研究成果完整性、财经事件计数、筛选后的策略研究列表、财经情报事件列表、事件关系图

### 6.4 InfluxDB 测量汇总

| 测量名 | Tags | 关键Fields | 来源阶段 |
|--------|------|-----------|----------|
| `soloquant_research_artifact` | category, source, artifact_id | title, url, strategy_idea, evidence | crawl_research |
| `soloquant_finance_event_node` | graph_id, run_date, node_type, node_id | name, risk_level, key_point_count | build_event_graph |
| `soloquant_finance_event_edge` | graph_id, run_date, edge_type, edge_id | source, target, relationship_strength | build_event_graph |
| `soloquant_strategy_result` | strategy_id, version, stage | score, total_return, total_value, is_best | optimize_backtests |
| `soloquant_strategy_progress` | strategy_id | pipeline_stage, best_score, live_score, decay_ratio | export_influx |
| `soloquant_pipeline_funnel` | (无) | crawled_count, generated_count, serving_count 等 | export_influx |
| `strategy_lifecycle` | strategy_id | status_code, status_text, degradation, retire_reason | update_lifecycle |

---

## 7. 调度系统

### 7.1 管道调度

| 调度项 | Cron | 说明 |
|--------|------|------|
| full-auto-pipeline | `*/5 * * * *` | 完整管道tick（守护进程模式） |
| crawl-strategy | `30 2 * * *` | 爬取策略论文 |
| crawl-news | `0 */2 * * *` | 爬取财经情报 |
| prepare-reproduction | `45 2 * * *` | 准备复现摘要 |
| generate-strategy-implementations | `48 2 * * *` | 生成策略代码 |
| analyze-finance-intelligence | `5 */2 * * *` | 分析财经情报 |
| build-finance-event-graph | `8 */2 * * *` | 构建事件图谱 |
| optimize | `0 4 * * 1-5` | 优化策略回测（工作日） |
| live-paper | `*/5 * * * 1-5` | Live Paper交易（工作日） |
| export-research-influx | `10 */2 * * *` | 导出研究数据 |
| export-finance-event-graph-influx | `15 */2 * * *` | 导出事件图谱 |
| export-strategy-results-influx | `20 */2 * * *` | 导出策略结果 |
| export-price-ohlc | `*/10 * * * 1-5` | 导出行情数据（工作日） |

### 7.2 数据下载调度

| 调度项 | Cron | 说明 |
|--------|------|------|
| tushare-incremental-scheduler | `0 2 * * *` | Tushare增量下载（守护进程） |
| tushare-realtime-daily | `*/5 * * * *` | Tushare实时日线数据 |
| prepare-iv-data | `0 3 * * 1-5` | 隐含波动率数据（工作日） |

---

## 8. 策略策略（Strategy Policy）

```json
{
  "language": "CSharp",              // 代码生成语言：CSharp 或 Python
  "min-versions": 3,                 // 优化前最少变体数
  "baseline-version": "baseline",    // 基线变体名
  "required-learned-versions": 2,    // 最少学习变体数
  "best-metric": "score",            // 排名指标（实际优先用Sharpe）
  "serving-score-threshold": 0.0,    // 上线分数阈值
  "degradation-threshold": 0.25,     // 退化下架阈值（25%相对回撤）
  "avoid-algorithms": ["AShareLlmQuantLeanAlgorithm"]  // 禁止使用的算法
}
```

---

## 9. GLM 交互模板

### 9.1 筛选Prompt

**角色：** 量化系统研究筛选器

**判断标准：**
- 必须包含可理解的量化思路
- 实现步骤为加分项（非必需）
- 回测效果为加分项（非必需）
- 缺少实现/回测时标注 `confidence_level: low/medium`

**输出格式：**

```json
{
  "is_valuable": true,
  "confidence_level": "high|medium|low",
  "strategy_idea": "核心量化思路描述",
  "implementation_steps": ["步骤1", "步骤2"],
  "evidence": "支持证据",
  "required_fields": [{"dataset": "daily", "field": "close"}]
}
```

### 9.2 复现Prompt

**角色：** 量化论文复现工程师

**输出格式：**

```json
{
  "core_idea": "核心思路",
  "tradable_universe": "可交易范围",
  "rebalance_frequency": "调仓频率",
  "signals": [{"name": "momentum_14", "formula": "...", "direction": "long"}],
  "portfolio_construction": "组合构建方法",
  "risk_controls": ["5% Stop Loss", "15% Max Drawdown Limit"],
  "data_requirements": [{"dataset": "daily", "field": "close", "reason": "..."}],
  "backtest_plan": {
    "start_date": "2020-01-01",
    "end_date": "2025-12-31",
    "benchmark": "000300.SH",
    "metrics": ["sharpe", "max_drawdown", "total_return"]
  },
  "reported_results": "论文报告的结果",
  "reproduction_steps": ["步骤1", "步骤2"],
  "implementation_notes": ["注意事项"],
  "open_questions": ["待解决问题"]
}
```

### 9.3 代码生成Prompt

**角色：** LEAN C#/Python策略实现工程师

**关键约束：**
- 只输出JSON，不要解释
- 代码必须是受限的可审计草案
- 不能包含密钥、token、网络调用或文件读取
- 必须使用正确的LEAN API类型和命名空间
- 必须实现完整的LEAN模块架构

**事件信号注入（可选）：**
- 根据事件信号上下文调整持仓大小
- 根据监管信号过滤股票池

---

## 10. 爬取源配置

### 10.1 策略来源

| 源 | site:限制 | 关键词示例 |
|----|----------|-----------|
| arXiv q-fin | `site:arxiv.org` | alpha factor implementation backtest |
| SSRN | `site:papers.ssrn.com` | equity anomaly trading strategy backtest |
| Quantpedia | `site:quantpedia.com` | factor strategy implementation |
| QuantConnect Community | `site:quantconnect.com` | A-share strategy backtest |
| 华泰金工 | 无 | 多因子 研报 策略 回测 |
| 中信金工 | 无 | 量化 策略 回测 |
| 通用 | 无 | momentum factor cross-section alpha |
| 通用 | 无 | deep learning alpha generation |

### 10.2 财经情报来源

| 源 | site:限制 |
|----|----------|
| 中国人民银行 | `site:pbc.gov.cn` |
| 证监会 | `site:csrc.gov.cn` |
| 上交所 | `site:sse.com.cn` |
| 深交所 | `site:szse.cn` |
| 北交所 | `site:bse.cn` |
| 证券时报 | `site:stcn.com` |
| Reuters | `site:reuters.com` |
| JP Morgan / Morgan Stanley / Goldman Sachs | `site:jpmorgan.com OR site:morganstanley.com OR site:goldmansachs.com` |
| 中金 / 华泰 / 中信 | `site:cicc.com OR site:htsc.com.cn OR site:citics.com` |
| 美联储 | `site:federalreserve.gov` |
| SEC / CFTC | `site:sec.gov OR site:cftc.gov` |
| 欧央行 | `site:ecb.europa.eu` |
| 英格兰银行 | `site:bankofengland.co.uk` |
| 日本央行 | `site:boj.or.jp` |
| CME Group | `site:cmegroup.com` |
| ICE / CBOE / Eurex | `site:theice.com OR site:cboe.com OR site:eurex.com` |
| Euronext | `site:euronext.com` |
