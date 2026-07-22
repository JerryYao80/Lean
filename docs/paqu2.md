# SoloQuant 爬取可视化设计：及时性与重磅性度量

## 一、目标

在 Grafana 中展示爬取过程的可视化，回答三个核心问题：

1. **哪个数据源** 什么时间爬取了 **哪些信息**？
2. **及时性**：信息从发布到被爬取的延迟是多少？
3. **重磅性**：爬取到的信息有多重要（LLM 评分）？

衡量爬取质量的核心指标 = **及时 × 重磅**。

---

## 二、架构

```
数据源层                         指标采集层                    可视化层
┌─────────────┐              ┌──────────────────┐         ┌──────────────┐
│ SearxNG     │──crawl──────→│                  │         │              │
│ Crawl4AI    │              │  crawl_scheduler │──写入──→│  InfluxDB    │
│ LLM 筛选    │──screen─────→│  (扩展)          │         │  (3个新      │
│             │              │                  │         │   measurement)│
├─────────────┤              ├──────────────────┤         │              │
│ arXiv API   │──api调用────→│  api_crawl_tasks │──写入──→│              │
│ Fed RSS     │              │  (新增)          │         │      ↓       │
│ FRED API    │              │                  │         │  Grafana     │
│ SEC EDGAR   │              ├──────────────────┤         │  Dashboard   │
│ CFTC COT    │              │                  │         │  (新建)      │
├─────────────┤              │  local_src_watcher│──写入──→│              │
│ local-src/  │──文件发现───→│  (新增)          │         │              │
│ (人工输入)   │              │                  │         └──────────────┘
└─────────────┘              └──────────────────┘
```

**核心原则**：复用现有 InfluxDB + Grafana 基础设施，新建独立 measurement 和 Dashboard，不修改已有功能。

---

## 三、InfluxDB 数据模型

### 3.1 Measurement: `soloquant_crawl_source`

记录每次爬取动作的元数据和指标。

```
soloquant_crawl_source,
  source_type=searxng|api|rss|local_src,
  source_name="arXiv_q-fin"|"Fed_RSS"|"local_src",
  category=strategy|finance_intelligence|data_driven
  crawled_count=5i,
  duplicate_count=2i,
  rejected_count=1i,
  screened_count=2i,
  avg_lag_minutes=45.0,
  max_lag_minutes=120i,
  avg_weight_score=7.5,
  max_weight_score=9.0,
  status_code=1i,
  status_text="ok",
  duration_seconds=12.3
  <timestamp_ns>
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `source_type` | tag | 数据源类型：searxng / api / rss / local_src |
| `source_name` | tag | 数据源名称（转义后） |
| `category` | tag | 爬取类别：strategy / finance_intelligence / data_driven |
| `crawled_count` | field(int) | 本次爬取到的条目数 |
| `duplicate_count` | field(int) | 重复条目数 |
| `rejected_count` | field(int) | 被规则拒绝的条目数 |
| `screened_count` | field(int) | 通过 LLM 筛选的条目数 |
| `avg_lag_minutes` | field(float) | 平均延迟（发布→爬取，分钟） |
| `max_lag_minutes` | field(int) | 最大延迟 |
| `avg_weight_score` | field(float) | 平均重磅评分（0-10） |
| `max_weight_score` | field(float) | 最高重磅评分 |
| `status_code` | field(int) | 1=ok, 0=skipped, 2=degraded, 3=error |
| `duration_seconds` | field(float) | 爬取耗时 |

### 3.2 Measurement: `soloquant_crawl_item`

记录每条爬取条目的详细指标（及时性 + 重磅性）。

```
soloquant_crawl_item,
  source_type=api,
  source_name="arXiv_q-fin",
  category=strategy,
  weight_tier=high|medium|low
  title_hash="a1b2c3",
  lag_minutes=30i,
  weight_score=8.5,
  screened=1i,
  is_duplicate=0i
  <timestamp_ns>
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `weight_tier` | tag | 重磅等级：high(≥7) / medium(4-7) / low(<4) |
| `title_hash` | tag | 标题哈希前6位（去重用，不存原文） |
| `lag_minutes` | field(int) | 延迟分钟数（发布→爬取） |
| `weight_score` | field(float) | 重磅评分（0-10） |
| `screened` | field(int) | 是否通过 LLM 筛选（1/0） |
| `is_duplicate` | field(int) | 是否重复（1/0） |

### 3.3 Measurement: `soloquant_local_src`

记录 local-src 文件夹中被发现的人工输入信息。

```
soloquant_local_src,
  file_ext=md|txt|pdf|url,
  category=manual
  filename_hash="d4e5f6",
  file_size_bytes=2048i,
  weight_score=9.0,
  discovered_at_lag_minutes=5i,
  processed=1i
  <timestamp_ns>
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `file_ext` | tag | 文件扩展名 |
| `category` | tag | 固定为 manual |
| `filename_hash` | tag | 文件名哈希前6位 |
| `file_size_bytes` | field(int) | 文件大小 |
| `weight_score` | field(float) | LLM 评分（对文件内容评分） |
| `discovered_at_lag_minutes` | field(int) | 文件修改时间到被发现时间的延迟 |
| `processed` | field(int) | 是否已被处理（1/0） |

---

## 四、指标定义

### 4.1 及时性（Timeliness）

```
及时性 = max(0, 100 - lag_minutes) / 100

lag_minutes = 爬取完成时间 - 信息发布时间
```

**发布时间获取方式**：

| 数据源类型 | 发布时间来源 |
|-----------|------------|
| SearxNG 搜索 | 搜索结果中的日期片段 / Crawl4AI 提取的 `<time>` / `<pubDate>` 标签 |
| arXiv API | `<published>` 字段 |
| Fed RSS | `<pubDate>` 字段 |
| FRED API | `realtime_start` 字段 |
| SEC EDGAR | `file_date` 字段 |
| local-src | 文件修改时间 `mtime` |

**及时性等级**：

| 延迟 | 等级 | 含义 |
|------|------|------|
| < 30 min | 🟢 极快 | 信息几乎实时到达 |
| 30 min – 4h | 🟢 快 | 当日发现 |
| 4h – 24h | 🟡 正常 | 隔日发现 |
| 1d – 7d | 🟠 慢 | 一周内 |
| > 7d | 🔴 过时 | 超过一周 |

### 4.2 重磅性（Weight / Importance）

```
重磅评分 = LLM 筛选评分 (0-10)
```

**评分提示词核心要求**（修复现有筛选后复用）：

```
对以下金融/量化信息进行重磅性评分（0-10）：
- 9-10: 央行决议、监管政策突变、黑天鹅事件、重大因子发现
- 7-8: 重要经济数据、头部投行策略报告、高引用学术论文
- 5-6: 常规经济指标、行业研报、中等策略论文
- 3-4: 一般市场评论、教程、平台首页
- 0-2: 营销文章、课程页面、无关内容

评分维度：
1. 信号强度：能否直接转化为交易信号或策略调整？
2. 时效敏感：信息价值是否随时间快速衰减？
3. 来源权威：发布机构的可信度和影响力
4. 稀缺性：该信息是否容易从公开渠道获取？
```

**重磅等级**：

| 评分 | 等级 | 颜色 |
|------|------|------|
| ≥ 7 | 🔴 重磅 | 高度可操作 |
| 4-7 | 🟡 常规 | 有参考价值 |
| < 4 | 🟢 低优先 | 可忽略 |

### 4.3 综合质量分数

```
质量分数 = 及时性 × 重磅评分 / 10

示例：
- Fed 利率决议，延迟 10min，评分 9.5 → 0.93 × 9.5 / 10 = 0.88
- arXiv 论文，延迟 2天，评分 6.0 → 0.33 × 6.0 / 10 = 0.20
- 营销文章，延迟 1天，评分 1.0 → 0.50 × 1.0 / 10 = 0.05
```

---

## 五、数据源扩展

### 5.1 现有数据源（SearxNG 路径）

| 数据源 | source_type | source_name | category |
|--------|------------|-------------|----------|
| arXiv q-fin | searxng | arXiv_q-fin | strategy |
| SSRN | searxng | SSRN | strategy |
| Quantpedia | searxng | Quantpedia | strategy |
| 华泰金工 | searxng | 华泰_金工研报 | strategy |
| 中信金工 | searxng | 中信_金工研报 | strategy |
| 央行监管 | searxng | 央行_监管机构 | finance_intelligence |
| 投行策略 | searxng | 投行_宏观策略 | finance_intelligence |
| 交易所公告 | searxng | 交易所公告 | finance_intelligence |
| ... | searxng | data_driven_* | data_driven |

### 5.2 新增直接 API 数据源

| 数据源 | source_type | source_name | 调用方式 | 优先级 |
|--------|------------|-------------|----------|--------|
| arXiv API | api | arXiv_API | `export.arxiv.org/api/query` | P0 |
| Fed RSS | rss | Fed_RSS | `federalreserve.gov/feeds/` | P0 |
| FRED API | api | FRED_API | `api.stlouisfed.org/fred/` | P0 |
| Semantic Scholar | api | Semantic_Scholar | `api.semanticscholar.org/` | P1 |
| SEC EDGAR | api | SEC_EDGAR | `efts.sec.gov/LATEST/` | P1 |
| CFTC COT | api | CFTC_COT | `publicreporting.cftc.gov/` | P1 |
| Kenneth French | api | Kenneth_French | ZIP 下载 | P2 |
| AQR Data | api | AQR_Data | CSV 下载 | P2 |
| ECB Data | api | ECB_API | SDMX REST | P2 |

### 5.3 新增 local-src 人工输入

**目录结构**：

```
local-src/
├── README.md              # 说明文件（如何放入信息）
├── 2026-06-19-fed-rate-decision.md      # 按日期+主题命名
├── 2026-06-19-pboc-mlf-rate.md
├── 2026-06-18-arxiv-quant-strategy.txt
├── urls/                  # 纯 URL 书签文件
│   ├── 2026-06-19-important-urls.txt    # 每行一个 URL
│   └── 2026-06-18-research-links.txt
└── clips/                 # 剪报/截图文本
    └── 2026-06-19-reuters-china-policy.txt
```

**发现机制**：

1. **文件监视器**：`local_src_watcher` Prefect task，每 5 分钟扫描 `local-src/` 目录
2. **增量发现**：记录已处理文件的 `{filename: mtime}` 到 `local-src-state.json`
3. **新文件检测**：比较当前文件列表与 state，发现新增或修改的文件
4. **内容处理**：
   - `.md` / `.txt`：直接读取内容，送 LLM 评分
   - `.url` / `urls/*.txt`：提取 URL，送 Crawl4AI 爬取内容后再评分
   - `.pdf`：提取文本（`pdftotext`），送 LLM 评分
5. **指标写入**：评分结果 + 发现延迟写入 `soloquant_local_src` measurement
6. **内容入库**：评分通过的内容复制到 `artifacts/crawled/` 目录，进入后续筛选流程

**local-src state 格式**：

```json
{
  "processed_files": {
    "2026-06-19-fed-rate-decision.md": {
      "mtime": "2026-06-19T14:30:00+08:00",
      "processed_at_utc": "2026-06-19T06:35:12Z",
      "weight_score": 9.5,
      "lag_minutes": 5,
      "status": "processed"
    }
  }
}
```

---

## 六、实现组件

### 6.1 修改 `soloquant_crawl_scheduler.py`

在现有 `strategy` / `finance_intelligence` / `data_driven_strategy` 任务中，增加 InfluxDB 指标写入：

```python
# 每个爬取任务完成后，额外写入指标
def _write_crawl_metrics(task_name, report, influx_config):
    """Write crawl source and item metrics to InfluxDB."""
    lines = []

    # Source-level metrics
    crawled = report.get("crawled", {})
    screened = report.get("screened", {})
    source_type = "searxng"  # 当前只有 SearxNG 路径
    source_name = _escape_influx_key(task_name)
    category = _infer_category(task_name)

    # Compute lag and weight from items (when available)
    avg_lag, max_lag = _compute_lag_stats(report)
    avg_weight, max_weight = _compute_weight_stats(report)

    fields = [
        f"crawled_count={crawled.get('written_count', 0)}i",
        f"duplicate_count={crawled.get('duplicate_count', 0)}i",
        f"rejected_count={crawled.get('rejected_count', 0)}i",
        f"screened_count={screened.get('written_count', 0)}i",
        f"avg_lag_minutes={avg_lag}",
        f"max_lag_minutes={max_lag}i",
        f"avg_weight_score={avg_weight}",
        f"max_weight_score={max_weight}",
        f'status_text="{report.get("status", "unknown")}"',
    ]
    status_code = {"ok": 1, "skipped": 0, "degraded": 2, "error": 3}.get(report.get("status"), -1)
    fields.append(f"status_code={status_code}i")

    ts_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
    line = f"soloquant_crawl_source,source_type={source_type},source_name={source_name},category={category} {','.join(fields)} {ts_ns}"
    lines.append(line)

    # Item-level metrics (per screened item)
    for item in report.get("screened_items", []):
        item_line = _build_item_line(item, source_type, source_name, category, ts_ns)
        lines.append(item_line)

    orchestrator.write_lines_to_influx(lines, url, org, bucket, token)
```

### 6.2 新增 `soloquant_api_crawl_tasks.py`

独立的 API 数据源爬取任务，每个数据源一个 `@task`：

```python
@task(name="crawl-arxiv-api", retries=2, retry_delay_seconds=120, timeout_seconds=300)
def crawl_arxiv_api(config: dict) -> dict:
    """Crawl arXiv q-fin + cs.LG papers via direct API."""
    # 1. 调用 arXiv API: export.arxiv.org/api/query?search_query=cat:q-fin&sortBy=submittedDate&max_results=20
    # 2. 解析 Atom feed，提取标题、摘要、发布时间、作者、arXiv ID
    # 3. 与已爬取集合去重（基于 arXiv ID）
    # 4. 新条目送 LLM 筛选 + 评分
    # 5. 写入 artifacts/crawled/
    # 6. 写入 InfluxDB soloquant_crawl_source + soloquant_crawl_item
    # 7. 返回 report

@task(name="crawl-fed-rss", retries=2, retry_delay_seconds=60, timeout_seconds=180)
def crawl_fed_rss(config: dict) -> dict:
    """Subscribe to Federal Reserve RSS feeds."""
    # 1. 读取 Fed RSS feeds
    # 2. 解析 <pubDate>，增量获取新条目
    # 3. LLM 评分
    # 4. 写入 InfluxDB

@task(name="crawl-fred-api", retries=2, retry_delay_seconds=60, timeout_seconds=300)
def crawl_fred_api(config: dict) -> dict:
    """Fetch key macro indicators from FRED API."""
    # 1. 调用 FRED API 获取关注的经济指标最新值
    # 2. 计算与前值的变化
    # 3. LLM 评分（变化幅度是否"重磅"）
    # 4. 写入 InfluxDB

def crawl_all_api_sources(config: dict) -> dict:
    """Orchestrate all API crawl tasks and aggregate results."""
    results = {}
    for task_fn in [crawl_arxiv_api, crawl_fed_rss, crawl_fred_api]:
        try:
            results[task_fn.__name__] = task_fn(config)
        except Exception as exc:
            results[task_fn.__name__] = {"status": "error", "error": str(exc)}
    return {"status": "ok", "api_sources": results}
```

### 6.3 新增 `soloquant_local_src_watcher.py`

local-src 文件夹监视器：

```python
@task(name="watch-local-src", retries=0, timeout_seconds=60)
def watch_local_src(config: dict) -> dict:
    """Scan local-src/ for new/modified files, score and index them."""
    local_src_dir = Path(config.get("workflow-root", "Results/soloquant")) / "local-src"
    state_path = local_src_dir / "local-src-state.json"

    # 1. Load state
    state = _load_state(state_path)

    # 2. Scan directory
    new_files = []
    for f in local_src_dir.rglob("*"):
        if f.is_file() and f.name in ("local-src-state.json", "README.md"):
            continue
        key = str(f.relative_to(local_src_dir))
        mtime = f.stat().st_mtime
        if key not in state or state[key]["mtime"] < mtime:
            new_files.append((f, key, mtime))

    # 3. Process each new file
    results = []
    for filepath, key, mtime in new_files:
        content = _read_content(filepath)
        score = _llm_score(content)  # 复用现有 LLM 筛选
        lag = (time.time() - mtime) / 60  # minutes since file modification

        # Write to InfluxDB
        _write_local_src_metric(filepath, score, lag)

        # If score >= threshold, copy to artifacts/crawled/
        if score >= 5.0:
            _copy_to_crawled(filepath, score)

        state[key] = {"mtime": mtime, "score": score, "lag": lag, "processed": True}
        results.append({"file": key, "score": score, "lag": lag})

    # 4. Save state
    _save_state(state_path, state)

    return {"status": "ok", "new_files": len(new_files), "results": results}
```

### 6.4 修改 `soloquant_prefect_tasks.py`

新增 API 爬取和 local-src 监视 task：

```python
@task(name="watch-local-src", retries=0, timeout_seconds=60)
def watch_local_src(config: dict) -> dict:
    """Interval: 5m. Scan local-src/ for new files."""
    if not _should_run("watch_local_src", config, "local-src-interval-seconds", 300):
        return {"status": "skipped", "reason": "interval_not_elapsed"}
    from soloquant_local_src_watcher import watch_local_src as _watch
    report = _watch(config)
    _mark_finished("watch_local_src", config, report)
    return report

@task(name="crawl-api-sources", retries=1, retry_delay_seconds=120, timeout_seconds=600)
def crawl_api_sources(config: dict) -> dict:
    """Interval: 4h. Crawl direct API sources (arXiv, Fed RSS, FRED, etc.)."""
    if not _should_run("crawl_api_sources", config, "api-crawl-interval-seconds", 14400):
        return {"status": "skipped", "reason": "interval_not_elapsed"}
    from soloquant_api_crawl_tasks import crawl_all_api_sources
    report = crawl_all_api_sources(config)
    _mark_finished("crawl_api_sources", config, report)
    return report
```

### 6.5 修改 `soloquant_prefect_flow.py`

在 PIPELINE_STAGES 中插入新阶段：

```python
PIPELINE_STAGES = (
    "ingest_local_strategies",
    "watch_local_src",           # 新增：local-src 文件发现
    "data_driven_crawl",
    "crawl_research",
    "crawl_api_sources",         # 新增：API 数据源直接爬取
    "prepare_reproduction",
    "prepare_iv_data",
    "build_event_graph",
    "build_event_signals",
    "reproduce_one",
    "materialize_variants",
    "optimize_backtests",
    "prepare_live_market_data",
    "update_lifecycle",
    "run_live_paper",
    "export_influx",
)
```

### 6.6 修复 LLM 筛选

在 `soloquant_orchestrator.py` 中修复 LLM 筛选：

1. 确保 `ZHIPU_API_KEY` / `DEEPSEEK_API_KEY` 环境变量正确传递
2. 增加 DeepSeek 作为备用 LLM 提供商
3. 筛选失败时降级为规则过滤（而非直接丢弃）
4. 筛选输出增加 0-10 评分字段

### 6.7 数据源配置外部化

将数据源配置从硬编码迁移到 JSON 文件：

**文件**：`Launcher/config/crawl-sources.json`

```json
{
  "strategy_sources": [
    {"name": "arXiv q-fin", "type": "searxng", "site_restriction": "arxiv.org", "enabled": true},
    {"name": "SSRN", "type": "searxng", "site_restriction": "ssrn.com", "enabled": true},
    {"name": "arXiv API", "type": "api", "url": "https://export.arxiv.org/api/query", "category": "q-fin", "enabled": true}
  ],
  "finance_sources": [
    {"name": "Fed RSS", "type": "rss", "url": "https://www.federalreserve.gov/feeds/press_monetary.xml", "enabled": true},
    {"name": "PBoC", "type": "searxng", "site_restriction": "pbc.gov.cn", "enabled": true}
  ],
  "api_sources": [
    {"name": "FRED API", "type": "api", "url": "https://api.stlouisfed.org/fred/", "api_key_env": "FRED_API_KEY", "enabled": true},
    {"name": "SEC EDGAR", "type": "api", "url": "https://efts.sec.gov/LATEST/", "enabled": true}
  ],
  "crawl_settings": {
    "max_queries_per_task": 10,
    "max_results_per_query": 3,
    "content_truncate_chars": 16000,
    "llm_screening_enabled": true,
    "llm_primary_provider": "zhipu",
    "llm_fallback_provider": "deepseek",
    "weight_score_threshold": 5.0
  }
}
```

---

## 七、Grafana Dashboard 设计

**文件**：`monitoring/grafana/dashboards/lean/soloquant-crawl-visibility.json`

**名称**：SoloQuant 爬取可视化

### 7.1 面板布局

```
Row 1: 爬取概览
┌─────────────────┬──────────────┬──────────────┬──────────────┐
│ 今日爬取条目总数  │ 今日重磅条目数 │ 平均及时性    │ 平均重磅评分  │
│ (stat)          │ (stat)       │ (gauge)      │ (gauge)      │
└─────────────────┴──────────────┴──────────────┴──────────────┘

Row 2: 数据源维度
┌─────────────────────────────────────────────────────────────────┐
│ 各数据源爬取量时序图 (stacked area)                              │
│ X=时间, Y=条目数, 颜色=source_name                              │
│ InfluxQL: SELECT SUM("crawled_count") FROM soloquant_crawl_source│
│           WHERE $timeFilter GROUP BY time(__interval), source_name│
└─────────────────────────────────────────────────────────────────┘

Row 3: 及时性 × 重磅性散点图
┌─────────────────────────────────────────────────────────────────┐
│ 及时性 vs 重磅性 散点图 (scatter)                                │
│ X=lag_minutes, Y=weight_score, 颜色=source_type, 大小=screened  │
│ InfluxQL: SELECT "lag_minutes","weight_score" FROM               │
│           soloquant_crawl_item WHERE $timeFilter                 │
└─────────────────────────────────────────────────────────────────┘

Row 4: 质量分数时序
┌─────────────────────────────────────────────────────────────────┐
│ 各数据源质量分数趋势 (time series)                               │
│ Y = avg_weight_score * (100 - avg_lag_minutes) / 1000           │
│ InfluxQL: SELECT "avg_weight_score" * (100 - "avg_lag_minutes")│
│           / 1000 FROM soloquant_crawl_source GROUP BY source_name│
└─────────────────────────────────────────────────────────────────┘

Row 5: 数据源健康状态
┌─────────────────────────────────────────────────────────────────┐
│ 各数据源状态表格 (table)                                         │
│ 列: source_name | type | last_crawl | status | 条目数 | 去重率  │
│     | avg_lag | avg_weight | quality_score                     │
│ InfluxQL: SELECT last(*) FROM soloquant_crawl_source            │
│           GROUP BY source_name                                   │
└─────────────────────────────────────────────────────────────────┘

Row 6: local-src 人工输入
┌─────────────────────────────────────────────────────────────────┐
│ local-src 新增文件时序 (bars)                                    │
│ + 最近处理文件列表 (table)                                       │
│ InfluxQL: SELECT * FROM soloquant_local_src WHERE $timeFilter   │
└─────────────────────────────────────────────────────────────────┘

Row 7: 重磅条目时间线
┌─────────────────────────────────────────────────────────────────┐
│ 重磅条目(≥7分) 时间线 (timeline/state timeline)                  │
│ 显示每天有哪些重磅信息被爬取到，来源是什么                        │
│ InfluxQL: SELECT "weight_score","source_name" FROM               │
│           soloquant_crawl_item WHERE weight_score >= 7           │
└─────────────────────────────────────────────────────────────────┘
```

### 7.2 模板变量

| 变量 | 类型 | 用途 |
|------|------|------|
| `source_type` | query | 按数据源类型筛选（searxng/api/rss/local_src） |
| `source_name` | query | 按具体数据源筛选 |
| `category` | query | 按爬取类别筛选（strategy/finance_intelligence/data_driven） |
| `weight_tier` | custom | 按重磅等级筛选（high/medium/low） |

---

## 八、Prefect Pipeline 集成

### 8.1 Flow 阶段顺序

```
ingest_local_strategies     # 已有
watch_local_src             # 新增：发现 local-src 中的新文件
data_driven_crawl           # 已有
crawl_research              # 已有
crawl_api_sources           # 新增：直接 API 数据源爬取
prepare_reproduction        # 已有
prepare_iv_data             # 已有
build_event_graph           # 已有
build_event_signals         # 已有
reproduce_one               # 已有
materialize_variants        # 已有
optimize_backtests          # 已有
prepare_live_market_data    # 已有
update_lifecycle            # 已有
run_live_paper              # 已有
export_influx               # 已有（扩展写入新的 measurement）
```

---

## 九、实施优先级

| 阶段 | 内容 | 依赖 | 预估工时 |
|------|------|------|----------|
| **P0** | 修复 LLM 筛选 + 评分输出 | 无 | 2h |
| **P0** | `soloquant_crawl_source` measurement 写入（现有 SearxNG 路径） | P0 LLM 修复 | 3h |
| **P0** | Grafana Dashboard 基础面板（Row 1-5） | P0 measurement | 2h |
| **P1** | `local-src/` 目录 + `watch_local_src` task | 无 | 2h |
| **P1** | `soloquant_local_src` measurement + Dashboard Row 6 | P1 local-src | 1.5h |
| **P1** | `soloquant_crawl_item` item-level metrics | P0 LLM 修复 | 2h |
| **P2** | `soloquant_api_crawl_tasks.py`（arXiv API、Fed RSS、FRED） | P0 measurement | 4h |
| **P2** | 数据源配置外部化 `crawl-sources.json` | 无 | 1.5h |
| **P2** | Dashboard Row 7 重磅时间线 | P1 item-level | 1h |

---

## 十、与现有系统的关系

| 现有组件 | 变更 |
|---------|------|
| `soloquant_prefect_influx_export.py` | 不修改，新增 measurement 由各自 task 直接写入 |
| `soloquant-prefect-pipeline.json` | 不修改，新建独立 Dashboard |
| `soloquant_prefect_flow.py` | 新增 2 个阶段（watch_local_src, crawl_api_sources） |
| `soloquant_prefect_tasks.py` | 新增 2 个 task 定义 |
| `soloquant_crawl_scheduler.py` | 扩展：增加 InfluxDB 指标写入调用 |
| `soloquant_orchestrator.py` | 修复 LLM 筛选，增加评分输出 |
| `artifacts/crawled/` | 不修改，local-src 高分内容复制进入 |
| `artifacts/screened/` | 不修改，LLM 筛选修复后自然恢复 |

**核心原则**：所有新增功能独立于现有组件，通过 InfluxDB measurement 和 Prefect task 注册集成，不修改任何已有的 measurement 或 Dashboard。
