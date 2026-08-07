# AShare ETF T0 Backtest And Live Flow

## 1. 现状诊断

截至 `2026-03-10`，`/home/project/hope/Lean/Data/alternative/ashare-etf-t0-live-features` 下 `119` 个 live feature CSV 的最后一个 `trade_date` 都停在 `20260203`。

抽样检查结果：

- `Data/alternative/ashare-etf-t0-live-features/sse/daily/510300.csv` 最后一行是 `20260203`
- `Data/alternative/ashare-etf-t0-live-features/sse/daily/510500.csv` 最后一行是 `20260203`
- `Data/alternative/ashare-etf-t0-live-features/szse/daily/159949.csv` 最后一行是 `20260203`

同时，底层历史日线源也停在同一天：

- `/home/project/tushare-downloader/tushare_data/fund_daily/ts_code=510300.SH/data.parquet` 最大日期是 `20260203`
- `/home/project/tushare-downloader/tushare_data/fund_daily/ts_code=510500.SH/data.parquet` 最大日期是 `20260203`
- `/home/project/tushare-downloader/tushare_data/fund_daily/ts_code=159949.SZ/data.parquet` 最大日期是 `20260203`
- 对本地 `fund_daily` 全量抽样统计，最多的尾部日期也是 `20260203`

另外，live feature CSV 的文件修改时间在 `2026-03-10 13:56` 左右，说明 bridge 在当天确实执行过 bootstrap，但没有把 `20260310` 的 `rt_min` 行写进去。

这说明当前问题不是“路径没人写”，而是：

1. bridge 启动时先把旧历史重新 materialize 到 `ashare-etf-t0-live-features`
2. 随后的 `rt_min` 刷新没有成功完成
3. 旧版本 bridge 出错时不一定会留下报告文件，所以从结果上只看得到“今天改过文件，但内容还是老数据”

本次修正后：

- `Scripts/ashare_etf_t0_feature_live_bridge.py` 启动时会保留已有的当日 live 行，不再被 bootstrap 覆盖
- bridge 失败时会写 `status=error` 报告，而不是静默停在旧数据
- `data-source/tushare/rt_min_downloader.py` 新增为独立分钟数据下载入口

## 2. 历史回测数据流

```text
/home/project/tushare-downloader/tushare_data
  -> Scripts/tushare_data_layer.py
  -> Scripts/ashare_etf_t0_feature_backtest.py
     - build_symbol_feature_frame()
     - prepare_symbol_frame()
  -> Scripts/export_ashare_etf_t0_feature_data.py
  -> Data/alternative/ashare-etf-t0-features/<market>/daily/<ticker>.csv
  -> Algorithm.CSharp/AShareEtfT0FeatureData.cs
  -> Algorithm.CSharp/AShareEtfT0FeatureIntradayAlgorithm.cs
```

关键点：

- 原始输入主要来自 `fund_daily`、`fund_nav`、`etf_share_size`、`index_daily`
- 当日特征先算出来，再统一 `shift(1)` 生成 `signal_*`
- 因此回测时策略实际使用的是“上一交易日可见”的信号，不是当天未来数据

这部分在信号定义上是正确的，核心原因是：

- `momentum_5`、`momentum_20`、`liquidity_5`、`volatility_10` 等先按日线计算
- `signal_momentum_5`、`signal_gap_abs` 等再统一 `.shift(1)`
- `AShareEtfT0FeatureIntradayAlgorithm` 消费的是 `signal_*`，不是当天刚算出的原始特征

## 3. 实时 Live-Paper 数据流

当前 live-paper 链路是：

```text
Tushare rt_min
  -> data-source/tushare/rt_min_downloader.py
     - 原始分钟 bars 标准化
     - 可选落盘到 tushare_data/rt_min/freq=.../date=.../ts_code=.../data.parquet
  -> Scripts/ashare_etf_t0_feature_live_bridge.py
     - 聚合成当日 snapshot
     - 保留历史行 + 覆盖/更新当日一行
  -> Data/alternative/ashare-etf-t0-live-features/<market>/daily/<ticker>.csv
  -> Algorithm.CSharp/AShareEtfT0FeatureData.cs
     - live 模式读取 feature_timestamp
  -> Algorithm.CSharp/AShareEtfT0FeatureIntradayAlgorithm.cs
```

bridge 的运行分两段：

1. `build_history_cache()` 从本地历史 parquet 建 base history
2. `refresh_live_feature_snapshots()` 用 `rt_min` 生成当日 live row，并写回 feature CSV

本次修正后 bridge 报告会包含：

- `status`: `ok` / `no_quotes` / `paused_outside_market_hours` / `error`
- `bootstrap_history_written_count`
- `history_cache_latest_trade_date`

如果再次出现“今天没数据”，应先看：

- `Results/ashare-etf-t0-feature-live-bridge-report.json`
- 其中的 `status`
- 其中的 `history_cache_latest_trade_date`

## 4. 交易数据是否被正确使用

结论分成两层。

### 4.1 被正确使用的部分

- `rt_min` 的 `open/high/low/close/amount/vol` 被正确聚合成当日 snapshot
- live row 的 `signal_*` 字段仍然来自上一交易日历史行，不会把当日分钟价格直接泄露进当日信号
- 这保证了“选股信号”没有明显 look-ahead

### 4.2 仍然不完全正确的部分

当前 live-paper 并不是真正的“开盘建仓，收盘平仓”实时执行链路。

原因是：

1. `AShareEtfT0FeatureIntradayAlgorithm` 每个交易日只处理一次
2. 它处理的时点取决于第一条 fresh live snapshot 到达的时间
3. 这通常已经是 `09:31` 之后，而不是真正的开盘前/开盘时
4. synthetic PnL 使用的是当前 snapshot 的 `close`，不是实际收盘价，除非策略逻辑改成接近收盘再决策

所以当前 minute 数据更多是在做：

- 当日状态快照 materialization
- live-paper 诊断和近实时观察

而不是：

- 完整的分钟级执行驱动
- 真正的开盘入场 / 收盘离场交易引擎

如果目标是严格的日内 T+0 live 执行，推荐拆成两条链：

1. 盘前计划链：只依赖前一交易日历史特征，在开盘前生成交易计划
2. 盘中执行链：用 `rt_min` 或实时盘口负责入场、监控、收盘退出和真实成交跟踪

## 5. 大数据量下的承载能力

### 当前已有的优点

- `rt_min` 拉取已经按单次最大行数限制推导安全批量
- 批量请求失败时会自动降级到单代码请求
- 新增的 minute 落盘按 `freq/date/ts_code` 分区，避免把全市场分钟数据塞进一个文件
- live feature 仍是每个标的每天一行，LEAN 自定义数据消费成本较低

### 当前主要瓶颈

- `build_history_cache()` 每次启动/换日都会把全 ETF 历史整段读进内存
- bootstrap 会遍历全 universe 重写所有 feature CSV
- bridge 每次轮询仍按全 universe 抓取 `rt_min`
- live 策略消费的是“聚合后的当日一行”，没有直接消费原始分钟序列
- feature CSV 是逐标的 CSV 文件，数量和历史长度持续增长时，启动扫描会越来越慢

### 建议的扩展方向

- 将 raw minute 数据长期保存在 `tushare_data/rt_min/...`，让 snapshot CSV 只承担 LEAN 接口适配，不承担原始数据归档
- bridge 只维护最近 `N` 个交易日的 feature CSV，而不是长期全量历史
- `history_cache` 改成懒加载或只保留滚动窗口，不每次都读全量
- live universe 先裁剪到可交易白名单，再做 `rt_min` 轮询
- 把“最后成功的 `rt_min` trade_date / feature_timestamp / quote_count”写进报告，便于做 freshness 告警

## 6. 新增分钟下载入口

新增脚本：

```text
data-source/tushare/rt_min_downloader.py
```

示例：

```bash
python3 data-source/tushare/rt_min_downloader.py \
  --ts-code 510300.SH \
  --ts-code 159949.SZ \
  --trade-date 20260310 \
  --frequency 1MIN
```

输出分区示例：

```text
/home/project/tushare-downloader/tushare_data/
  rt_min/
    freq=1MIN/
      date=20260310/
        ts_code=510300.SH/data.parquet
        ts_code=159949.SZ/data.parquet
```

这个 raw minute 分区和 `Data/alternative/ashare-etf-t0-live-features` 的职责不同：

- `tushare_data/rt_min/...`：原始分钟数据归档
- `ashare-etf-t0-live-features/...`：给 LEAN live-paper 使用的聚合特征快照
