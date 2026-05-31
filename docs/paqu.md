# SearxNG + crawl4ai 量化策略数据存储

## 根目录

```
/home/project/hope/Lean/Results/soloquant/artifacts/
```

## 目录结构

```
artifacts/
├── crawled/                          # ① 原始爬取结果
│   ├── strategy/20260516/            # 策略类网页（按日期分区）
│   │   ├── arxiv-q-fin-7f39a938.json # 单个爬取结果（JSON）
│   │   └── index.json               # 当日索引
│   └── finance_intelligence/20260511/# 财经情报类网页
│       ├── 权威财经媒体-4deaf615.json
│       └── index.json
│
├── screened/                         # ② GLM筛选后的有价值策略（当前为空）
│   └── strategy/20260516/valuable-items.json
│
├── pdf/                              # ③ 下载的PDF论文+提取文本
│   └── strategy/20260510/
│       ├── arxiv-q-fin-2409-06289.pdf
│       ├── arxiv-q-fin-2409-06289.txt
│       └── arxiv-q-fin-2409-06289.md
│
├── reproduction/                     # ④ GLM生成的策略复现摘要
│   └── strategy/20260514/
│       ├── arxiv-q-fin-3bcafaf4.json
│       └── index.json
│
├── intelligence-analysis/            # ⑤ GLM财经事件分析
│   └── finance_intelligence/20260511/
│
├── event-graph/                      # ⑥ 财经事件图谱
│   └── finance_intelligence/20260516/graph.json
│
└── event-signals/                    # ⑦ 策略可消费的信号上下文
    └── 20260516/signal-context.json
```

## 数据流

```
SearxNG搜索 → crawl4ai抓取网页 → persist_crawled_items() → crawled/
                                        ↓
                              GLM筛选(screen_with_glm) → screened/valuable-items.json
                                        ↓
                              GLM复现摘要生成 → reproduction/
                              GLM财经分析 → intelligence-analysis/
                                        ↓
                              事件图谱构建 → event-graph/
                              信号上下文构建 → event-signals/
```

## 各阶段说明

### ① 原始爬取结果 (`crawled/`)

SearxNG 搜索返回 URL 后，crawl4ai 抓取网页内容，以 JSON 格式存储。按类别和日期分区：

- `strategy/` — 量化策略论文、研报、思路
- `finance_intelligence/` — 央行公告、监管动态、交易所风险提示等

每个文件包含：搜索关键词、源 URL、爬取的正文内容、时间戳等。

### ② GLM 筛选结果 (`screened/`)

GLM 大模型对爬取内容进行筛选，判断是否对量化策略有价值。输出 `valuable-items.json`，仅保留有价值的条目。

**注意**：当前该目录为空，说明 GLM 筛选环节未成功执行（之前 pipeline 的 `GLM_API_KEY` 环境变量问题导致筛选失败）。

### ③ PDF 下载与提取 (`pdf/`)

对论文类 URL（arXiv 等），自动下载 PDF 并提取为纯文本 (.txt) 和 Markdown (.md) 格式。

### ④ GLM 策略复现摘要 (`reproduction/`)

GLM 对有价值的策略论文生成结构化的复现摘要，包含：策略逻辑、所需数据、参数建议、预期收益等。

### ⑤ GLM 财经事件分析 (`intelligence-analysis/`)

GLM 对财经情报进行结构化分析，提取关键事件、影响方向、涉及标的等。

### ⑥ 财经事件图谱 (`event-graph/`)

从多个财经分析中构建事件图谱，表示事件之间的因果和关联关系。

### ⑦ 信号上下文 (`event-signals/`)

将事件图谱转化为策略可消费的信号上下文，供运行中的策略作为决策参考。

## 其他相关路径

| 路径 | 说明 |
|------|------|
| `Results/soloquant/strategy-registry.json` | 所有策略的注册表 |
| `Results/soloquant/soloquant-pipeline-state.json` | Pipeline 运行状态 |
| `Results/soloquant/crawl-scheduler-state.json` | 爬取调度器状态 |
| `Results/soloquant/soloquant-crawl-scheduler.log` | 调度器日志 |
| `Results/soloquant/soloquant-pipeline.log` | Pipeline 日志 |
| `Results/soloquant/strategies/` | 各策略实现目录 |
| `Results/soloquant/generated-code/` | GLM 生成的 C# 策略代码 |

## 当前数据情况

- `crawled/strategy/`：2025-05-10 至 2025-05-16 有数据
- `crawled/finance_intelligence/`：2025-05-10、05-11、05-15 有数据
- `screened/`：为空（GLM 筛选未成功执行）
- `pdf/strategy/`：有 arXiv 论文下载
- `reproduction/strategy/`：有复现摘要
