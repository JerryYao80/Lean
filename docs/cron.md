# SoloQuant 时间周期配置

## Pipeline 间隔

| 配置项 | 值 | 说明 |
|--------|-----|------|
| `poll-seconds` | 300 (5min) | daemon循环间隔 |
| `crawl-interval-seconds` | 28800 (8h) | 网页爬取间隔 |
| `local-ingest-interval-seconds` | 3600 (1h) | 本地策略扫描间隔 |
| `reproduce-interval-seconds` | 1800 (30min) | 复现间隔（生成代码→编译→冒烟测试） |
| `optimize-interval-seconds` | 1800 (30min) | 回测优化间隔（每次优化1个策略） |

## Pipeline 模式

| 模式 | 说明 |
|------|------|
| `--mode work` (默认) | 定时调度模式：间隔跳过、后台Popen、状态更新 |
| `--mode debug` | 调试模式：跳过所有间隔检查、LLM阶段同步执行、不更新状态 |

## Pipeline 后台阶段

LLM阶段以Popen后台进程运行，不阻塞pipeline tick：

| 阶段 | 说明 |
|------|------|
| `crawl_research` | 爬取策略和金融情报 |
| `prepare_reproduction` | 准备复现 + 分析金融情报 |
| `reproduce_one` | 生成代码→编译→冒烟测试（每次1个） |
| `optimize_backtests` | 回测优化（每次1个策略×3个版本） |

## Cron 调度

| Cron项 | 值 | 说明 |
|--------|-----|------|
| `download-history-cron` | `0 2 * * *` | 每天凌晨2点下载历史数据 |
| `download-realtime-cron` | `*/5 * * * *` | 每5分钟下载实时数据 |
| `crawl-strategy-cron` | `30 2 * * *` | 每天凌晨2:30爬取策略 |
| `crawl-news-cron` | `0 */2 * * *` | 每2小时爬取新闻 |
| `prepare-reproduction-cron` | `45 2 * * *` | 每天2:45准备复现 |
| `materialize-reproduction-cron` | `50 2 * * *` | 每天2:50物化复现 |
| `prepare-iv-data-cron` | `0 3 * * 1-5` | 工作日凌晨3点准备IV数据 |
| `analyze-finance-intelligence-cron` | `5 */2 * * *` | 每2小时分析金融情报 |
| `build-finance-event-graph-cron` | `8 */2 * * *` | 每2小时构建事件图谱 |
| `export-research-influx-cron` | `10 */2 * * *` | 每2小时导出研究数据 |
| `export-finance-event-graph-influx-cron` | `15 */2 * * *` | 每2小时导出事件图谱 |
| `export-price-ohlc-cron` | `*/10 * * * 1-5` | 工作日每10分钟导出OHLC |
| `full-auto-pipeline-cron` | `*/5 * * * *` | 每5分钟运行完整pipeline |
| `optimize-cron` | `0 4 * * 1-5` | 工作日凌晨4点优化 |
| `live-paper-cron` | `*/5 * * * 1-5` | 工作日每5分钟跑模拟盘 |

## Crawl Task 间隔

| Task | `interval-minutes` | 说明 |
|------|-----|------|
| strategy | 5 | 策略爬取轮询间隔 |
| finance_intelligence | 5 | 金融情报爬取轮询间隔 |