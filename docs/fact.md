# Barra CNE5 V2 Factor Bridge 部署指南

在另一台服务器上运行 `Scripts/barra_cne5v2_factor_bridge.py` 需要的目录和文件。

## 1. 脚本文件（必须）

```
Scripts/barra_cne5v2_factor_bridge.py
data-source/tushare/barra_cne5_data_loader.py
data-source/tushare/barra_cne5_factor_builder.py
data-source/tushare/barra_cne5v2_factor_builder.py
```

脚本通过 `sys.path` 自动将 `Scripts/` 和 `data-source/tushare/` 加入模块搜索路径，因此**目录结构必须保持一致**：

```
<project-root>/
├── Scripts/
│   └── barra_cne5v2_factor_bridge.py
└── data-source/
    └── tushare/
        ├── barra_cne5_data_loader.py
        ├── barra_cne5_factor_builder.py
        └── barra_cne5v2_factor_builder.py
```

## 2. Tushare 数据目录（必须）

脚本依赖 `--tushare-data-path` 指向的 parquet 数据集。默认路径为 `/home/project/tushare-downloader/tushare_data`。

需要的子目录及大致大小：

| 子目录 | 用途 | 大小 |
|--------|------|------|
| `daily/` | 日线行情 | ~1.5G |
| `daily_basic/` | 日线指标（换手率、总市值等） | ~2.4G |
| `income/` | 利润表 | ~700M |
| `balancesheet/` | 资产负债表 | ~1.2G |
| `cashflow/` | 现金流量表 | ~855M |
| `stock_basic/` | 股票基本信息 | ~236K |
| `index_daily/` | 指数日线 | ~108M |
| `index_weight/` | 指数成分权重 | ~68M |
| `shibor/` | Shibor利率 | ~640K |
| `trade_cal/` | 交易日历 | ~16K |
| `moneyflow/` | 个股资金流 | ~2.9G |
| `fina_indicator/` | 财务指标 | ~1.2G |
| `hk_hold/` | 港资持股 | ~1.5M |
| `moneyflow_hsgt/` | 沪深港通资金流 | ~464K |
| `margin_detail/` | 融资融券明细 | ~655M |
| `cyq_perf/` | 筹码分布 | ~213M |

数据格式：每个子目录下按 `ts_code=XXXXXX/data.parquet` 或 `date=YYYYMMDD/data.parquet` 组织。

## 3. Python 依赖

```
pandas>=1.5
numpy>=1.24
tqdm>=4.60
```

无其他第三方依赖。标准库使用：`json`, `math`, `re`, `hashlib`, `time`, `argparse`, `os`, `sys`, `pathlib`, `collections`, `concurrent.futures`。

## 4. 输出目录（自动创建）

`--output-path` 指向的目录无需预先创建，脚本会自动生成。默认为 `<project-root>/Data/alternative/barra-cne5v2-factors-full/`。

输出结构：
```
output-path/
├── sse/
│   ├── 600000.parquet
│   ├── 600519.parquet
│   └── ...
└── szse/
    ├── 000001.parquet
    ├── 000725.parquet
    └── ...
```

每个 parquet 包含字段：`trade_date`, `beta`, `momentum`, `size`, `earnyld`, `resvol`, `growth`, `btop`, `leverage`, `liquidity`, `nlsize`, `moneyflow`, `quality`, `northbound`, `margin`, `chipcost`, `total_mv`, `turnover_rate`, `listed_days`, `missing_factor_count`, `is_st`。

## 5. 运行命令

```bash
# 完整回测因子构建
python3 Scripts/barra_cne5v2_factor_bridge.py \
  --tushare-data-path /path/to/tushare_data \
  --output-path /path/to/output \
  --start-date 20200101 \
  --end-date 20251231

# 单日构建
python3 Scripts/barra_cne5v2_factor_bridge.py \
  --tushare-data-path /path/to/tushare_data \
  --output-path /path/to/output \
  --date 20260606

# 随机因子生成（无需真实数据，用于测试）
python3 Scripts/barra_cne5v2_factor_bridge.py \
  --factor-source-mode random \
  --tushare-data-path /path/to/tushare_data \
  --output-path /path/to/output \
  --start-date 20200101 \
  --end-date 20251231

# 导入外部因子文件
python3 Scripts/barra_cne5v2_factor_bridge.py \
  --factor-source-mode import \
  --external-factor-path /path/to/external/factors \
  --output-path /path/to/output

# 通过配置文件运行
python3 Scripts/barra_cne5v2_factor_bridge.py --config config.json
```

## 6. 最小部署清单

```
<project-root>/
├── Scripts/
│   └── barra_cne5v2_factor_bridge.py
├── data-source/
│   └── tushare/
│       ├── barra_cne5_data_loader.py
│       ├── barra_cne5_factor_builder.py
│       └── barra_cne5v2_factor_builder.py
└── tushare_data/              # --tushare-data-path 指向此目录
    ├── daily/
    ├── daily_basic/
    ├── income/
    ├── balancesheet/
    ├── cashflow/
    ├── stock_basic/
    ├── index_daily/
    ├── index_weight/
    ├── shibor/
    ├── trade_cal/
    ├── moneyflow/
    ├── fina_indicator/
    ├── hk_hold/
    ├── moneyflow_hsgt/
    ├── margin_detail/
    └── cyq_perf/
```

Tushare 数据目录总计约 12G，可用 `rsync` 或 `scp` 传输。
