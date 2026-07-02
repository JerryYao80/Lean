# A股隐含波动率数据导出

## 一、脚本功能：导出 A 股隐含波动率数据（LEAN 格式）

`Scripts/export_ashare_implied_volatility_data.py` 是一个**数据导出转换脚本**，把 Tushare 的期权数据计算成隐含波动率，输出成 LEAN 回测引擎能读取的 CSV。

### 输入数据源

从 `/home/project/tushare-downloader/tushare_data_v2/` 读取：
- `opt_basic` — 期权合约基础信息
- `opt_daily` — 期权日线行情
- `fund_daily` — 50ETF/300ETF/500ETF 标的价格
- `shibor` — 无风险利率

### 计算逻辑

调用 `Scripts/ashare_implied_volatility.py`，对三个 ETF 标的（50ETF/300ETF/500ETF），每个交易日执行：
- Black-Scholes 模型反解隐含波动率
- 计算 model-free 类 VIX 指数

输出字段（13 列）：
- `atm_iv` — 平值隐含波动率（年化小数，如 0.25）
- `iv_call_25delta` / `iv_put_25delta` — 25Delta 看涨/看跌 IV
- `skew` — 偏度
- `vix` / `sigma_near` / `sigma_next` — 类 VIX 指数（百分比）
- `trade_date` — yyyyMMdd 格式

### 输出位置

```
Data/alternative/ashare-implied-volatility/sse/daily/{510050,510300,510500}.csv
```

LEAN 的 `AShareImpliedVolatilityData`（BaseData 子类）读取这些 CSV，供策略在回测/实盘中调用波动率因子。

### 底层模块额外输出

`ashare_implied_volatility.py` 另外写 InfluxDB，供 Grafana 可视化：
- `lean_ashare_iv` 测量点
- `lean_ashare_vix` 测量点
- `lean_ashare_iv_skew` 测量点

---

## 二、执行方式：SoloQuant 管线阶段调度

该脚本**本身不是独立 cron/systemd 任务**，而是 **SoloQuant 数据管线的一个阶段（stage）**。

### 调度链

```
SoloQuant pipeline daemon（常驻）
   └─ run_pipeline_tick() 循环遍历 PIPELINE_STAGES
        └─ 阶段 "prepare_iv_data"
             └─ services.prepare_iv_data()
                  └─ 子进程调用 export_ashare_implied_volatility_data.py
```

### 管线阶段顺序

`PIPELINE_STAGES`（`Scripts/soloquant_pipeline_runner.py:23`）：
```python
PIPELINE_STAGES = (
    "ingest_local_strategies",
    "data_driven_crawl",
    "crawl_research",
    "prepare_reproduction",
    "prepare_iv_data",      # ← 第 5 位
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

### 阶段实现

`prepare_iv_data()`（`soloquant_pipeline_runner.py:725`）拼出子进程命令：
```python
command = [
    sys.executable, str(CURRENT_DIR / "export_ashare_implied_volatility_data.py"),
    "--tushare-data-path", tushare_path,
    "--output-root", output_root,
]
if run_date:
    command.extend(["--start-date", str(run_date)])
```

### 执行频率

**关键**：`prepare_iv_data` 在 `run_pipeline_tick` 里**没有独立 interval 节流**（不像 ingest/crawl/reproduce 那些有 `*-interval-seconds` 配置），所以它跟随 pipeline tick 的整体节奏。每次 tick 到这一阶段就会重新导出全量 CSV（脚本幂等覆盖写）。

---

## 三、调度验证

- CSV 文件时间戳：`Jun 28 07:09-07:11`（符合今天 pipeline tick 跑过该阶段）
- 系统级定时器搜索结果：`crontab -l` / supervisor / systemd 均无该脚本
- 原因：由 SoloQuant daemon 内部驱动，而非系统级定时器

---

## 四、如何调整执行频率

如果想降低重复导出，可在 `run_pipeline_tick` 的 stage 节流块（`soloquant_pipeline_runner.py:1040-1072`）里给 `prepare_iv_data` 加类似配置：

```python
if stage_name == "prepare_iv_data" and state is not None:
    interval = max(1, orchestrator.safe_int(pipeline_config.get("iv-interval-seconds"), 28800))
    last_run = state.stage_last_run_at(stage_name)
    if last_run and not is_pipeline_due(now, last_run, interval_seconds=interval):
        logger.info("stage_skip", stage=stage_name, reason="interval_not_elapsed")
        continue
```

然后在 `config-soloquant.json` 的 `pipeline` 段加：
```json
"iv-interval-seconds": 28800
```

即可实现 8 小时节流（或任何自定义周期）。

---

## 五、相关文档

- `docs/moban.md` — 模板文档，提到运行此脚本导出 LEAN 格式数据
- `docs/midsml3.md` — IV 数据导出脚本职责表
- `docs/prefect-task.md` — Prefect 任务定义，接受 `--start-date` 参数
- `docs/vispipeline.md` — 可视化管线，提到此脚本基本不用改

---

## 六、命令行用法

```bash
# 全量导出（默认行为）
python Scripts/export_ashare_implied_volatility_data.py

# 指定时间范围
python Scripts/export_ashare_implied_volatility_data.py --start-date 20250101 --end-date 20251231

# 自定义 Tushare 数据路径
python Scripts/export_ashare_implied_volatility_data.py --tushare-data-path /path/to/tushare_data_v2

# 自定义输出路径
python Scripts/export_ashare_implied_volatility_data.py --output-root /custom/path
```

参数说明：
- `--tushare-data-path`：Tushare parquet 数据目录（默认 `/home/project/tushare-downloader/tushare_data_v2`）
- `--output-root`：LEAN CSV 输出根目录（默认 `Data/alternative/ashare-implied-volatility`）
- `--market`：市场代码（默认 `sse`，上交所）
- `--underlyings`：逗号分隔的标的代码（默认 `OP510050.SH,OP510300.SH,OP510500.SH`）
- `--start-date` / `--end-date`：时间范围过滤（YYYYMMDD）
