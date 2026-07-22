# Tushare 因子动物园主架构设计 (Factor Zoo Master Architecture)

- **日期**: 2026-07-22
- **状态**: 设计待审 (brainstorming 产出, 尚未实现)
- **作者**: brainstorming session
- **关联**: `2026-07-22-crowding-factor-design.md` (首个新因子, 已落地 builder); `2026-07-01-factor-zoo-design.md` (原始三层架构)

## 0. 背景与现状 (经 4 路并行代码勘探确认)

四诉求: (1) 对照 tushare 文档查缺补漏、能下尽下; (2) 构建尽可能多因子充实动物园; (3) 因子随数据每日增量更新不滞后; (4) 让重构策略时有的放矢、有因子可选。

### 现状事实 (file:line 依据见各段)

- **数据基础**: 下载器在 `/home/project/tushare-downloader/`, `tushare_worker` supervisor 程序 (autorestart=true, RUNNING) 每日 16:00 CST 触发, 真增量 (`incremental_update.py:135-152` `choose_start_date` 按 `last_target_date`+lookback_trade_days=2)。已注册 173 接口, 16 个在每日核心轮转 (`CORE_SCHEDULED_APIS`)。token 走代理 URL, 15000 积分档。`cyq_perf`/`cyq_chips` 默认 `enabled=False`, 靠 17:00 cron `backfill_cyq.py` 按需回补。
- **因子动物园现状 (两套并行系统)**:
  - **系统 A — `Common/Factors/` + `FactorRegistry`**: `IFactor` 接口, 8 类别 33 因子 (Volatility/Trend/Value/Quality/Sentiment/Liquidity/Chip/Forward)。两种模式: Runtime (自算, 如 hv_20d) 和 Precomputed+InjectValue (外部算好注入, 如 crowding/chip)。
  - **系统 B — Barra CNE5 V1/V2 离线管线**: 15 因子全实现, Python builder (`data-source/tushare/barra_cne5v2_factor_builder.py`) 痗 → CSV (`Data/alternative/barra-cne5v2-factors/`) → C# `BaseData` 子类 `AddData<T>` 消费。**不进 FactorRegistry**。
- **存储碎片化**: crowding→parquet+InfluxDB; Barra→CSV; IV→CSV+InfluxDB; forward→InfluxDB。无统一读接口。
- **新鲜度断链 (诉求 3 的核心缺口)**: `tushare_worker` 下载成功后 `_run_lean_conversion()` (`incremental_scheduler.py:229-238`) 只调 LEAN 价格/拆分转换, **不调任何 alpha 因子 builder**。crowding/forward/Barra 的 `build_day` 存在且按天设计, 但无任何 daemon/cron 调用 → 因子值会滞后。无任何因子新鲜度检查。
- **选择/重构断链 (诉求 4 的核心缺口)**: `FactorRegistry.AllMetadata()` 存在但零调用者; 优化器 `manifest.yaml.parameter_space` 只声明标量 (top-n/exposure/权重), 15 个 Barra 权重写死在 config JSON, 优化器够不着; 重构 LLM `Scripts/inspiration/hypothesize.py:17-56` 的 prompt 不含因子目录 → LLM 只能凭归因数据臆造设计, 不知道动物园有什么。crowding 因子 spec 的 Task 6 (把目录塞进重构 LLM prompt) 尚未实现。

### 设计约束 (来自记忆/CLAUDE.md, 必须严守)

1. **不改现有特性**: 成熟特性永不被修改。`FactorRegistry`/Barra builder/现有策略 `AddData` 路径原码不改。
2. **新特性完全独立**: 新代码在新命名空间/新文件。
3. **LEAN native only**: 因子计算在 Python (builder), 消费/打分在 C# (FactorStore/AlphaModel)。与现有 Barra 模式一致。
4. **pipeline 必须常驻, 改后重启**: 新 daemon 入 supervisor, 改后 `supervisorctl restart`。
5. **dashboard 不改现有面板**: 新增独立面板。
6. **筹码用 cyq_perf 5 档而非 cyq_chips** (与 crowding builder 一致)。

## 1. 目标 / 范围 / 非目标

### 1.1 目标 (四诉求映射)

1. **数据基础**: 对照 tushare 文档全面核查 15000 积分档, 列出档位够但未下载/历史空缺/STALE 的表并补齐 (见 §6 数据补齐清单)。
2. **充实因子**: 首批 7 个经典风格因子 (经对抗式验证: 公式/字段/档位/重叠/PIT), 统一走新层。
3. **不滞后**: 新建 `factor_worker` supervisor 程序, 每日轮询 tushare 最新交易日 vs 各因子最新日, 落后即调 `build_day` 增量补齐, 写新鲜度状态 + Grafana 面板。
4. **有的放矢**: 生成静态 `FactorCatalog` manifest; 重构 LLM 读它选因子; `strategy_manifest.yaml` 加 `factor-include`/`factor-weight` 参数让优化器够得着。

### 1.2 范围 (本 spec 覆盖)

- 统一读接口 `FactorStore` (纯增量新类) + 4 只读适配器 (RRegistry/RBarra/RParquet/RInflux)
- `factor_worker` 守护进程 (轮询/增量补齐/新鲜度上报)
- 7 个新经典风格因子 (CSI300+500 宇宙, 见 §5)
- `FactorCatalog` 静态 manifest 生成器
- `strategy_manifest.yaml` `parameter_space` 因子参数 schema
- 重构 LLM prompt 注入 catalog 的接入点 (`Scripts/inspiration/hypothesize.py`)
- Grafana 新鲜度面板
- tushare 15000 档全面核查 + 数据补齐清单 (§6)

### 1.3 非目标 (显式排除)

- ❌ 不迁移现有因子存储格式 (crowding parquet+Influx / Barra CSV / IV CSV 原样保留)
- ❌ 不改 `FactorRegistry` 内部、不改 Barra builder 内部、不改现有策略 `AddData` 路径
- ❌ 不建动态查询服务 (catalog 仅静态 manifest 文件)
- ❌ 不做自动告警/自动触发 evolution (新鲜度仅状态文件 + Grafana 面板, 超期不自动动作)
- ❌ 首批宇宙不超 CSI300+500 (全 A 留后续批)
- ❌ 本 spec 不规定第二批及以后因子的数学 (另出路线图 spec)

## 2. 架构与组件 (方案 A: 分层适配)

```
┌─────────────────────────────────────────────────────────────┐
│ 消费层                                                        │
│  · 重构 LLM (inspiration/hypothesize.py) ← 读 FactorCatalog   │
│  · bayesian_optimizer ← 读 manifest.parameter_space          │
│  · 策略 AddData<T> ← 原样不变 (现有 CSV 路径)                 │
└──────────────────────────┬──────────────────────────────────┘
                           │ 查询
┌──────────────────────────┴──────────────────────────────────┐
│ FactorStore  (Common/Factors/Store/, 纯增量新类, C#)          │
│  · Get(id, sym, date) → FactorResult                         │
│  · FreshnessReport() → dict[factor_id, last_date]            │
│  · AllMetadata() → 复用现有 FactorMetadata 结构               │
│  路由到适配器: factor_id 前缀/注册决定                         │
└──┬───────────┬───────────────┬──────────────┬───────────────┘
   │           │               │              │
┌──▼──────┐ ┌──▼─────────┐ ┌──▼──────────┐ ┌─▼────────────┐
│RRegistry│ │ RBarra      │ │ RParquet     │ │ RInflux(只读)│
│只读包现 │ │ 读 Data/alt │ │ 读 result/   │ │ 查 lean_*    │
│有 Factor│ │ barra CSV   │ │ 新因子 parquet│ │ 度量         │
│Registry │ │ 不改 builder│ │ (7 新因子)   │ │              │
│不改原码 │ │             │ │              │ │              │
└─────────┘ └─────────────┘ └──────┬───────┘ └──────────────┘
                                     ▲
┌────────────────────────────────────┴───────────────────────┐
│ FactorWorker  (data-source/tushare/, 新 supervisor 程序)     │
│  · 60s 轮询: tushare 最新交易日 vs 各因子最新日              │
│  · 落后 → 调各 build_day(target_date, universe) 增量补齐      │
│  · 写 Results/factor-zoo/freshness.json                      │
│  · 写 InfluxDB lean_factor_freshness → Grafana 面板         │
│  · catalog 版本变化 → 重生成 FactorCatalog manifest          │
└──────────────────────────────────────────────────────────────┘
```

### 组件清单

| 组件 | 位置 | 新/接现有 | 职责 |
|---|---|---|---|
| `FactorStore` | `Common/Factors/Store/FactorStore.cs` | 新 | 统一读接口 + 适配器路由 |
| `RRegistryAdapter` | `Common/Factors/Store/` | 新 | 只读包 `FactorRegistry`, 不改其原码 |
| `RBarraAdapter` | `Common/Factors/Store/` | 新 | 只读 `Data/alternative/barra-*.csv`, 不改 builder |
| `RParquetAdapter` | `Common/Factors/Store/` | 新 | 读新因子 `result/factor-zoo/<date>/<ts_code>.parquet` |
| `RInfluxAdapter` | `Common/Factors/Store/` | 新 | 只读 InfluxDB `lean_*` 度量 |
| `FactorWorker` | `data-source/tushare/factor_worker.py` | 新 | supervisor 守护 + 增量补齐 + 新鲜度上报 |
| 7 新因子 builder | `data-source/tushare/factor_builders/` | 新 | 每因子一个 `build_day` 模块 |
| `FactorCatalog` 生成器 | `Scripts/factor_zoo/build_catalog.py` | 新 | 扫描所有因子最新日 + 元数据 → manifest |
| `manifest.factor-*` schema | `Scripts/auto_optimize/manifest_loader.py` | 新增字段 | 让优化器枚举/调权因子 |
| 重构 LLM 接入 | `Scripts/inspiration/hypothesize.py` | 新增段落 | 把 catalog 塞进 prompt |
| Grafana 面板 | `Launcher/config/dashboards/factor-zoo-freshness.json` | 新 | 因子最新日柱状图, 过期变红 |
| tushare 核查脚本 | `Scripts/factor_zoo/audit_tushare_coverage.py` | 新 | 对照 15000 档产出缺口清单 |

## 3. 数据流

### 3.1 factor_worker 新鲜度循环 (每日增量补齐)

```
[每 60s 唤醒]
    │
    ▼
读 trade_cal 最新交易日 T_cal  (tushare_data_v2/trade_cal)
读 tushare 已完成日期  T_raw  (incremental_scheduler_state.last_finished_target_date)
    │
    ├─ 若 T_raw < T_cal  → tushare 还没下完, 跳过本轮 (等下载 daemon)
    │
    ▼  T_raw == T_cal (原始数据已齐)
对动物园每个因子 f:
    读 f 最新日 T_f  (各因子存储: crowding parquet / Barra CSV / 新因子 parquet)
    若 T_f < T_cal  → 落后, 入待补队列 Q
    │
    ▼
按依赖去重 Q (多因子共享 daily_basic, 只读一次)
对每个待补交易日 t ∈ (T_f, T_cal]:
    调对应 builder.build_day(t, universe=CSI300+500)
       ├─ crowding_factor_builder.build_day     [现有, 接入]
       ├─ export_forward_factors.run             [现有, 接入]
       ├─ accruals/grossprof/assetgrowth/.../   [7 新因子, 新 build_day]
       └─ Barra: 调现有 barra_cne5_pipeline.py --factor-source-mode build [全量重算, 不改 builder]
    每个 builder 写: parquet(result/factor-zoo/t={t}/) + InfluxDB(lean_*)
    │
    ▼
全部补齐后:
    写 freshness.json  {factor_id: last_date, status, last_run}
    写 InfluxDB lean_factor_freshness (tags: factor_id; field: last_date, lag_days)
    若 catalog 版本号变化 → 重生成 FactorCatalog manifest
    │
    ▼
[sleep 60s, 下一轮]
```

**容错与幂等**:
- 状态文件 `Results/factor-zoo/factor_worker_state.json`: 每因子 `last_built_date`/`last_status`/`last_error`。崩溃重启断点续。
- 单因子 build_day 失败不阻断其他: try/except 隔离, 记 `failed`, 下轮重试 (最多 3 轮后标 `poisoned` 暂停)。
- 幂等: build_day 对同一交易日重跑覆盖 (parquet upsert keep=last, InfluxDB 同 timestamp 覆写)。
- 背压: 一轮待补 > N 个交易日 (如假期后积压) 分批补, 每批写一次状态。

### 3.2 FactorCatalog manifest 生成

**时机**: factor_worker 每轮补齐后或因子集合变化时。输出 `Results/factor-zoo/factor-catalog.yaml`。

```yaml
version: 2026-07-22T17:00:00Z
universe: [CSI300, CSI500]
factors:
  - id: crowding
    name: Trading Crowding Score
    category: Sentiment
    compute_mode: Precomputed
    storage: {kind: parquet, path: result/crowding-factor/<date>/<ts_code>.parquet, influx: lean_ashare_crowding_factor}
    tushare_deps: [daily_basic, moneyflow, margin_detail, hsgt_top10, cyq_perf]
    last_date: 2026-07-21
    status: fresh
    selection_hint: "拥挤度, 高=过热, 通常反向"
    parameters: {}
  - id: barra_beta
    name: Barra Beta
    category: Trend
    compute_mode: Precomputed
    storage: {kind: csv, path: Data/alternative/barra-cne5v2-factors/<mkt>/daily/<ticker>.csv}
    tushare_deps: [daily]
    last_date: 2026-07-21
    status: fresh
    parameters: {weight: {default: -0.05, range: [-0.2, 0.2]}}
  - id: accruals_sloan
    name: Accruals (Sloan)
    category: Quality
    compute_mode: Precomputed
    storage: {kind: parquet, path: result/factor-zoo/<date>/<ts_code>.parquet, influx: lean_factor_accruals_sloan}
    tushare_deps: [balancesheet, cashflow, stock_basic]
    last_date: 2026-07-21
    status: fresh
    selection_hint: "应计异象, 高应计=盈余质量差, 通常反向"
    parameters: {}
```
(每个因子一条; 完整 7 因子 + 现有 crowding/Barra/forward/IV/Chip/Value/Quality/Trend/Liquidity 全量在 §5 与现有 §0 盘点中)
```

**两类消费**:
- **重构 LLM** (`Scripts/inspiration/hypothesize.py`): prompt 插入 "可选用因子" 列表, 取 manifest 的 `id/name/category/selection_hint` (不含存储细节)。LLM 据此在重构假设里指定 `factor-include: [accruals_sloan, ivol_20d]`。
- **优化器** (`manifest_loader.py` + `bayesian_optimizer.py`): 读 `parameter_space` 新增 `factor-include` (枚举) / `factor-weight-<id>` (范围)。Optuna 像采样标量一样采样这些参数, 写回 strategy config。

### 3.3 策略消费

现有策略照旧 `AddData<TFactorData>` 读 CSV/parquet。新因子要被某策略用, 作者在 config 加 `AddData` 指向 `result/factor-zoo/` (或经 `RParquetAdapter` 暴露的统一路径)。**这步留给具体策略 spec, 本架构 spec 只保证因子值已就绪、可被发现**。

## 4. 错误处理 / 测试策略 / 约束对齐

### 4.1 错误处理

| 故障 | 处理 |
|---|---|
| tushare_data 未下完 (T_raw < T_cal) | factor_worker 跳过本轮, 等下载 daemon; 不空跑因子 |
| 单因子 build_day 抛错 | try/except 隔离, 记 `failed`+`last_error`, 其他因子继续; 下轮重试 (最多 3 轮后标 `poisoned` 暂停, 写 freshness.json 供人查) |
| 因子依赖表为空/历史缺 | 适配器 `IsAvailable(sym,date)` 返回 false; `FactorStore.Get` 返回 `Quality=Missing`; 不喂 NaN 进策略 |
| tushare 接口权限不足 (permission_denied) | 复用现有 `SkipIncrementalAPI` 机制, 标 `permission_denied`; freshness.json 标 `status=blocked` |
| InfluxDB 写失败 | 不阻断 parquet 写入 (parquet 是回测真源); InfluxDB 仅 dashboard, 失败仅记日志, 下轮重试 |
| factor_worker 崩溃 | supervisor `autorestart=true`; 状态文件断点续; 单轮积压分批 |
| catalog 生成失败 | 不阻断因子计算; 旧 manifest 继续可用; 下轮重生成 |

### 4.2 测试策略

**单元测试 (pytest, `Tests/Python/FactorZoo/`)**:
- 每个 `build_day`: fixture tushare parquet → 断言因子值范围、NaN 处理、ST/新股 edge case
- `FactorCatalog` 生成器: fixture 因子集 → 断言 manifest 结构、`status` 判定 (fresh/stale/blocked)
- 新鲜度循环: mock T_cal/T_raw/T_f → 断言 "落后才补、已齐跳过"

**集成测试**:
- `factor_worker` 端到端: mock T_f 落后 1 天 → 跑一轮 → 断言 parquet 多 1 天、freshness.json 更新、InfluxDB 有点
- 适配器只读性: 断言 `RBarraAdapter` 读不修改 `Data/alternative/barra-*.csv` (mtime/hash 不变)

**回归测试 (守 "不改现有特性")**:
- 跑现有 crowding/Barra/forward 既有测试套件 (`Tests/Factors/test_crowding_factor.py` 等) → 必须全绿
- 跑现有 Barra 策略回测一次 → 结果与接入前 bit-identical (证明适配器只读、零侵入)

**C# 测试 (NUnit, `Tests/Common/Factors/`)**:
- `FactorStore` 路由: 不同 factor_id → 正确适配器
- `FactorStore.Get` 返回 `FactorResult`, `Quality` 字段正确反映 Missing/Stale

### 4.3 约束对齐

| 约束 | 本设计如何守 |
|---|---|
| 不改现有特性 | 适配器只读包现有系统, 不改 `FactorRegistry`/Barra builder/现有策略原码; 新因子在独立命名空间 `Common/Factors/Store/` + `data-source/tushare/factor_builders/` |
| 新特性完全独立 | `factor_worker` 独立 supervisor 程序, 不碰 `tushare_worker`; catalog 独立 manifest 文件 |
| LEAN native only | 因子计算 Python, 消费/打分 C#——与现有 Barra 一致 |
| pipeline 必须常驻, 改后重启 | factor_worker 入 supervisor, 改后 `supervisorctl restart factor_worker` |
| dashboard 不改现有面板 | 新增独立 `factor-zoo-freshness.json` |
| 筹码用 cyq_perf 5 档 | 新因子涉筹码用 cyq_perf (与 crowding 一致) |
| 15000 积分 token | §6 逐接口核对档位, 超 15000 显式标注不可用 |

## 5. 首批 7 个新因子 (经对抗式验证)

**验证方法**: 每因子先设计, 再用独立 agent 对抗验证——读真实 parquet schema 核字段拼写、WebFetch tushare 文档核档位、读 `FactorRegistry.cs`+Barra builder 核重叠、查 PIT 安全。

**标准化口径 (统一)**: 横截面 `winsorize(1%/99%) → zscore`, 宇宙 = CSI300+500 (待 000905.SH 下载后, 见 §6)。财务类年度更新+月度横截面; 价量类每日/月度。PIT 锚定 `f_ann_date` (实际公告日) 而非 `end_date`, 与 `BarraCNE5DataLoader.load_point_in_time` 一致。

| # | factor_id | 类别 | 公式 | tushare 依赖 (档位) | 验证结论 |
|---|---|---|---|---|---|
| 1 | `accruals_sloan` | Quality | Sloan 1996 应计: `Accruals = (Δ非现金WC − Δ现金)/平均总资产`, 年度。非现金WC=Δ(流动资产−货币资金)−Δ(流动负债−短期借款−应付票据−一年内到期非流动负债); 再减 Δ折旧摊销 | balancesheet(2000)+cashflow(2000)+stock_basic(0) | ACCEPT after fix: 去 stock_basic 的 `list_status` (盘上无此列, 非必需), 对短期债务子项 `fillna(0)` 后再差分 (盘上 notes_payable 24/24 行 null) |
| 2 | `gross_profitability` | Quality | Novy-Marx 2013: `GP=(revenue−oper_cost)/total_assets`, 年度 | income(2000/5000 VIP)+balancesheet(2000/5000)+stock_basic(0) | ACCEPT after fix: stock_basic 是单 `data.parquet` 非年分区 (修正 on_disk_partition), 去 `list_status`, 删 "daily_basic.name 回退" (daily_basic 无 name 字段)。核心字段 revenue/oper_cost/total_assets 已确认在盘 |
| 3 | `asset_growth` | Investment | Cooper-Gulen-Schill 2008: `AG=(total_assets_t−total_assets_{t-1})/total_assets_{t-1}`, 年度, 排除银行 (comp_type∈{2,4,5}) | balancesheet(2000)+stock_basic(2000)+namechange(0) | ACCEPT: 无修复。银行排除 guard 已验证必要 (000001.SZ 盘上 comp_type='2') |
| 4 | `ivol_20d` | Volatility | 特异性波动: CSI300 市场模型回归残差 `r_i−(α+β·r_m)` 的 20d 标准差 ×√252, df=N−2 修正, N_eff≥40 兜底。与 Barra resvol (DASTD+CMRA+HSIGMA 复合) 和 hv_20d (总波动) 方法/口径不同 | daily(120)+adj_factor(2000)+index_daily(0) | ACCEPT after fix: **CSI500(000905.SH) index_weight 盘上缺失**。修复二选一: (a) 标准化宇宙改 CSI300-only; (b) 先下载 000905.SH index_weight (见 §6)。**本 spec 采 (b)**, 因宇宙已定 CSI300+500 |
| 5 | `max_ret_20d` | Trend | Bali-Cakici-Whitelaw 2011 MAX: trailing 20d 最大日收益率 `MAX=max(pct_chg/100)`, 取负做多头 | daily(120) | ACCEPT: 修文档一处 (除权除息段落, 不影响计算——tushare `pct_chg` 已含复权) |
| 6 | `short_term_reversal` | Reversal | Jegadeesh-Lehmann 1 月反转: `REV20=−(close_t/close_{t-20}−1)`, 用 adj_factor 复权。与 momentum_20d 区别: 窗口/符号/反转逻辑 | daily(120)+adj_factor(2000)+stock_basic(2000) | ACCEPT: 建议用 bak_basic (按交易日分区) 做 PIT 正确的历史 ST 过滤, 勿用 stock_basic 快照 |
| 7 | `roe_change` | Quality | ROE 同比变化: `ΔROE=ROE_t−ROE_{t-4Q}`, 质量动量。与 roe (水平) 和 net_margin (利润率) 口径不同 | fina_indicator(2000) | ACCEPT: 字段 roe/roe_yoy/ann_date/end_date 已确认在盘。同 ivol_20d, CSI500 成员数据需先下载 |

**备选因子 (若上述某因子实现受阻)**: `net_financing` (Bradshaw 外部融资 = (股权+债权发行)/平均资产, balancesheet/cashflow/share_float) — 已有设计, workflow 中验证待用。

## 6. 数据补齐清单 (诉求 1 核心产出)

### 6.1 关键缺口 (工作流审计确认 — 2026-07-22 实测)

**审计方法**: pyarrow ParquetFile 元数据 + 定向日期列读取 (非全扫), 对照 `tushare-downloader/status.py` 与 `incremental_state.json`; 档位对照 tushare 文档 doc_id=290 主权限表。基准日 = 最新 `trade_cal is_open=1 ≤ 20260722`。

| 缺口 | 影响 | 补齐动作 | 优先级 |
|---|---|---|---|
| `index_weight` 000905.SH (CSI500) 缺失 | ivol_20d/roe_change 横截面宇宙、"CSI300+500" 约定本身 | backfill 脚本增 000905.SH (+000852.SH 中证1000 备用) index_weight (2000 积分, 可下) | P0 (前置) |
| **`index_daily` 全 CSI 基准卡在 20260616, 25 天缺口** | ivol_20d 市场模型回归无近期基准 → 因子动物园阻塞 | 强制 backfill index_daily (000300.SH/000905.SH 等) | P0 (前置) |
| `margin_detail` 每标的 staleness 不均 (部分流动性名 2026-02) | crowding 筹码轴 margin_growth 子项 | 强制 backfill margin_detail 落后标的 | P0 |
| 4 基本面 (income/balancesheet/cashflow/fina_indicator) `ann_date` 拉取停在 20260515 | accruals/gross_profitability/asset_growth/roe_change 的 PIT 锚点滞后 | backfill ann_date 增量 (end_date 已到 Q1-2026, 仅公告日未拉) | P0 |
| `forecast`/`express`/`dividend`/`stk_holdertrade`/`pledge_stat` ~116-119 交易日 ann_date 缺口 | forward 因子族 (earnings_surprise/disclosure_timing/insider_trade/pledge_risk) 滞后 | backfill ann_date 增量 | P1 |
| `cyq_perf`/`cyq_chips` registry disabled | crowding 筹码轴; 记忆要求用 cyq_perf 5 档非 cyq_chips | cyq_perf 现走 17:00 cron 旁路 (fresh 0721); cyq_chips 滞后 0702。**迁 `tushare_cyq_worker` 到 supervisor (已有 `install_cyq_supervisor.sh`), 主用 cyq_perf** | P1 |
| 6 表轻度 STALE (3-4 交易日滞后): daily_basic/moneyflow/moneyflow_hsgt/hsgt_top10/hk_hold/limit_list_d | 因子输入略滞后 | incremental_scheduler 正在跑 (19:29 当日步完成), 隔夜自愈, 无需手动 | P3 (自愈) |
| `stock_basic` 缺 `list_status` 列 | accruals/gross_profitability 设计引用 | **采弃用** (非必需, ST 用 `stock_basic.name` 过滤; 新上市用 `list_date`) | P2 |
| `namechange` 仅 ~10000 行 (单页) | 历史 ST 重建不全 | 全量分页回补 namechange | P2 |

### 6.2 15000 积分档核对结论 (工作流确认)

- **15000 是 tushare 顶档** ("15000+ = special-data unlimited")。**没有任何按积分计费的接口需要 >15000 积分**。§5 的 7 因子所需接口均 ≤2000 积分, 全部可下。
- **不能下的不是积分问题, 是独立付费权限 (与积分无关)**: HK 股 (hk_daily ¥1000/yr 等)、US 股 (us_daily ¥2000/yr 等)、stk_auction_o/c (集合竞价 ¥500/yr)、stk_premarket (盘前股本 ¥500/yr)。这些 120 分试用仅 ~2 次调用, 不可用于历史回补。**本批 7 因子不依赖任何独立付费接口**。
- **超 15000 档的 disabled 接口**: stk_mins/ft_mins (分钟线, 需 10000+ 且独立权限), 不在本批因子依赖内。
- **registry 命名 bug**: registry 的 `index_member` 应为 `index_member_all` (2000 积分, doc_id=335), 下载器会失败直到改名 — 实现阶段修正。

### 6.3 完整审计产出 (实现阶段)

173 接口的逐表 NOT_DOWNLOADED/EMPTY/INCOMPLETE/STALE/DISABLED 清单由 `Scripts/factor_zoo/audit_tushare_coverage.py` 复跑产出 (本 spec 已含上述实测结论的子集)。

## 7. 落地顺序 (实现计划骨架, 细节交 writing-plans)

1. **tushare 覆盖审计 + 补齐** (含 enable cyq_perf, 下载 000905.SH index_weight) — §6
2. **FactorStore + 4 适配器** (C# 纯增量) + 单测 — §2
3. **FactorCatalog 生成器** + manifest schema — §3.2
4. **factor_worker** supervisor 程序 (先接入现有 crowding/forward build_day 验证循环) — §3.1
5. **7 新因子 build_day** + 单测 (逐个接入 worker) — §5
6. **Barra 接入** (factor_worker 调现有 `barra_cne5_pipeline.py --factor-source-mode build`, 不改 builder; 重算成本由 §8 Barra 性能项跟进)
7. **manifest.factor-include schema** + 优化器接入 — §3.2
8. **重构 LLM prompt 注入 catalog** (`inspiration/hypothesize.py`) — §3.2
9. **Grafana 新鲜度面板** — §2
10. **端到端回归** (现有套件全绿 + Barra 回测 bit-identical) — §4.2

## 8. 风险

- **适配层性能**: FactorStore 经 4 适配器路由可能增加查询延迟。缓解: 适配器内缓存当日值, 跨截面批量读。
- **Barra 全量重算成本**: factor_worker 调 `barra_cne5_pipeline.py --factor-source-mode build` 是全量重算 (builder 现只有 reuse/build 两模式, 不改它即只能全量)。缓解: (a) Barra 接入设为每日单跑一次而非每轮; (b) 若成本不可接受, 在**独立后续 spec** 给 builder 加 `--mode incremental` 开关 (旧路径不动, 增量与全量对拍 bit-identical 才启用) — **本 spec 不做增量模式**, 避免碰成熟 builder。
- **CSI500 下载分**: 000905.SH index_weight 是否在 15000 档内, 需实现阶段核 (tushare index_weight 通常 2000 积分起, 应可下)。
- **7 因子实盘有效性**: 本 spec 保证因子可算、新鲜、可选, 不保证 alpha。有效性由后续回测/IC 检验, 非本架构 spec 范围。
