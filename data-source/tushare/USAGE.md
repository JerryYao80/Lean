# Tushare 数据下载器使用指南

## 功能概述

本项目已配置**185个可用接口**，覆盖以下数据类别：

| 类别 | 接口数 | 说明 |
|------|--------|------|
| stock_basic | 14 | 股票基础数据 |
| stock_quote | 14 | 股票行情数据 |
| stock_finance | 10 | 财务数据 |
| stock_reference | 9 | 参考数据 |
| stock_special | 12 | 特色数据 |
| stock_margin | 3 | 两融数据 |
| stock_moneyflow | 7 | 资金流向 |
| stock_billboard | 11 | 龙虎榜数据 |
| stock_concept | 12 | 概念板块 |
| index | 15 | 指数数据 |
| etf | 9 | ETF数据 |
| futures | 11 | 期货数据 |
| options | 2 | 期权数据 |
| cb | 11 | 可转债数据 |
| fx | 2 | 外汇数据 |
| hk | 9 | 港股数据 |
| us | 9 | 美股数据 |
| macro | 14 | 宏观经济 |
| industry | 8 | 行业数据 |
| spot | 2 | 现货数据 |
| news | 1 | 新闻数据 |

**已排除的接口（12个）：**
- 爬虫接口（realtime_tick, realtime_trans, realtime_list）
- 高积分接口（stk_mins, ft_mins）
- 专业版接口（stk_factor_pro, index_factor_pro, fund_factor_pro, cb_factor_pro）
- 高频率限制接口（news, major_news, anns）

## 快速开始

### 0. 交易日 16:00 增量更新

项目现在支持两种调度方式：

- `cron`：适合纯定时的一次性任务
- `supervisor`：适合你现在这种已经长期稳定运行的常驻托管方式

如果你已经在用 `supervisor`，更推荐运行常驻调度器：

```bash
python incremental_scheduler.py
```

它会一直驻留，每分钟检查一次，只有在交易日 16:00 之后才会触发一次增量更新；同一交易日成功后不会重复跑，失败后会按重试间隔自动补跑。

Supervisor 配置样例在：

```bash
deploy/supervisor/tushare_incremental_scheduler.conf
```

独立的一次性增量脚本仍然保留：

```bash
python incremental_update.py
```

默认行为：
- 以上交所 `trade_cal` 判断是否为交易日
- 16:00 之后更新到当日，16:00 之前更新到上一交易日
- 首次运行默认回补最近 40 个交易日
- 后续运行基于 `download_state/incremental_state.json` 做真正增量

常用命令：

```bash
# 只看计划，不写文件
python incremental_update.py --dry-run

# 指定类别增量更新
python incremental_update.py --category stock_quote,index,etf

# 强制更新到某个交易日
python incremental_update.py --target-date 20260309
```

如果要接到系统定时任务：

```bash
# 安装工作日 16:00 cron（脚本内部还会再判断是否是交易日）
bash install_incremental_cron.sh
```

如果要接到 supervisor：

```bash
cp deploy/supervisor/tushare_incremental_scheduler.conf /etc/supervisor/conf.d/
supervisorctl reread
supervisorctl update
```

执行入口脚本：

```bash
bash run_incremental_update.sh
```

### 1. 查看可用类别

```bash
python download_all.py --help
python smart_downloader.py --list
```

### 2. 快速测试（下载小数据量类别）

```bash
python download_all.py --quick
```

这将下载：fx (外汇), spot (现货), options (期权), news (新闻) 共约10个接口

### 3. 下载指定类别

```bash
# 下载股票基础数据
python download_all.py --category stock_basic

# 下载多个类别
python download_all.py --category stock_basic,stock_quote,index

# 跳过测试直接下载（更快但可能遇到权限问题）
python download_all.py --category stock_basic --no-test
```

### 4. 下载全部数据

```bash
python download_all.py
```

**⚠️ 注意：** 全量下载约185个接口，预计需要**数小时到数天**，具体取决于：
- 网络速度
- 限流配置（当前200次/分钟）
- 数据量（1990-2026年）

## 下载策略说明

### 自动分块策略

系统会根据接口类型自动选择分块策略：

1. **NONE（不分块）**：基础数据、列表数据
   - 示例：stock_basic, fund_basic, index_basic
   
2. **YEAR（按年分块）**：日线、周线、月线行情
   - 示例：daily, weekly, adj_factor
   - 下载1990-2026年，共37个分块
   
3. **QUARTER（按季度分块）**：财务数据
   - 示例：income, balancesheet, cashflow
   - 按季末日期（0331, 0630, 0930, 1231）分块
   
4. **DATE（按日分块）**：高频数据
   - 示例：cyq_chips, stk_ah_comparison
   - 仅下载2020年后数据以控制数据量

### 断点续传

- 已下载的数据会自动跳过
- 可以随时中断（Ctrl+C）并重新开始
- 数据存储在 `tushare_data/` 目录，按类别分区

### 限流保护

- 默认：200次请求/分钟
- 使用令牌桶算法自动限流
- 支持自动重试（最多5次，指数退避）

## 数据存储结构

```
tushare_data/
├── stock_basic/
│   └── data.parquet
├── daily/
│   ├── year=1990/data.parquet
│   ├── year=1991/data.parquet
│   └── ...
├── income/
│   ├── quarter=19900331/data.parquet
│   ├── quarter=19900630/data.parquet
│   └── ...
└── ...
```

## 进度监控

### 实时进度显示

下载过程中会显示：
```
进度: 10/100 (10.0%) | 成功: 8 | 跳过: 2 | 失败: 0 | 总行数: 123,456
```

### 下载报告

完成后会生成JSON报告：
```
download_reports/download_report_20260126_003000.json
```

包含：
- 每个类别的统计信息
- 每个API的下载状态
- 失败的接口及原因

## 常见问题

### Q: 下载速度慢怎么办？

A: 调整配置文件 `config.py`：
```python
MAX_REQUESTS_PER_MINUTE = 300  # 提高限流（注意不要超过账户限制）
MAX_WORKERS = 8  # 增加并发线程
```

### Q: 某个接口总是失败怎么办？

A: 检查下载报告，确认失败原因：
- 权限不足：需要升级Tushare账户
- 积分不足：需要获取更多积分
- 参数错误：检查 `api_registry.py` 中的配置

### Q: 如何只更新最新数据？

A: 现在优先使用增量脚本：

```bash
python incremental_update.py
```

如果你仍然需要用全量脚本重刷某个分区，可以删除最新年份/季度的文件后重新下载：
```bash
rm -rf tushare_data/daily/year=2026
python download_all.py --category stock_quote
```

### Q: 数据下载后如何使用？

A: 使用pandas读取parquet文件：
```python
import pandas as pd

# 读取单个文件
df = pd.read_parquet('tushare_data/stock_basic/data.parquet')

# 读取所有年份
df = pd.read_parquet('tushare_data/daily/')  # 自动合并所有分区
```

## 高级用法

### 自定义下载脚本

```python
from downloader import TushareDownloader

downloader = TushareDownloader()

# 下载单个接口
downloader.download_single_api('stock_basic')

# 下载指定年份
downloader.download_single_api('daily', year=2024)

# 批量下载
downloader.download_all(
    categories=['stock_basic', 'stock_quote'],
    priority=1  # 只下载优先级1的接口
)
```

### 添加新接口

编辑 `api_registry.py`，添加新的 `APIConfig`：

```python
APIConfig(
    api_name="new_api",
    description="新接口说明",
    chunk_strategy=ChunkStrategy.YEAR,  # 或 NONE/QUARTER/DATE
    date_field="trade_date",
    category="stock_quote",
    priority=1,
    enabled=True
)
```

## 技术支持

- Tushare官方文档：https://tushare.pro/document/2
- 项目问题：检查 `logs/` 目录下的日志文件
