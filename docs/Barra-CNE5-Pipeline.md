# Barra CNE5 Pipeline 脚本说明

## 文件

目标脚本：

- `Scripts/barra_cne5_pipeline.py`

## 功能概述

这个脚本是 Barra CNE5 链路的总控入口，用来把以下三个阶段自动串起来：

1. 因子准备
2. LEAN 独立回测
3. Monte Carlo 稳健性分析

它的目标不是重新实现因子计算或回测逻辑，而是自动决定“该用哪种因子来源”，然后按顺序调度现有模块执行。

## 它具体做什么

### 1. 读取配置

脚本会合并三类配置来源：

- 默认内置配置
- 配置文件 `Launcher/config/config-barra-cne5-pipeline.json`
- 命令行参数覆盖

另外还支持环境变量覆盖：

- `BARRA_CNE5_EXTERNAL_FACTOR_PATH`
- `BARRA_CNE5_TUSHARE_DATA_PATH`
- `LEAN_LAUNCHER_PATH`

### 2. 自动发现因子来源

脚本会按优先级自动决定因子阶段如何执行：

1. 如果 `Data/alternative/barra-cne5-factors` 已经有标准化因子文件，则直接复用
2. 如果给了 `external-factor-path`，则导入外部因子
3. 如果没给参数，但环境变量 `BARRA_CNE5_EXTERNAL_FACTOR_PATH` 存在，则导入该目录
4. 如果以上都没有，则尝试在这些目录自动发现外部因子：
   - `local_data/barra-cne5-factors`
   - `local_data/barra_cne5_factors`
   - `local_data/barra-cne5`
   - `local_data/barra_cne5`
   - `local_data/barra`
5. 如果仍然没有，则回退到 Tushare 本地 parquet 构建模式

### 3. 调用因子桥接脚本

它会调用 `Scripts/barra_cne5_factor_bridge.py` 完成因子准备。

这个阶段支持两种模式：

- `build`
  - 从 Tushare parquet 计算 Barra 因子并写入标准目录
- `import`
  - 导入你外部算好的因子文件，并自动标准化成 LEAN 需要的目录结构和列格式

### 4. 调用 LEAN 回测

因子准备完成后，脚本会启动：

- `Launcher/bin/Debug/QuantConnect.Lean.Launcher`

使用的配置文件默认是：

- `Launcher/config/config-barra-cne5-backtest.json`

这一阶段会产出：

- `Results/barra-cne5-trades.csv`
- `Results/barra-cne5-daily-summary.csv`
- `Results/barra-cne5-allocation.csv`
- `Results/barra-cne5-factor-exposure.csv`

### 5. 调用 Monte Carlo

回测完成后，脚本会调用 `Scripts/barra_cne5_monte_carlo.py`，使用回测输出自动生成：

- `Results/barra-cne5-monte-carlo-report.txt`
- `Results/barra-cne5-monte-carlo-report.json`

### 6. 输出 pipeline 汇总报告

脚本最后会把整条流水线的执行结果写到：

- `Results/barra-cne5-pipeline-report.json`

这个报告会记录：

- 最终采用的因子来源模式
- 外部因子路径
- 因子阶段结果
- 回测阶段结果
- Monte Carlo 阶段结果

### 7. 实时界面输出

执行脚本时，终端输出不是黑盒：

- 启动时会先打印整条 pipeline 的执行计划
- 会显示总步骤数、因子来源决策、关键路径和输出位置
- 每个阶段开始时会打印：
  - 当前阶段名
  - 阶段序号，例如 `1/3`
  - 进度百分比区间，例如 `0% -> 33%`
  - 当前关键动作
- 因子准备阶段会继续细分为白盒输出：
  - `build` 模式下，按交易日打印当前日期进度
  - 单个交易日内部按 symbol 打印进度，例如 `[build 20240131] 1500/5181`
  - 单日并行构建时会打印 `[parallel build]`，显示 worker 数、chunk 数，以及并行 chunk 完成进度
  - 时间段并行构建时会打印 `[parallel range]`，显示 worker 数、日期块大小和总 block 数
  - 时间段并行构建时会打印 `[parallel dates]`，显示已完成交易日数、block 进度和刚完成的日期段
  - 如果时间段并行某个 block 长时间未完成，会打印 `[parallel range heartbeat]`
  - 因子写盘时按 symbol 文件打印进度，例如 `[write files] 800/5181`
  - `import` 模式下按输入文件打印进度，例如 `[import files] 35/200`
- 回测阶段会继续输出：
  - 启动命令和工作目录
  - LEAN 子进程原始输出
  - 如果 LEAN 长时间无输出，pipeline 会按固定心跳打印 `[backtest heartbeat]`，说明仍在运行、已耗时多久、当前在等待 launcher 完成
- Monte Carlo 阶段会继续输出：
  - 输入文件加载情况
  - 当前正在执行的情景类型
  - trial 级进度，例如 `[block bootstrap] trials 1250/5000`
  - 报告写出位置
- 每个阶段结束时会打印：
  - 耗时
  - 关键结果摘要
  - 关键输出文件
- 如果阶段失败，会明确打印失败阶段和错误信息，并写出当前已完成部分的 pipeline 报告

默认情况下，白盒输出已经自动开启，不需要你手工适配。只有在你想降低或提高输出频率时，才需要调整这些可选参数：

- `progress-interval-symbols`
- `progress-interval-files`
- `progress-interval-trials`
- `backtest-heartbeat-seconds`
- `factor-worker-count`
- `parallel-date-block-size`

## 它不做什么

- 不修改现有 ETF T+0 回测和 live-paper 链路
- 不改变 `AShareBarraCNE5Algorithm` 的交易逻辑
- 不要求你手工改外部因子列名或目录结构
- 不在 C# 侧重新计算 Barra 因子

## 推荐使用方式

### 方式 1：零参数运行

适用于以下任一情况：

- 标准化因子已经在 `Data/alternative/barra-cne5-factors`
- 外部因子已经放在 `local_data/` 下的约定目录
- 已经设置过 `BARRA_CNE5_EXTERNAL_FACTOR_PATH`

```bash
python3 Scripts/barra_cne5_pipeline.py
```

如果你只想知道操作手册在哪里：

- 总体运行手册：`docs/Barra-CNE5-Runbook.md`
- Pipeline 功能说明：`docs/Barra-CNE5-Pipeline.md`
- 实现说明：`docs/Barra-CNE5-Implementation.md`

### 方式 2：临时指定外部因子目录

```bash
python3 Scripts/barra_cne5_pipeline.py \
  --external-factor-path /path/to/your/barra/output
```

### 方式 3：强制使用 Tushare 构建模式

```bash
python3 Scripts/barra_cne5_pipeline.py \
  --factor-source-mode build \
  --tushare-data-path /home/project/tushare-downloader/tushare_data
```

4 核 CPU 跑单日快照时，建议：

```bash
python3 Scripts/barra_cne5_pipeline.py \
  --factor-source-mode build \
  --date 20240131 \
  --factor-worker-count 4
```

说明：

- `factor-worker-count=auto` 时，脚本会在单日且股票池较大时自动尝试最多 4 个进程。
- `factor-worker-count=auto` 时，脚本在时间段构建且股票池、交易日数量足够大时，也会自动尝试并发。
- 如果当前环境不允许多进程，脚本会打印原因并回退到单进程。

4 核 CPU 跑时间段构建时，建议：

```bash
python3 Scripts/barra_cne5_pipeline.py \
  --factor-source-mode build \
  --start-date 20250102 \
  --end-date 20251231 \
  --factor-worker-count 4 \
  --parallel-date-block-size 5
```

说明：

- 时间段模式当前使用“按交易日块并发”，每天的截面标准化边界不变。
- `parallel-date-block-size` 越小，白盒进度越细，但任务调度和重复读取会更多。
- `parallel-date-block-size` 越大，进度更新更粗，但单个 block 的吞吐更高。
- 对 4 核 CPU，`5` 是较稳的起点。

### 方式 4：只跑部分阶段

只准备因子：

```bash
python3 Scripts/barra_cne5_pipeline.py --factor-only
```

只跑回测：

```bash
python3 Scripts/barra_cne5_pipeline.py --backtest-only
```

只跑 Monte Carlo：

```bash
python3 Scripts/barra_cne5_pipeline.py --monte-carlo-only
```

注意：

- `--backtest-only` 和 `--monte-carlo-only` 假设前置输出已经存在
- 如果跳过因子阶段但标准化因子目录为空，脚本会直接报错

## 适合的使用场景

- 你已经自己跑完 Barra 因子，只想自动接入 LEAN
- 你不想手工把外部因子改成 LEAN 目录结构
- 你希望把 Barra 回测和 Monte Carlo 作为一条固定流水线重复执行
- 你希望把这条链路保持和现有 ETF T+0 系统完全隔离
