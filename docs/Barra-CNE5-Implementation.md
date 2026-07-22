# Barra CNE5 实现说明

## 目标

这条链路为 A 股 Barra CNE5 因子选股和 Monte Carlo 稳健性分析提供一套独立实现，不修改现有 T+0 ETF 回测或 live-paper 路径。

## 隔离边界

- 只新增以下文件，不改 ETF T+0 算法、配置、bridge 和 live-paper 入口。
- 新因子数据目录固定为 `Data/alternative/barra-cne5-factors`。
- 新回测配置固定为 `Launcher/config/config-barra-cne5-backtest.json`。
- 新输出文件固定为 `Results/barra-cne5-*.csv` 和 `Results/barra-cne5-monte-carlo-report.*`。
- Barra 回测算法是独立的 `AShareBarraCNE5Algorithm`，不复用 ETF T+0 的信号、下单或 live 逻辑。

## 代码结构

- Python 数据层
  - `data-source/tushare/barra_cne5_data_loader.py`
  - `data-source/tushare/barra_cne5_factor_builder.py`
  - `Scripts/barra_cne5_factor_bridge.py`
  - `Scripts/barra_cne5_pipeline.py`
  - `Scripts/barra_cne5_monte_carlo.py`
- C# 算法层
  - `Algorithm.CSharp/AShareBarraCNE5FactorData.cs`
  - `Algorithm.CSharp/AShareBarraCNE5SignalModel.cs`
  - `Algorithm.CSharp/AShareBarraCNE5Algorithm.cs`
- 配置与测试
  - `Launcher/config/config-barra-cne5-backtest.json`
  - `Tests/Algorithm/AShareBarraCNE5DataTests.cs`
  - `Tests/Configuration/BarraCNE5LeanLauncherConfigTests.cs`
  - `Tests/Python/Scripts/BarraCNE5FactorBridgeTests.py`
  - `Tests/Python/Scripts/BarraCNE5MonteCarloTests.py`

## 数据流

```text
Tushare parquet
  -> BarraCNE5DataLoader
  -> BarraCNE5FactorBuilder
  -> Scripts/barra_cne5_factor_bridge.py
  -> Data/alternative/barra-cne5-factors/<sse|szse>/daily/<ticker>.csv
  -> AShareBarraCNE5FactorData
  -> AShareBarraCNE5SignalModel
  -> AShareBarraCNE5Algorithm
  -> Results/barra-cne5-*.csv
  -> Scripts/barra_cne5_monte_carlo.py
```

## 因子文件格式

每个股票一个 CSV，路径示例：

```text
Data/alternative/barra-cne5-factors/sse/daily/600000.csv
Data/alternative/barra-cne5-factors/szse/daily/000001.csv
```

列格式：

```text
trade_date,beta,momentum,size,earnyld,resvol,growth,btop,leverage,liquidity,nlsize,total_mv,turnover_rate,listed_days,missing_factor_count,is_st
```

说明：

- `trade_date` 为 `yyyyMMdd`。
- 十个 Barra 风格因子已经做过截面标准化。
- `missing_factor_count` 用于回测时过滤缺失过多的样本。
- `is_st` 用于过滤 ST 股票。

## 因子构建实现

`BarraCNE5FactorBuilder` 负责：

- 从 Tushare parquet 读取 `daily`、`daily_basic`、`income`、`balancesheet`、`cashflow`、`index_daily`、`trade_cal`、`shibor` 等数据。
- 计算可用描述子，并按 CNE5 风格因子组合成 10 个最终因子。
- 对每个描述子和最终因子做 winsorize + z-score。
- 使用 `f_ann_date` / `ann_date` 做 point-in-time 截断，避免前视。
- 将结果按股票拆分成 LEAN 自定义数据可直接读取的 CSV。
- 当前已做两类性能优化：
  - 单进程热点优化：同一 symbol/date 只构造一次长窗口收益对齐数据，避免 `beta`、`rstr`、`dastd`、`cmra` 重复读取和重复 merge
  - 并行构建：
    - 单日快照：`factor-worker-count` 在单日且大股票池时可启用最多 4 个 worker
    - 时间段构建：使用“按交易日块并发”，由 `parallel-date-block-size` 控制每个任务块包含的交易日数

当前实现对不可直接从 Tushare 获取的一致预期类因子采用重分配：

- `earnyld = 0.34 * ETOP + 0.66 * CETOP`
- `growth = 0.43 * SGRO + 0.57 * EGRO`

`barra_cne5_factor_bridge.py` 现在除了直接从 Tushare 构建因子，还支持自动导入外部因子产出：

- 支持 `csv` / `parquet`
- 支持单文件全市场快照、按股票拆分文件、已存在的 LEAN 风格目录
- 自动识别常见列名别名，例如 `date` / `ticker` / `earnings_yield` / `book_to_price`
- 自动把外部文件归一化到 `Data/alternative/barra-cne5-factors/<sse|szse>/daily/<ticker>.csv`
- 缺失的辅助列会自动补齐，例如 `missing_factor_count`、`is_st`

## 回测实现

`AShareBarraCNE5Algorithm` 是一个独立的日频 synthetic 股票回测器：

- 读取本地 A 股日线和 Barra 因子 CSV。
- 定期做截面打分、选股和目标权重生成。
- 使用独立的 A 股费用模型估算手续费、印花税和上交所过户费。
- 输出成交、日度净值、持仓分配和组合因子暴露。

关键实现点：

- Universe 默认扫描 `factor-data-path` 下的 CSV；也支持通过 `symbols` 参数显式指定。
- `RecordAllocations()` 使用最近一次截面打分缓存，避免单标的重算导致 `score` 失真。
- 因子和结果路径对齐 `Globals.DataFolder` / `Globals.ResultsDestinationFolder` 解析，避免相对路径在不同启动目录下跑偏。

## Monte Carlo 实现

`Scripts/barra_cne5_monte_carlo.py` 不重新跑 LEAN，而是基于回测输出做二次稳健性分析：

- `block_bootstrap`
  - 对 `daily-summary.csv` 中的 `net_return` 做分块 bootstrap。
- `factor_perturbation`
  - 用回测期间的组合因子暴露拟合日收益，再对因子暴露方向加入扰动。
- `combined`
  - 将上面两类路径平均合成。

输出：

- 文本报告：`Results/barra-cne5-monte-carlo-report.txt`
- JSON 报告：`Results/barra-cne5-monte-carlo-report.json`

## 自动化 Pipeline

`Scripts/barra_cne5_pipeline.py` 是推荐入口：

- 若 `Data/alternative/barra-cne5-factors` 已有标准化因子文件，则自动复用
- 若传入 `external-factor-path`，则自动导入外部因子，不要求你手工改列名或改目录
- 若未传入 `external-factor-path`，会继续检查环境变量 `BARRA_CNE5_EXTERNAL_FACTOR_PATH`
- 若仍未提供，会尝试自动扫描 `local_data/` 下常见 Barra 目录
- 若既没有现成因子也没提供外部因子，则回退到 Tushare 构建模式
- 随后自动执行 LEAN 回测和 Monte Carlo
- 最终输出 `Results/barra-cne5-pipeline-report.json`
- 运行时默认输出白盒进度：
  - 因子构建按交易日、symbol、写盘文件持续打印进度
  - 单日并行构建时会额外打印并行 worker/chunk 进度
  - 时间段并行构建时会额外打印并行 date-block 进度和 heartbeat
  - 回测阶段带 heartbeat，避免长时间静默
  - Monte Carlo 按情景和 trial 持续打印进度

## 与用户职责的边界

- 你可以自行执行 Barra 因子计算；仓库内已经提供可直接运行的 bridge 和 builder。
- 本次实现负责把因子结果桥接到 LEAN、自定义数据读取、独立回测、Monte Carlo 统计和文档补齐。
