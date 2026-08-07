# BL 协方差矩阵所需数据诊断

## 问题

V4 策略使用 `FixedBlackLittermanPortfolioConstructionModel`，BL 核心依赖协方差矩阵 Σ：

```
Σ = returns.Covariance().Multiply(252)
```

`returns` 矩阵由 `FormReturnsMatrix()` 从 `_symbolDataDict` 的 `RollingWindow(period=63)` 生成。当前协方差矩阵严重失真，导致 BL 输出垃圾权重。

## 根因分析

BL 的 `ReturnsSymbolData` 数据来源只有两个：

| 来源 | 调用时机 | 数据内容 | 量级 |
|------|---------|---------|------|
| `OnSecuritiesChanged` → `History()` → `Update()` | 算法启动时（一次性） | 日收益率 `(P_t/P_{t-1}) - 1` | ±0.01~0.03 |
| `DetermineTargetPercent` → `Add()` | 每次再平衡 | Insight magnitude（预期年化收益） | 0.05~0.15 |

**关键问题：A 股价格数据从 2018-01-02 开始，正好等于算法起始日期。**

`OnSecuritiesChanged` 调用 `History(63 bars)` 时，算法还没走过任何交易日，返回 0 条数据。`InitializeHistoryFromFeed` 在第一次再平衡（2018-02-01）时拿到约 22 条日收益率，远不够填满 `RollingWindow(63)`。

之后 `Add()` 每月注入 1 条 magnitude，量级是日收益率的 10 倍，且与日收益率无协方差关系。每月 1 条，63 个月后（约 2023-05），`RollingWindow` 中的日收益率被全部推出，**协方差矩阵完全由 magnitude 构成**，失去经济意义。

## 数据缺失清单

### 结论：三个数据问题全部是「转换缺失」，不是「下载缺失」

| 数据 | Tushare Pro API | tushare_data 中状态 | 转换到 LEAN 格式 | 缺失原因 |
|------|----------------|--------------------|-----------------|---------|
| pre-start 价格历史 | `daily`（支持回溯至 1990） | 已下载至 2000 年 | **未转换** | TushareDataConverter 只转换了 2018-01-02 之后的数据 |
| 复权因子 (factor_files) | `adj_factor` | 已下载（6000行/股） | **未转换** | 无 adj_factor → LEAN factor_files 的转换逻辑 |
| 映射文件 (map_files) | `namechange` | 已下载（10000行） | **未转换** | 无 namechange → LEAN map_files 的转换逻辑 |

### 1. 算法起始日之前的价格历史（最关键）

**现状**：LEAN 的 `Data/equity/sse/daily/` 和 `Data/equity/szse/daily/` 日线数据从 2018-01-02 开始。

**tushare_data 中已有**：`tushare_data/daily/` 中每只股票有约 6000 行日线数据，日期范围 2000~2026。例如 000001.SZ 的数据从 2000-08-30 开始。

**缺失环节**：`TushareDataConverter` 只将 2018-01-02 之后的数据转换为 LEAN 格式。需要修改转换器，将 tushare_data 中 2016-01-02~2017-12-31 的日线数据也写入 LEAN 的 `Data/equity/` 目录。

**需要**：至少回溯 1 年的日线数据（2017-01-02 ~ 2017-12-31），约 244 个交易日。建议回溯 2 年（2016-01-02 起）。

**影响**：
- 无此数据：BL 首次计算时协方差矩阵只有 ~22 条日收益率 + magnitude 污染
- 有此数据：BL 首次计算时协方差矩阵有 63 条纯日收益率

### 2. 复权因子文件（factor_files）

**现状**：SSE/SZSE 的 `factor_files/` 目录下只有 ETF 的因子文件，个股（600xxx、000xxx）**全部缺失**。

**tushare_data 中已有**：`tushare_data/adj_factor/` 中每只股票有约 6000 行复权因子数据。格式为 `ts_code, trade_date, adj_factor`。例如 000001.SZ 的 adj_factor 从 2001-05-14 开始。

**缺失环节**：没有将 `adj_factor` 转换为 LEAN 的 `factor_files/` 格式的转换逻辑。LEAN 的 factor_files 格式为 `date, priceFactor, splitFactor`（或等效格式），需要从 tushare 的 `adj_factor` 计算/拆分出 priceFactor 和 splitFactor。

**转换方法**：
- tushare 的 `adj_factor` 是累计复权因子（前复权 = close × adj_factor = 后复权价）
- LEAN 的 factor_files 需要 `priceFactor`（价格因子，处理分红除息）和 `splitFactor`（拆分因子，处理送股转增）
- 可以从 `adj_factor` 的日间变化推导：`daily_change = adj_factor[t] / adj_factor[t-1]`
  - 当 `daily_change ≈ 1`：无事件
  - 当 `daily_change` 对应除权除息：拆分为 priceFactor 和 splitFactor
  - 或者更简单：直接将 `adj_factor` 整体作为 `priceFactor`，`splitFactor = 1`（全复权方式）

**影响**：
- 无此数据：价格未做前复权/后复权，除权日前后价格跳空，`RateOfChange` 产生虚假收益率
- 有此数据：收益率序列连续一致，协方差矩阵准确

### 3. 映射文件（map_files）

**现状**：与 factor_files 相同，只有 ETF，个股缺失。

**tushare_data 中已有**：`tushare_data/namechange/data.parquet` 包含 10000 行更名记录。格式为 `ts_code, name, start_date, end_date, ann_date, change_reason`。

**缺失环节**：没有将 `namechange` 转换为 LEAN 的 `map_files/` 格式的转换逻辑。LEAN 的 map_files 格式通常为 `date, mapped symbol`，记录 symbol 在不同日期的映射关系。

**特别说明**：
- A 股更名相对不频繁（10000 条记录覆盖全部 A 股历史），大部分股票无需映射文件
- 退市股处理更重要，可以从 `stock_basic` 中的 `delist_date` 字段补充
- 优先级低于 pre-start 历史和复权因子

**影响**：
- 无此数据：退市股、更名股可能产生数据断点
- 有此数据：LEAN 能正确处理证券生命周期

### 4. 持续日线数据输入（架构问题，非数据问题）

**现状**：BL 的 `_symbolDataDict` **没有持续价格数据输入**。`OnSecuritiesChanged` 只在 Universe 变化时调用一次，之后只有 `Add()` 注入 magnitude。

**这是架构问题**：LEAN 原生 BL 也没有持续数据输入。`RollingWindow(63)` 中的日收益率会逐月被 `Add()` 注入的 magnitude 推出。即使提供了 pre-start 历史，63 个月后协方差矩阵仍会退化为 magnitude 的协方差。

**需要在代码层面解决**：
- 方案 A：让 BL model 订阅每日价格数据，持续 `Update()` `ReturnsSymbolData`
- 方案 B：每次再平衡时调用 `History()` 刷新日收益率，不依赖 RollingWindow 的存量
- 方案 C：分离 magnitude 注入和收益率计算，协方差矩阵仅用日收益率

## 数据现状汇总

### tushare_data 中已有的原始数据

| 数据 | Tushare API | 数据量 | 日期范围 | 存储位置 |
|------|------------|--------|---------|---------|
| 日线行情 | `daily` | ~6000行/股 | 2000~2026 | `tushare_data/daily/ts_code={code}/data.parquet` |
| 复权因子 | `adj_factor` | ~6000行/股 | 2001~2026 | `tushare_data/adj_factor/ts_code={code}/data.parquet` |
| 更名记录 | `namechange` | 10000行 | 全历史 | `tushare_data/namechange/data.parquet` |
| 股票列表 | `stock_basic` | 1个parquet | 全市场 | `tushare_data/stock_basic/data.parquet` |

### LEAN Data 中已转换的数据

| 数据 | SSE | SZSE | 备注 |
|------|-----|------|------|
| 日线行情 | 2299 只，2018-01-02 起 | 6161 只，2018-01-02 起 | 无 pre-start 历史 |
| factor_files | 0 只个股 | 0 只个股 | 仅有 ETF |
| map_files | 0 只个股 | 0 只个股 | 仅有 ETF |
| Barra 因子 | 190 只(SSE) | 0 只 | CSI300，2005 起 |

### 缺失的不是数据源，而是转换逻辑

```
tushare_data/  ───[需要转换逻辑]───>  LEAN Data/
─────────────────────────────────────────────────────
daily/         ───[扩展日期范围]───>  Data/equity/sse|szse/daily/
adj_factor/    ───[格式转换]─────>  Data/equity/sse|szse/factor_files/
namechange/    ───[格式转换]─────>  Data/equity/sse|szse/map_files/
```

## 优先级

1. **扩展日线数据转换范围**（2016-01-02 起）—— 解决 BL 首次计算的协方差矩阵质量，数据已在 tushare_data 中
2. **实现 adj_factor → factor_files 转换** —— 消除除权除息造成的虚假收益率，数据已在 tushare_data 中
3. **实现 namechange → map_files 转换** —— 正确处理证券生命周期，数据已在 tushare_data 中
4. **代码修改：持续数据输入** —— 解决 RollingWindow 被 magnitude 污染的长期退化问题（架构层面）
