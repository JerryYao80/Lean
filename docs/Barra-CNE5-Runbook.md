# Barra CNE5 运行说明

## 目标

本说明覆盖 Barra CNE5 独立链路的使用方式，包括因子桥接、LEAN 回测和 Monte Carlo 分析。该链路不会影响现有 T+0 ETF 回测和 live-paper。

## 前置条件

1. 本地 Tushare parquet 数据已准备好，例如 `/home/project/tushare-downloader/tushare_data`。
2. LEAN 已编译完成，至少包含：
   - `Algorithm.CSharp/bin/Debug/QuantConnect.Algorithm.CSharp.dll`
   - `Launcher/bin/Debug/QuantConnect.Lean.Launcher`
3. Python 环境已安装：
   - `pandas`
   - `numpy`
   - `pyarrow`

## 推荐入口

优先使用一条命令跑完整 pipeline：

默认配置文件已经提供：

- `Launcher/config/config-barra-cne5-pipeline.json`
- 脚本功能说明：`docs/Barra-CNE5-Pipeline.md`
- 实现说明：`docs/Barra-CNE5-Implementation.md`

如果你要找“操作手册”，主入口就是这份文件：

- `docs/Barra-CNE5-Runbook.md`

如果你已经自己算好了 Barra 因子，只需要给一个输入目录：

```bash
python3 Scripts/barra_cne5_pipeline.py \
  --external-factor-path /path/to/your/barra/output
```

也可以只设置一次环境变量，之后直接零参数启动：

```bash
export BARRA_CNE5_EXTERNAL_FACTOR_PATH=/path/to/your/barra/output
python3 Scripts/barra_cne5_pipeline.py
```

如果你把外部因子放在这些约定目录之一，pipeline 也会自动发现：

- `local_data/barra-cne5-factors`
- `local_data/barra_cne5_factors`
- `local_data/barra-cne5`
- `local_data/barra_cne5`
- `local_data/barra`

如果标准化因子已经在 `Data/alternative/barra-cne5-factors` 下，pipeline 会自动复用，不再重复导入。

如果你希望直接从本地 Tushare parquet 构建，再跑回测和 Monte Carlo：

```bash
python3 Scripts/barra_cne5_pipeline.py \
  --tushare-data-path /home/project/tushare-downloader/tushare_data
```

如果你是 4 核 CPU，并且当前主要在跑“单日截面”的 Barra 因子构建，可以显式指定：

```bash
python3 Scripts/barra_cne5_pipeline.py \
  --factor-source-mode build \
  --date 20240131 \
  --factor-worker-count 4
```

说明：

- `factor-worker-count` 同时作用于单日快照和时间段构建。
- 默认值是 `auto`，在股票池和交易日数量足够大时会自动尝试使用最多 4 个进程。
- 如果当前运行环境不允许多进程同步原语，脚本会明确打印告警并自动回退到单进程，不会黑盒卡死。

如果你要构建一个时间段，例如 `20250102` 到 `20251231`，当前也已经支持稳妥的并发方式：

```bash
python3 Scripts/barra_cne5_pipeline.py \
  --factor-source-mode build \
  --start-date 20250102 \
  --end-date 20251231 \
  --factor-worker-count 4 \
  --parallel-date-block-size 5
```

说明：

- 时间段模式使用的是“按交易日块并发”，不是粗暴把整段日期直接切给多个进程。
- 默认 `parallel-date-block-size=5`，表示每个任务块处理 5 个交易日。
- 这种模式更稳，因为每天的截面标准化边界不变，同时主进程可以持续显示 block 级进度。

完整 pipeline 报告会写到：

- `Results/barra-cne5-pipeline-report.json`

## 运行时可观测性

执行 `Scripts/barra_cne5_pipeline.py` 时，终端输出默认是白盒的，不需要你额外加参数：

- 因子构建
  - 会打印当前交易日
  - 会打印当前正在处理第几个 symbol
  - 会给出 symbol 进度百分比、已耗时和 ETA
  - 因子写盘时会继续打印当前写到哪个 symbol 文件
  - 单日并行模式下，会额外打印 `[parallel build]`，显示 worker 数、chunk 数和并行进度
  - 时间段并行模式下，会额外打印 `[parallel range]`、`[parallel dates]` 和 `[parallel range heartbeat]`
- 外部因子导入
  - 会打印扫描到的候选文件数
  - 会打印当前处理到第几个输入文件、接受了多少、忽略了多少
  - 标准化写盘时会继续打印 symbol 文件进度
- LEAN 回测
  - 会打印 launcher 启动命令和配置
  - 会直接透传 LEAN 自己的日志输出
  - 如果一段时间没有新输出，pipeline 会打印 heartbeat，说明回测仍在运行以及已经运行多久
- Monte Carlo
  - 会打印输入加载情况
  - 会打印当前情景类型
  - 会打印 trial 级进度、耗时和 ETA
  - 会打印报告写出位置

如需调整输出频率，可以在 `Launcher/config/config-barra-cne5-pipeline.json` 或命令行里覆盖这些可选参数：

- `progress-interval-symbols`
- `progress-interval-files`
- `progress-interval-trials`
- `backtest-heartbeat-seconds`
- `factor-worker-count`
- `parallel-date-block-size`

## 1. 构建 Barra 因子文件

如果你只想单独准备因子文件，入口仍然是：

```bash
python3 Scripts/barra_cne5_factor_bridge.py \
  --tushare-data-path /home/project/tushare-downloader/tushare_data \
  --start-date 20200101 \
  --end-date 20251231 \
  --output-path Data/alternative/barra-cne5-factors \
  --report-file Results/barra-cne5-factor-bridge-report.json
```

如果你的因子是外部产出，不需要手工改目录或改列名，可以直接导入：

```bash
python3 Scripts/barra_cne5_factor_bridge.py \
  --factor-source-mode import \
  --external-factor-path /path/to/your/barra/output \
  --output-path Data/alternative/barra-cne5-factors \
  --report-file Results/barra-cne5-factor-bridge-report.json
```

常用参数：

- `--date 20241231`
  - 只构建单日快照。
- `--universe csi300`
  - 使用 CSI 300 成分股。
- `--market-symbol 000300.SH`
  - 作为市场收益参考。

桥接完成后会生成：

- `Data/alternative/barra-cne5-factors/<sse|szse>/daily/<ticker>.csv`
- `Results/barra-cne5-factor-bridge-report.json`

## 2. 准备 LEAN 本地股票日线

Barra 回测除了因子 CSV，还依赖 LEAN 本地 A 股日线数据。算法启动时会检查：

- 因子文件是否存在
- 本地日线 zip 是否存在

缺数据的股票会被自动跳过并写日志，不会影响 ETF T+0 相关链路。

## 3. 运行独立 Barra 回测

默认配置文件：

```text
Launcher/config/config-barra-cne5-backtest.json
```

运行方式：

```bash
./Launcher/bin/Debug/QuantConnect.Lean.Launcher \
  --config Launcher/config/config-barra-cne5-backtest.json
```

如果已经使用 `Scripts/barra_cne5_pipeline.py`，这里不需要再手工执行。

关键参数位于配置的 `parameters` 段：

- `factor-data-path`
  - 默认是相对于 `data-folder` 的 `alternative/barra-cne5-factors`
- `rebalance-frequency`
  - `monthly` / `weekly` / `biweekly`
- `top-n`
  - 持仓只数
- `target-portfolio-exposure`
  - 目标总仓位
- `minimum-present-factors`
  - 单股票最少需要的有效因子数
- `max-missing-factor-count`
  - 最大允许缺失因子数
- `symbols`
  - 可选，显式指定股票池，例如 `600000.SH,000001.SZ`

默认输出：

- `Results/barra-cne5-trades.csv`
- `Results/barra-cne5-daily-summary.csv`
- `Results/barra-cne5-allocation.csv`
- `Results/barra-cne5-factor-exposure.csv`

## 4. 运行 Monte Carlo

在完成一次 Barra 回测后执行：

```bash
python3 Scripts/barra_cne5_monte_carlo.py \
  --daily-summary-file Results/barra-cne5-daily-summary.csv \
  --factor-exposure-file Results/barra-cne5-factor-exposure.csv \
  --report-file Results/barra-cne5-monte-carlo-report.txt \
  --json-report-file Results/barra-cne5-monte-carlo-report.json
```

如果已经使用 `Scripts/barra_cne5_pipeline.py`，这里也会自动执行。

常用参数：

- `--trial-count 5000`
- `--horizon-days 252`
- `--block-size 21`
- `--factor-perturbation-scale 0.15`
- `--seed 42`

输出包括：

- 年化收益中位数与 5%-95% 区间
- 最大回撤分布
- Sharpe / Calmar 中位数
- 日度 VaR / CVaR
- 总收益为负的概率

## 5. 结果解释

- `daily-summary.csv`
  - 回测净值、换手、选股数量、是否再平衡
- `allocation.csv`
  - 每次再平衡后的持仓权重、价格、截面打分
- `factor-exposure.csv`
  - 组合对 10 个 Barra 风格因子的加权暴露
- `monte-carlo-report.*`
  - 基于回测输出的稳健性统计，而不是重新驱动 LEAN 回测

## 6. 隔离保证

- 没有修改现有 ETF T+0 回测或 live-paper 配置。
- 没有修改 ETF T+0 自定义数据格式。
- 没有改动 ETF T+0 下单和执行逻辑。
- Barra 相关输出全部使用 `barra-cne5-*` 命名，目录独立。
