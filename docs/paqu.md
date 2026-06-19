# SoloQuant 爬取体系：数据源、配置、改进方案

## 一、高质量财经宏观信息数据源

### 1.1 央行政策与监管（信号最强）

| 数据源 | URL | 内容 | 访问方式 | 质量 |
|--------|-----|------|----------|------|
| **Fed RSS** | https://www.federalreserve.gov/feeds/ | 货币政策声明、FOMC纪要、演讲、工作论文、利率 | RSS（41个feed） | A+ |
| **ECB Data API** | https://data-api.ecb.europa.eu | 利率、汇率、HICP通胀、收益率曲线、银行监管 | REST API (SDMX) | A+ |
| **BoJ Statistics** | https://stat-search.boj.or.jp/index_en.html | TANKAN调查、利率、资金流量、货币基数 | RSS + Web | A |
| **PBoC** | https://www.pbc.gov.cn/ | MLF/LPR利率、货币政策、准备金率、监管通知 | HTML爬取 | B+ |
| **SEC EDGAR** | https://www.sec.gov/edgar | 10-K/10-Q/8-K/13-F、机构持仓、内幕交易 | REST API (JSON) | A+ |
| **CFTC COT** | https://publicreporting.cftc.gov/ | 持仓分拆（商业/非商业/管理资金），1986年至今 | REST API (CSV/XML) | A |
| **CSRC** | https://www.csrc.gov.cn/ | 法律法规、政策问答、市场统计 | HTML爬取 | B |

### 1.2 经济指标与宏观数据

| 数据源 | URL | 内容 | 访问方式 | 质量 |
|--------|-----|------|----------|------|
| **FRED** | https://api.stlouisfed.org/fred/ | 80万+时序：GDP、就业、CPI、利率、贸易、消费 | REST API（免费key） | A+ |
| **World Bank API** | https://api.worldbank.org/v2/ | 1.6万+指标：WDI、国际债务、营商环境 | REST API（无需认证） | A |
| **IMF Data API** | https://dataservices.imf.org/REST/SDMX_JSON.svc/ | IFS、WEO、国际收支、贸易方向 | SDMX REST API | A |
| **BIS Statistics** | https://data.bis.org/ | 国际银行统计、有效汇率、信贷/GDP缺口 | SDMX API + 批量下载 | A |
| **中国国家统计局** | https://data.stats.gov.cn/ | CPI、PPI、PMI、GDP、工业生产、房地产价格 | Web查询（无API） | B+ |
| **OECD Data** | https://data.oecd.org/api/ | 38国宏观指标：GDP、失业、通胀、贸易 | REST API (SDMX) | A- |

### 1.3 市场快讯与情报

| 数据源 | URL | 内容 | 访问方式 | 质量 |
|--------|-----|------|----------|------|
| **Reuters RSS** | https://www.reuters.com/rssFeed/businessNews | 商业与市场突发新闻 | RSS（免费标题+摘要） | A- |
| **金十数据 Jin10** | https://www.jin10.com/ | 实时经济数据发布、央行决议、快讯、财经日历 | WebSocket + HTTP API | A- |
| **Fed Beige Book** | https://www.federalreserve.gov/monetarypolicy/beigebook.htm | 区域经济状况（每年8次） | RSS + PDF | A |

### 1.4 央行研究论文

| 数据源 | URL | 内容 | 访问方式 | 质量 |
|--------|-----|------|----------|------|
| **Fed Working Papers** | https://www.federalreserve.gov/econres.htm | FEDS/IFDP系列：货币政策、金融稳定、银行业 | RSS + PDF | A+ |
| **BIS Working Papers** | https://www.bis.org/wpapers/index.htm | 货币政策、金融稳定、国际金融、宏观审慎监管 | RSS + PDF | A |
| **IMF Working Papers** | https://www.imf.org/en/Publications/WP | 全球宏观、金融稳定、汇率、资本流动 | Web + PDF | A |

---

## 二、高质量量化策略思路数据源

### 2.1 学术论文（核心来源）

| 数据源 | URL | 内容 | 访问方式 | 质量 |
|--------|-----|------|----------|------|
| **arXiv q-fin** | https://arxiv.org/list/q-fin/ | 投资组合优化、风险管理、交易微观结构、衍生品定价、ML金融 | REST API + RSS | A |
| **SSRN** | https://www.ssrn.com/ | 金融经济学预印本：算法交易、资产定价、市场微观结构 | Web + PDF | A |
| **NBER Working Papers** | https://www.nber.org/papers | 资产定价、公司金融、宏观、货币、国际金融 | PDF（18个月后免费） | A- |
| **RePEc/IDEAS** | https://ideas.repec.org/ | 540万+研究条目，JEL分类，引用分析 | 批量数据 | A- |
| **OpenAlex** | https://developers.openalex.org/ | 数亿篇学术作品、作者、机构、主题、引用网络 | REST API (CC0) | A |
| **Semantic Scholar** | https://api.semanticscholar.org/ | 2亿+论文，引用、摘要、TLDR摘要 | REST API | A |

### 2.2 ML/AI 预印本（策略技术来源）

| 数据源 | URL | 内容 | 访问方式 | 质量 |
|--------|-----|------|----------|------|
| **arXiv cs.LG + cs.AI** | https://arxiv.org/list/cs.LG/ | 深度学习、强化学习、NLP情感、时间序列、Transformer | REST API + RSS | A |
| **Papers With Code** | https://paperswithcode.com/ | ML论文+代码实现+基准结果，可按任务筛选 | REST API | A- |
| **NeurIPS Proceedings** | https://papers.nips.cc/ | 顶会ML论文：深度学习、RL、图神经网络 | 免费PDF | A |
| **ICML Proceedings** | https://proceedings.mlr.press/ | 顶会ML论文：表示学习、优化、序列决策 | 免费PDF (PMLR) | A |
| **OpenReview** | https://openreview.net/ | ICLR/NeurIPS同行评审+论文+讨论 | REST API | A |

### 2.3 行业研究与因子数据

| 数据源 | URL | 内容 | 访问方式 | 质量 |
|--------|-----|------|----------|------|
| **AQR Data Library** | https://www.aqr.com/Insights/Datasets | 动量、价值、BAB、质量减垃圾等因子收益 | 免费注册 + CSV | A+ |
| **Kenneth French** | https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html | FF3/5因子、动量、反转、行业组合 | 直接下载ZIP | A+ |
| **Quantpedia** | https://quantpedia.com/ | 1000+策略百科+绩效指标 | 免费约70条，完整需付费 | B+ |
| **QuantConnect** | https://www.quantconnect.com/ | 1200+共享策略，LEAN引擎，社区 | 免费账户 | A- |

### 2.4 中文量化数据源

| 数据源 | URL | 内容 | 访问方式 | 质量 |
|--------|-----|------|----------|------|
| **AKShare** | https://github.com/akfamily/akshare | A股、期货、期权、债券、基金、宏观、外汇 | Python库 (MIT) | A |
| **Tushare** | https://tushare.pro/ | A股行情、财报、产业链、新闻、宏观、期货 | Python SDK + REST API | A- |
| **BaoStock** | https://baostock.com/ | 历史K线、财务报表、宏观、行业分类 | Python库（无需注册） | B+ |
| **聚宽 JoinQuant** | https://www.joinquant.com/ | A股量化研究社区+策略分享+回测 | 免费注册 + Python API | B+ |
| **米筐 RiceQuant** | https://www.ricequant.com/ | A股量化平台+策略回测+社区 | 免费注册 | B |

---

## 三、当前项目爬取配置分析

### 3.1 当前架构

```
SearxNG (:11236) → 搜索结果URL列表
    ↓
Crawl4AI (:11235) → 全页内容提取（Markdown）
    ↓
LLM (GLM-5.1) → 筛选/评分/结构化
    ↓
artifacts/crawled/ → 持久化
```

**搜索引擎覆盖**：
- 西方：Bing、Brave、Presearch
- 中文：Baidu、Sogou（含微信）、360Search
- 新闻：Bing News、Reuters、Qwant News

### 3.2 当前问题

| 问题 | 严重程度 | 说明 |
|------|----------|------|
| **筛选层完全失效** | 🔴 严重 | `artifacts/screened/` 目录为空，GLM API key 环境变量问题导致 LLM 筛选从未成功执行 |
| **高重复率** | 🔴 严重 | 最近一次策略爬取：69个重复 vs 0个新条目，反复爬取相同URL |
| **金融情报查询语法错误** | 🔴 严重 | `build_finance_intelligence_query` 构建的查询中 `site:` 限制未正确括号化，导致逻辑错误 |
| **生产配置过度限流** | 🟡 中等 | `crawl-max-queries-per-task: 2`, `crawl-max-results-per-query: 1`，远低于调度器默认值（80/60/40） |
| **硬编码年份** | 🟡 中等 | 财经情报关键词中硬编码 "2026"，将随时间失效 |
| **内容截断** | 🟡 中等 | LLM 筛选时截断至 12,000 字符，长论文可能丢失关键信息 |
| **无跨轮去重** | 🟡 中等 | 虽然持久化时跳过重复，但每轮仍搜索和爬取相同URL，浪费资源 |
| **单LLM提供商** | 🟡 中等 | 仅配置智谱/GLM，无备用；API不可用时筛选完全失效 |
| **SearxNG无配置文件** | 🟢 低 | 仓库中无 SearxNG 配置，作为外部服务假设已运行 |

### 3.3 当前数据源配置

**策略爬取（13个命名源）**：
- arXiv q-fin、SSRN、Quantpedia、WorldQuant BRAIN、QuantConnect、Numerai、Alpha Architect、AQR
- 华泰、中信、聚宽、米筐、Uqer

**财经情报爬取（8个源）**：
- 交易所公告、官方媒体、投行研报、央行/监管机构（中美欧日）、期货交易所

**数据驱动爬取（17个策略族）**：
- momentum_reversal、value_quality、money_flow、earnings_surprise、chip_cost、etf_premium、sector_rotation、margin_signal、northbound_flow、multi_factor、analyst_signal、macro_rate、barra_momentum、barra_value、barra_quality、low_volatility、size_tilt、liquidity_premium、chip_concentration、rate_sensitivity

---

## 四、改进方案

### 4.1 "爬得到"——确保数据源可达

**问题**：SearxNG 依赖多个搜索引擎，部分引擎可能被封禁或不稳定。

**改进**：
1. **增加直接 API 数据源**，不依赖 SearxNG：
   - arXiv API（`export.arxiv.org/api/query`）— 直接获取 q-fin 和 cs.LG 论文
   - Semantic Scholar API — 引用图遍历 + TLDR 摘要
   - FRED API — 宏观数据直接获取
   - SEC EDGAR API — 监管文件直接获取
   - Fed RSS — 央行政策直接订阅

2. **SearxNG 引擎健康检查**：每次爬取前 ping 各引擎，自动禁用不可用引擎

3. **Crawl4AI 重试与代理**：对超时/403 的 URL 自动重试，支持配置 HTTP 代理

4. **RSS/Atom 直接订阅**：对 Fed、ECB、Reuters 等提供 RSS 的源，直接订阅而非通过搜索引擎

### 4.2 "爬得准"——确保内容质量

**问题**：LLM 筛选失效，规则过滤不够精确，查询语法有错误。

**改进**：
1. **修复 LLM 筛选**：
   - 确保 `ZHIPU_API_KEY` 环境变量正确传递
   - 增加备用 LLM（DeepSeek、OpenAI）作为 fallback
   - 筛选失败时降级为纯规则过滤，不直接丢弃

2. **修复金融情报查询语法**：
   - 将 `site:` 限制正确括号化
   - 分离不同源的查询，避免跨源 OR 组合

3. **增强规则预过滤**：
   - URL 级去重：维护已爬取 URL 集合（Bloom Filter），搜索结果中直接跳过
   - 内容类型过滤：根据 Content-Type 头拒绝非 HTML/PDF 响应
   - 语言检测：对非中英文内容直接跳过

4. **LLM 筛选提示词优化**：
   - 增加领域特异性：明确要求"可工程化实现的量化策略"，而非泛泛的"有价值"
   - 增加反例：在 system prompt 中列举常见误判类型（课程页面、营销文章、平台首页）
   - 增加输出约束：要求给出策略的核心信号、参数范围、数据需求

### 4.3 "爬得全"——确保覆盖广度

**问题**：重复爬取多，新发现少；数据源覆盖不够全面。

**改进**：
1. **增加高价值 API 数据源**（直接集成，不走搜索引擎）：

   ```
   新增直接 API 源：
   ├── arXiv API          → q-fin + cs.LG 论文（每日新提交）
   ├── Semantic Scholar   → 引用图扩展（从已知好论文发现相关论文）
   ├── FRED API           → 宏观指标自动获取
   ├── SEC EDGAR API      → 13-F 机构持仓变动
   ├── Fed RSS            → 央行政策实时订阅
   ├── AQR Data           → 因子收益数据
   └── Kenneth French     → FF因子数据
   ```

2. **引用图扩展**：从已采纳的论文出发，通过 Semantic Scholar API 获取引用和被引用论文，递归扩展（深度 2-3 层）

3. **跨源去重**：基于 DOI/arXiv ID/标题标准化进行跨源去重，避免同一论文从多个源重复获取

4. **增量爬取**：
   - 策略源：arXiv 按提交日期增量获取（记录上次最新日期）
   - 财经源：RSS 增量订阅（记录上次最新条目时间戳）
   - 搜索源：轮换关键词组合，避免每次使用相同查询

### 4.4 人工配置数据源

**当前**：数据源硬编码在 `soloquant_orchestrator.py` 中（STRATEGY_SOURCES、FINANCE_INTELLIGENCE_SOURCES 等）。

**改进**：将数据源配置外部化到 JSON/YAML 文件，支持运行时修改：

```json
{
  "strategy_sources": [
    {"name": "arXiv q-fin", "type": "api", "url": "https://export.arxiv.org/api/query", "category": "q-fin", "enabled": true},
    {"name": "SSRN", "type": "searxng", "site_restriction": "ssrn.com", "enabled": true},
    {"name": "Quantpedia", "type": "searxng", "site_restriction": "quantpedia.com", "enabled": true},
    {"name": "Custom Source", "type": "searxng", "site_restriction": "my-source.com", "enabled": true}
  ],
  "finance_sources": [
    {"name": "Fed RSS", "type": "rss", "url": "https://www.federalreserve.gov/feeds/press_monetary.xml", "enabled": true},
    {"name": "ECB API", "type": "api", "url": "https://data-api.ecb.europa.eu", "enabled": false}
  ],
  "crawl_settings": {
    "max_queries_per_task": 10,
    "max_results_per_query": 3,
    "content_truncate_chars": 16000,
    "llm_screening_enabled": true,
    "llm_fallback_provider": "deepseek"
  }
}
```

通过 Grafana Control API 或直接编辑此文件，用户可以：
- 启用/禁用特定数据源
- 添加自定义数据源（公司内部研究、私有 RSS 等）
- 调整爬取参数（查询数、结果数、内容截断长度）
- 切换 LLM 筛选提供商

---

## 五、数据源优先级推荐

### 立即集成（免费API，高信号）

| 优先级 | 数据源 | 集成方式 | 价值 |
|--------|--------|----------|------|
| P0 | **arXiv API** | REST API 直接调用 | 最快获取最新量化策略研究 |
| P0 | **Fed RSS** | RSS 订阅 | 央行政策实时感知 |
| P0 | **FRED API** | REST API（免费key） | 80万+宏观时序数据 |
| P1 | **Semantic Scholar API** | REST API | 从已知好论文发现相关论文 |
| P1 | **SEC EDGAR API** | REST API | 机构持仓变动、内幕交易 |
| P1 | **CFTC COT API** | REST API | 持仓分析核心数据 |
| P2 | **Kenneth French** | ZIP 直接下载 | 因子收益基准数据 |
| P2 | **AQR Data** | 免费注册下载 | 行业标准因子数据 |
| P2 | **ECB Data API** | SDMX REST API | 欧洲宏观与政策数据 |

### 中期集成（需少量开发）

| 优先级 | 数据源 | 集成方式 | 价值 |
|--------|--------|----------|------|
| P1 | **AKShare** | Python 库直接调用 | 最全面的中文市场数据 |
| P2 | **Papers With Code API** | REST API | ML论文+代码实现 |
| P2 | **OpenAlex API** | REST API | 学术引用网络分析 |
| P3 | **Jin10 WebSocket** | WebSocket | 中文实时财经快讯 |
| P3 | **RePEc 批量数据** | 批量下载 | 金融研究全文索引 |

### 长期考虑（需付费或特殊访问）

| 数据源 | 说明 |
|--------|------|
| Quantpedia Prime | 1000+策略完整访问（需付费） |
| NBER 新论文 | 18个月内需付费 |
| Bloomberg/Wind | 专业终端数据（昂贵） |
