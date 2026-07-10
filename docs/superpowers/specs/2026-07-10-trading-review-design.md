# 交易复盘(Trading Review)层设计规范

> **状态**:已批准(2026-07-10)。用户确认:SerializeRlState 扩展同意 / 求和目标=Trade.ProfitLoss gross 确认 / 整体 6 节设计认可。
>
> **方案**:Approach A — manifest 驱动的插件式 review 层。通用跨策略框架,gold2(Gold2BetaVolTargetStrategy)为首个具体实例。

## 0. 目标与约束

### 0.1 目标(用户确认)

复盘层的首要目标(4 选 3,多选):
1. **PnL 因子归因** — 把组合 PnL 拆成各因子层贡献(gold2: 趋势/波动率目标/极端风险/实际利率帽子)
2. **逐单 narrative + 信号对账** — 每笔 Trade 关联入场 Insight/因子状态/regime,做"模型预测 vs 实际兑现"对账
3. **回撤成因归因** — 最差回撤周期里逐仓位/逐信号拖累来源
4. (未选)执行质量 TCA — 作为可选 block 保留实现

### 0.2 硬约束

- **永不修改 LEAN C# 原生接口**(项目记忆 `never-modify-lean-native` / `feedback_lean-native-only`)。review 层全部在 Python,`Scripts/review/`。
- **通用跨策略框架**;gold2 是首个 adapter 实例,接口预留通用。
- **manifest 声明 layer schema + 策略暴露归因函数**(插件式 adapter)。
- 产物:review.json(结构化)+ HTML tearsheet + InfluxDB review_* measurements + Grafana 面板 + manifest `review:` 段 + 可选管线阶段。
- 运行:手动命令 + 管线可选叠加(默认不阻断 deploy_gate)。

### 0.3 与 LEAN native 的关系

review 层**只读** LEAN 背测原生产物,不改 C# 核心。唯一 C# 改动是 gold2 策略**自己的** `SerializeRlState` 方法 + OnData 调用点(`IRlStateExportable` 在 `Algorithm.CSharp/Common/`,是策略项目自己的 common,**不是** LEAN core 的 `Common/`)→ 不触发"永不改 LEAN native"规则。

---

## 1. 架构 + 数据流

### 1.1 目录结构

全部 greenfield Python,放 `Scripts/review/`(与 `Scripts/auto_optimize/` 同级,镜像其自包含模式):

```
Scripts/review/
  __init__.py                 # package marker
  run_review.py               # CLI 入口(orchestrator,镜像 run_e2e.py 形态)
  cli.py                      # argparse: --manifest --results --html --influx --write-manifest
  adapters/
    __init__.py
    base.py                   # StrategyReviewAdapter ABC
    gold2.py                  # Gold2ReviewAdapter
  schema/
    review_schema.json        # review.json 的 JSON Schema
    artifact_map.yaml         # artifact glob 路径默认值
  tearsheet/
    builder.py                # HTML 生成器
    templates/gold2.html.j2   # Jinja2 模板
  influx_export.py            # review_* InfluxDB 导出
  pipeline_overlay.py         # 可选管线阶段逻辑
```

### 1.2 Adapter 解析(bootstrap)

`run_review.py` 顶部 `sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))`(镜像 `evolution_scheduler.py:24-25` for auto_optimize),使 `importlib.import_module('adapters.gold2')` 在任何 cwd 下都解析。`Scripts/review/` 与 `Scripts/review/adapters/` 都需 `__init__.py`。

Adapter 解析:`importlib.import_module(manifest.raw['review']['adapter_module'])` → `getattr(module, manifest.raw['review']['adapter_class'])()`。

**Accessor 安全**:`StrategyManifest` 没有 `review` 属性 → 所有读取走 `manifest.raw.get('review', {})` 再 `.get(key, default)`,绝不 `manifest.review.xxx`。

### 1.3 端到端数据流

1. 背测把 LEAN 原生产物写到 `Results/<run>/`:
   - `{algo}.json`(charts 含 Strategy Equity + Drawdown、orders 含 orderSubmissionData、`totalPerformance.closedTrades/tradeStatistics/portfolioStatistics`)
   - `{algo}-order-events.json`(filled events: `fillPrice/fillQuantity/orderFeeAmount`)
   - `{algo}-summary.json`
   - 可选 `{algo}/alpha-results.json`
   - 可选 `state_trace*.jsonl`
2. 操作员运行 `python3 Scripts/review/run_review.py --manifest ... --results-dir ...`
3. manifest 经 `manifest_loader.load_manifest()` 加载,`raw` 逐字携带 `review:` 段(line 94 `raw=raw`)
4. adapter 解析 + 实例化
5. `adapter.load_artifacts()` glob 路径(默认值可经 `manifest.raw['review'].get('artifact_globs', defaults)` 覆盖),跑 4 个 review block,返回 `ReviewResult`
6. `schema/review_schema.json` 验证;`review.json` 写入 results 目录
7. 可选 `--html` → `tearsheet/builder.py` 渲染自包含 HTML(Jinja2 + inline SVG,无 plotly/CDN)
8. 可选 `--influx` → `influx_export.py` 写 4 个 measurement(复用 `Scripts/export_barra_cne5_unified.py` 的 `esc()` + `write_influx()`,env: `INFLUXDB_URL/ORG/BUCKET/TOKEN`)
9. `last_review`(ISO 时间戳 + review.json 路径)写到 sidecar `Results/<run>/review.last_review.json` — **不改源 manifest YAML**(保持 git diff 干净;sidecar 是 scheduler 的读取目标)。`--write-manifest` 时才用 ruamel.yaml 回写源 manifest。

### 1.4 四个 block → 源 artifact

| block | 源 artifact | 备注 |
|---|---|---|
| `layer_attribution` | `alpha-results.json`(sourceModel/direction/weight/scoreMagnitude/scoreDirection)+ `state_trace*.jsonl`(per-bar 权重) | alpha 缺失时残差分解 |
| `per_trade_narrative` | `{algo}.json` 的 `totalPerformance.closedTrades`(15 字段/trade)join `order-events.json` by orderIds | |
| `drawdown_attribution` | `{algo}.json` 的 `charts['Drawdown']['series']['Equity Drawdown']['values']` + `charts['Strategy Equity']` | **必须读完整 `.json`,非 `-summary.json`**(summary 缺 Drawdown chart) |
| `tca` | `tradeStatistics.totalFees` + per-trade `totalFees` + `order-events.fillPrice` join `{algo}.json orders[orderId].orderSubmissionData.{bidPrice,askPrice,lastPrice}` | 验证 997/997 orders 有 orderSubmissionData |

### 1.5 管线 overlay(可选,永不默认阻断 deploy_gate)

hook 点:`evolution_scheduler.fire_optimization()`(line 80),在状态文件写入(line 129)后调用,try/except 包裹 log+吞掉。(`_check_and_fire` 是 `main()` 内嵌套闭包 line 146,非外部可 hook。)

`pipeline_overlay.should_run()` 由 `manifest.raw.get('review',{}).get('review_schedule','manual')` 门控(weekly/on_backtest/manual);默认 `manual` → 不自动跑直到操作员加 `review:` 段。`deploy_gate` 保持 `manual`(config.yaml);review 纯粹附加观测。

### 1.6 零 C# 改动保证

review 层不碰任何 LEAN core 文件。唯一 C# 交互:读 natively 产出的 `alpha-results.json`(由 `Gold2TrendAlphaModel.cs` 产出)+ 可选地为 gold2 接 `SerializeRlState` 的 OnData 调用点(§3.3,策略自己代码非 LEAN core)。`manifest_lint.py` **不**验证 `review:` 段(只校 parameter_space/state_schema/rl_state_completeness);review 段有效性由 `schema/review_schema.json` 在运行时强制。

---

## 2. review.json Schema

`review.json` 是 adapter 的规范输出 — HTML tearsheet 和 InfluxDB exporter 都消费它(不需重跑背测,不需 render 时查 Influx)。5 个顶层 block + 3 个 tearsheet 辅助 block。

### 2.1 run_meta(必需)

- `strategy_name` = AlgorithmId = `Config.Get('algorithm-id', algorithm-type-name)` = result 文件名(非 `state.Name` 默认 "local")。与 `state_trace[0].strategy` 交叉校验,mismatch 时 warn,文件名值为准。
- `backtest_id`(文件名)
- `period_start` / `period_end`(`tradeStatistics.startDateTime`/`endDateTime`)
- `generated_at`、`adapter_version`、`schema_version`

### 2.2 layer_attribution(必需,object,key=层名 + `_unattributed`)

每个值 = `{pnl_abs, pnl_pct_of_total, n_trades, n_wins, contribution_to_total_return}`。

**不变式**:`sum(layer.pnl_abs) == decimal.Parse(totalPerformance.tradeStatistics.totalProfitLoss)`(closed-trade gross P&L;`totalProfitLoss` 经 `JsonRoundingConverter` 序列化成 STRING,需 `decimal.Parse`)。`Trade.TotalFees` 单列在 narrative 里,**不入任何层**。`n_trades` 求和 = `tradeStatistics.totalNumberOfTrades`。

### 2.3 per_trade_narrative(必需,array)

每个 closedTrade:
- `trade_id, symbol, entry_time, exit_time, entry_price, exit_price`
- `direction`(long|short,映射自 TradeDirection 0/1)
- `quantity`(Trade.Quantity,无符号)、`pnl`(=Trade.ProfitLoss gross)、`fees`(=Trade.TotalFees)
- `mae, mfe, end_trade_drawdown`
- `days_held`(由 `duration` 字符串 `"d.hh:mm:ss"` 派生)
- `layer_contributions`(`{layer_name: pnl_abs}`;无层 key 时 fallback `{_unattributed: pnl}`)
- 可选 enrichment(artifact 存在时):
  - `entry_signal`(按 `(symbol, entryTime)==(ticker, generatedTime)` 精确匹配;`magnitude` 是 STRING 需 `float()`;~0.1% 不匹配 → `entry_signal_source='absent:no alpha at entryTime'`)
  - `regime_at_entry`(state_trace 最近 `ts ≤ entryTime`;策略特定子集,gold2: `{trend_dir, realrate_cap, w_smooth, extreme_triggered}`)
  - `insight_realized_vs_predicted`(仅当 `scoreDirection/scoreMagnitude` 非零且 `scoreIsFinal=true`)

### 2.4 drawdown_attribution(必需,array,top-N episodes)

每个 episode:`peak_time, trough_time, recovery_time(nullable), depth_pct, top_contributing_layers [{layer, pnl_abs}], top_contributing_trades [trade_id], regime_during, regime_source`。

**源**:`{algo}.json` 的 `charts.Strategy Equity.series.Equity.values`(candlesticks)+ `charts.Drawdown.series.Equity Drawdown.values`。

### 2.5 tca(必需)

`{avg_slippage_bps, fill_quality_score, n_fills, source}`。

slippage = 对 filled events 求 `(fillPrice - mid)/mid * 10000` 均值,`mid=(bidPrice+askPrice)/2`(bid/ask 缺失时 `lastPrice`)。join 路径:`closedTrade.orderIds → order-events.json orderId → {algo}.json orders[orderId].orderSubmissionData`。SerializedOrderEvent 无 expected-price 字段,但 `orderSubmissionData` 在 997/997 orders 验证存在 → 背测 TCA 可填充;仅 trimmed artifacts 时 null+reason。

### 2.6 Tearsheet 辅助 block(--html 时必需)

- `kpis`:`total_return_pct, cagr_pct, sharpe, sortino, max_drawdown_pct, win_rate_pct`
- `equity_curve`:`[{t, strategy_equity_rebased_100}]`
- `benchmark_curve`:`[{t, benchmark_equity_rebased_100}]`(如 CSI 300 或 SHIBOR-1Y 基线)
- `monthly_pnl`:`{YYYY-MM: pct}`

均由 adapter 已读的同批 LEAN artifact 派生。

### 2.7 层 key 派生(closedTrades 无 per-trade 层标签)

1. alpha sourceModel join — `closedTrade (symbols[0].value, entryTime)` → `alpha-results[] (ticker, generatedTime)` 精确匹配 → `sourceModel` 成为层 key(验证 995/996 匹配)
2. fallback 到 `_unattributed`(无 alpha-results 或不匹配)

`order-events.Message` **不**作层标签(997/997 都是 LEAN fill-data 警告)。

---

## 3. Adapter 协议 + gold2 归因数学

### 3.1 StrategyReviewAdapter ABC(`adapters/base.py`)

```python
class StrategyReviewAdapter(ABC):
    LAYERS: list[str]   # 有序,如 ["trend","vol_target","extreme_risk","realrate_cap"]

    @abstractmethod
    def layer_attribution(self, trade: TradeRecord, context: TradeContext) -> dict[str, Decimal]:
        """返回 LAYERS 每个名字一个 Decimal。MUST sum to trade.profit_loss(LEAN gross)。"""

    @abstractmethod
    def trade_narrative(self, trade, context) -> dict: ...

    @property
    @abstractmethod
    def sum_to_property(self) -> str: ...   # "profit_loss" — LEAN Trade.ProfitLoss(gross, pre-fees)
```

**TradeRecord**:`symbol, entry_time, entry_price, exit_time, exit_price, quantity(=Trade.Quantity,无符号), side, profit_loss(=Trade.ProfitLoss gross), fees(=Trade.TotalFees), tpv_entry(冻结), realized_pnl(派生 = profit_loss - fees)`。

`profit_loss` 由 LEAN 在 `TradeBuilder.cs:328` 算成 `(ExitPrice-EntryPrice)*Quantity*sign*conversionRate*multiplier` — adapter **绝不**重算。

**求和目标(用户确认)**:N 层求和 = `Trade.ProfitLoss`(gross),**非** net `realized_pnl`。LEAN 的 `Trade` 把 `ProfitLoss`(gross)与 `TotalFees`(恒正)分开(`Trade.cs:99,104`)。`sum_to_property = "profit_loss"`;`fees` 挂在 TradeRecord,narrative 里作调节行;`realized_pnl` 派生非求和目标。

### 3.2 gold2 4 层分解

位置管线已验证:`Gold2VolTargetPortfolioModel.cs:35-39` + `Gold2ExtremeRiskModel.cs:32-42` + `Gold2RealRateCapModel.cs:33-43` + `CompositeRiskManagementModel.cs:89-101`(Composite 先 Extreme 后 RealRate,各保留较新 capped target,两 cap 经 `min` 叠加)。

**3 个关键修正**:
- **(C1 量纲)** 每层乘 `scale = TPV_entry / entry_price`(入场冻结)使 `Q_layer = w_layer * scale` 是 quantity 不是 weight。否则求和落在 `weight×price` ≠ 货币 PnL。
- **(C2 dead-zone)** 用 `w_vol = w_after_vol`(L3 实际 post-deadzone 输出到 state_trace 的权重),非理想化 `w_smooth*dirCoef`。`ApplyDeadZone`(threshold 0.05)在 hold bar 持有 `lastActual`,故 trend/vol 切分在 rebalance bar 精确、hold bar 把 persistence 归入 `vol_target`(近似)。
- **(C3 P 时机)** realized weight 从第一个 `ts > entry_time` 的 trace 行读(post-fill,`≤ exit` 无 lookahead),因 `ts == entry_time` 行是 pre-fill 持仓。

定义(long-only;`Δp = exit_price − entry_price`,带符号;LEAN ProfitLoss 对 Long 已乘 +1):

```
scale  = TPV_e / p_e
w_trend= dir_coef;  w_vol = w_after_vol
w_ext  = extreme_triggered ? min(w_vol, extreme_cap) : w_vol
w_real = min(w_ext, realrate_cap)
Q_layer= w_layer * scale;   δQ_lot = Q - Q_real   # 整手余量 + 执行残差(≤0)

C_trend        = Q_trend * Δp
C_vol_target   = (Q_vol - Q_trend) * Δp
C_extreme_risk = (Q_ext - Q_vol)   * Δp
C_realrate     = (Q_real - Q_ext)  * Δp + δQ_lot * Δp
```

**求和不变式(望远镜,量纲正确)**:

```
C_trend + C_vol_target + C_extreme_risk + C_realrate
  = Q_real*Δp + δQ_lot*Δp
  = (Q_real + δQ_lot)*Δp
  = Q*Δp
  = Trade.ProfitLoss   (gross)
```

每个中间 `Q_layer` 与相邻项相消;`Q_flat=0`(空仓基线零收益);`δQ_lot` 强制归入 `C_realrate` 使 4 个命名层精确求和到 gross `Trade.ProfitLoss`。fees 单列。**无 double-count**:相邻 `Q_layer` 间每个 `Δp` 切片恰好计数一次。

`extreme_cap_was_protective = (Δp<0 AND extreme_triggered)` 仅作 narrative 粗粒度标注,**不**量化避免损失(反事实会 double-count 破坏不变式)。

### 3.3 状态源决策(用户已同意方案 a)

gold2 的 `SerializeRlState`(`Gold2BetaVolTargetStrategy.cs:136`)**当前从未被调用** — grep 确认零调用点(对比 `OptionVolArb5LayerStrategy.cs:256,270-289` 有完整 writer)。所以 gold2 今天**没有 state_trace.jsonl**,入场栏 `TradeContext` 为空。

**采用方案 (a):扩展 `SerializeRlState` + 接 OnData 调用点**,而非 (b) Python 重算。

**理由**:`IRlStateExportable` 在 `Algorithm.CSharp/Common/`(策略项目自己的 common,**不是** LEAN core 的 `Common/`)→ 不触发"永不改 LEAN native"规则。Python 重算 EWMA(λ=0.94, 60 日 warmup, `Time < time` 无 lookahead 守卫)+ dead-zone 状态机 + VIX/RVol P95 窗口会复制 ~150 行有状态 C#,warmup seeding / 缺日填充 / 整手时机会静默漂移 — 归因层全部价值是"匹配策略实际做了什么",不可接受。

**需新增 4 字段**(manifest `dim_hint` 9→13):
- `dir_coef` — 需 `Gold2TrendAlphaModel.LastDirCoef` 在 `Update()` 中存储
- `w_after_vol` — 提升 `Gold2VolTargetPortfolioModel._lastActualWeight` 为 public getter
- `extreme_cap` — 运行时 `GetDecimalParameter("extreme-vol-cap", 0.3m)` 值(**不**从 manifest.parameter_space 恢复,可能被 tune)
- `trend_disabled` — bool;manifest parameter_space 需补 `trend-disable`(当前缺失)

**调用点**:`OnData` 在 framework models 跑完后加 `WriteRlStateTraceIfNeeded(slice.Time)`(此时 `LastDirCoef`/`LastActualWeight`/`extreme_triggered` 是 current-bar;因子仍消费 `≤ t−1` 数据 — 已验证)。

### 3.4 No-lookahead

入场栏信号行(`ts == entry_time`)反映用 `≤ t−1` 数据算的信号(策略据此行动);realized weight 从第一个 `ts > entry_time` 行读(post-fill,`≤ exit`)。`Δp` 是被解释的量,绝非层权重输入。

### 3.5 降级路径(state_trace 接好前)

gold2 fallback 到残差分解:`trend` = Alpha 归因 PnL(bars where `Gold2Trend` direction = Up vs Flat);其余三层报 `unattributed_residual` + `attribution_method: "residual"` + WARN。完整望远镜数学在 SerializeRlState callsite 接好后解锁。

---

## 4. HTML Tearsheet + InfluxDB + Grafana

### 4.1 HTML tearsheet(`tearsheet/builder.py` + `templates/gold2.html.j2`)

单文件自包含 `.html`,从 `review.json` ALONE 生成,写到 `Results/<run>/review.html`。无外部 JS/CSS、无 CDN、无 plotly — `file://` 离线/air-gap 可开。Jinja2(已装,3.1.6)+ 手写 inline SVG(~40 行/模板)。唯一 JS 是 ~80 行 inline vanilla JS 做逐单表排序 + regime 过滤下拉。

**9 个面板**:
1. header block(`run_meta`)
2. 6 卡 KPI 行(`kpis`,阈值着色)
3. 累计收益 vs benchmark SVG(`equity_curve` + `benchmark_curve`,rebase 到 100)
4. 回撤区 SVG(`drawdown`,红填 trough→zero,trough 标注 `top_layer`)
5. 月度收益热力图 SVG(`monthly_pnl`,RdYlGn diverging)
6. **分层归因堆叠条 SVG**(`layer_attribution[].pnl_abs`,核心面板)
7. 逐单排序表(`per_trade_narrative` + `layer_contributions` 投影成扁平 `<layer>_contrib` 列,按 `layer_names`)
8. 回撤归因表(`drawdown_attribution`)
9. TCA 块(条件,仅 `tca` 存在)

`--self-check` 断言 SVG 良构 AND `sum(layer_attribution[].pnl_abs) == decimal.Parse(run_meta.total_closed_trade_pnl)`(§2.2 不变式,绝对值)1e-6 内 — 上线前 fail-fast 强制加法完备性。注意:求和目标是 closed-trade gross P&L(货币,与 `totalProfitLoss` 同单位),**非** `kpis.total_return_pct`(百分比,不同单位)。

### 4.2 InfluxDB `review_*` measurements(`influx_export.py`)

一趟写 4 个 measurement,复用 `Scripts/export_barra_cne5_unified.py` 的 `write_influx()` + `esc()`(tag 转义),`Scripts/export_backtest_results_to_influx.py` 的 `escape_string_field`(string field 转义 `\` 和 `"` — `esc()` 只处理 tag 字符,对引号 field 不安全)。

**tag key 统一 `algorithm_id`**(非 `strategy`)— 与 `lean_chart`/`lean_portfolio`/`lean_metric`/`barra_cne5v2_*` 对齐,支持跨 measurement join/overlay(面板 5 overlay `lean_chart Equity` 与 regime span)。

| measurement | tags | fields |
|---|---|---|
| `review_layer_attribution` | `algorithm_id, layer, mode, run_id` | `pnl_abs, pnl_pct_of_total, n_trades, n_wins, contribution_to_total_return` |
| `review_trade` | `algorithm_id, symbol, regime_at_entry, direction, run_id, mode` | `pnl, mae, mfe, days_held` + 每层 `<layer>_contrib` |
| `review_drawdown` | `algorithm_id, run_id, mode, regime_at_trough` | `depth_pct, top_layer, duration_days` |
| `review_tca` | `algorithm_id, run_id, mode` | `avg_slippage_bps, fill_quality_score, n_fills` |

时间戳在事件时刻(exit/trough/backtest-end),`quant` bucket,env 一致。

### 4.3 Grafana dashboard(`monitoring/grafana/dashboards/lean/strategy-review.json`)

datasource `uid: lean-influxdb`,schemaVersion 39,`uid: strategy-review`,tags `["lean","review"]`。模板变量 `algorithm_id`(`SHOW TAG VALUES FROM "review_layer_attribution" WITH KEY = "algorithm_id"`)、`run_id`、`mode`(默认 `backtesting`)。

**6 个面板**:
1. Review Status stat(两 target:`review_drawdown` count + `review_layer_attribution` last pnl,PASS/WARN/FAIL 映射)
2. Layer Attribution Stacked Bar 跨 backtests(`sum(pnl_abs) GROUP BY layer,run_id`,x=run_id,stack=layer)
3. Per-Trade Scatter pnl vs mae 按 regime 着色(`SELECT pnl,mae FROM review_trade WHERE ... GROUP BY "regime_at_entry"` — GROUP BY 必需使每 regime 成独立色系)
4. Drawdown Attribution 表(`depth_pct,top_layer,duration_days` ORDER BY time DESC)
5. Regime-Colored Cumulative Return timeseries(overlay `lean_chart Equity` 与 `review_trade.regime_at_entry` 标注 span — 共享 `algorithm_id`)
6. Layer PnL % bargauge(`last(pnl_pct_of_total) GROUP BY layer`)

**Regression test**(`Scripts/test_strategy_review_dashboard_regression.py`,遵循 `test_barra_dashboard_regression.py` 模式):`test_dashboard_exists`、`test_original_6_panels_unchanged`(冻结 `(title, gridPos, type)` 到 `Scripts/_strategy_review_baseline_6.json`)、`test_algorithm_id_variable_preserved`、`test_no_stale_plugins`(拒绝 `{stat,barchart,scatter,table,timeseries,bargauge,row,gauge}` 外的 plugin type)。满足"永不改现有 dashboard" — strategy-review 是全新仪表盘,6 panel 冻结,独立于 barra-cne5 baseline 守护。

---

## 5. 管线集成 + manifest `review:` 段 + CLI

### 5.0 数据前提

adapter 优雅降级:
- order-events + `{algo}.json` equity/orders = **必需**(缺失 exit 3;LEAN 成功背测必产)
- `alpha-results.json` = **可选**(gold2 存在,4049 insights — 但全 `sourceModel='Gold2Trend'`;LEAN 只对 Alpha sourceModels 归因 PnL,故 `vol_target`/`extreme_risk`/`realrate_cap`(Portfolio/Risk models)无 insight、无原生 per-layer 归因)
- `state_trace.jsonl` = **可选** — gold2 当前不写(`SerializeRlState` 从未调用,§3.3);adapter 设 `artifacts.state_trace=None` 并在 review.json 标记,**不** exit 3

per-layer 归因方法:state_trace 缺失时用残差分解 — `trend` = Alpha 归因 PnL,其余三层报 `unattributed_residual` + `attribution_method: "residual"` + WARN;完整望远镜数学(§3)在 SerializeRlState callsite 接好后解锁。

### 5.1 manifest `review:` 段

新顶层 key,与 `cpcv:`/`walk_forward:`/`ppo_training:` 平行;仅 `run_review.py` + e2e gate 消费,**raw-passthrough,不加 `ReviewConfig` dataclass**(匹配现有 precedent,守"新功能完全独立"规则)。

gold2 literal YAML:

```yaml
review:
  adapter_module: adapters.gold2          # 经 Scripts/review/ sys.path bootstrap 解析
  adapter_class: Gold2ReviewAdapter
  layer_names: [trend, vol_target, extreme_risk, realrate_cap]   # 匹配 ABC LAYERS + schema layer key + Influx <layer>_contrib field
  review_schedule: on_backtest            # manual | on_backtest | weekly
  block_deploy: false                     # 默认 false;仅 fail+block_deploy=true 阻断
  last_review: null                       # 回写(ISO-8601 UTC)
  review_status: null                     # pass | warn | fail | null
  review_artifact_path: null              # repo-root/Results/-相对,如 gold2-betavol/review/review.json
  thresholds:
    max_layer_attribution_gap: 0.15
    min_narrative_trades: 20
    max_consecutive_dd_days: 15
```

`block_deploy: false`(默认)→ review 即使 `review_status: fail` 也**不**阻断 `deploy_gate`(deploy_gate 保持 `manual`)。仅操作员显式设 `block_deploy: true` 且 `review_status: fail` 才阻断。

### 5.2 run_review.py CLI

`Scripts/review/run_review.py`:顶部 `sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))` 使 `importlib.import_module('adapters.gold2')` 不依赖 cwd。

**flags**:`--manifest`、`--results`(可选;fallback 到 `lean_config` 的 `results-destination-folder` 去掉 `../../../` 前缀 → repo-root-相对)、`--html`、`--influx`、`--write-manifest`。

**流程**:load manifest → `manifest.raw["review"]` → resolve results dir(输出路径独立算 `repo_root/Results/<review_artifact_path>`,否则 `repo_root/Results/<results_basename>/review/review.json` — 不与 results_dir join 避免双重路径)→ import adapter → `load_artifacts`(hard-required 缺失 → exit 3;state_trace 缺失 → 降级)→ `validate_schema` → `layer_attribution`(gap 对 `thresholds.max_layer_attribution_gap` 检查)→ `trade_narrative`(from `closedTrades`)→ `drawdown_attribution`(from `charts['Drawdown']`)→ `tca`(orderSubmissionData join)→ 聚合 `review_status` → 写 `review.json` → 可选 `--html` / `--influx` → 可选 `--write-manifest`(ruamel.yaml v0.18.16 `preserve_comments=True` 回写 `last_review/review_status/review_artifact_path`)。

**exit codes**:
- 0 = pass
- 2 = warn(非阻断)
- 3 = fail / missing-hard-required / schema-error
- 1 = CLI usage error

(不写 state_trace 的策略,缺 state_trace **不**是 exit 3。)

### 5.3 e2e 插入

`run_e2e.py`:在 `baseline = run_baseline_test(...)` 后、return 前插 review step,在 `e2e()` 内。gated by `review.review_schedule`;默认非阻断。

- `review_schedule == manual` 或 `review:` 缺失 → 跳过
- `warn` → 永不阻断
- `fail` + `block_deploy: false` → log,继续
- `fail` + `block_deploy: true` → e2e 早返 `review.blocked_deploy: True`(唯一阻断路径)

`evolution_scheduler.fire_optimization()`(line 80)是 `weekly` cadence 的 module-level hook — `_check_and_fire`(line 146 嵌套闭包)自调 `load_manifest()` 读 `.raw.get('review',{}).get('review_schedule')` 并在 try/except 中非阻断调 `run_review.py --write-manifest`。

### 5.4 manifest_loader 集成

raw-passthrough,**不加 `ReviewConfig` dataclass**(匹配 `cpcv`/`walk_forward`/`ppo_training` precedent,守"新功能完全独立"规则)。`manifest_lint.py` 的 `LintResult`(当前仅 `ok: bool, errors: list`)加 `warnings: list` 字段;review-没跑的检查进 `warnings`(**不**进 `errors`),故 `ok = len(errors)==0` 仍 True — advisory WARN 语义,不 gate-fail。

---

## 6. 测试 + 已知局限

### 6.1 单测

- **Adapter ABC 合规**:`MockAdapter(StrategyReviewAdapter)`(`LAYERS=['a','b']`,返回 `{a: Decimal('1'), b: Decimal('4')}` 对 `trade.profit_loss=5`);断言 `sum(values)==profit_loss` 且 `set(keys)==set(LAYERS)`。`BadAdapter` 返回 `a:Decimal('6')`(sum≠profit_loss)必须抛错。gold2 adapter:构造已知 `dir_coef/w_after_vol/extreme_cap/realrate_cap/tpv_entry/entry_price` 的 `TradeRecord`,断言 4 个 `C_*` 精确等于望远镜求和(参数化:extreme 触发/未触发、trend disabled、hold-bar deadzone)。
- **gold2 attribution sum-to-total**:property test — 随机 `TradeRecord` + `TradeContext`(sane 范围)→ `abs(sum(adapter.layer_attribution(t,c).values()) - t.profit_loss) < 1e-9`(Decimal)。并断言每个 `C_*` 有限,整手残差 `δQ_lot` 落在 `C_realrate`。
- **review.json schema 验证**:`jsonschema.validate(doc, review_schema.json)` 对 (a) 手构良构 doc、(b) 逐个缺必需字段的 doc(必须 fail)、(c) `layer_attribution` 求和 ≠ `totalProfitLoss` 的 doc(adapter 侧,非 schema 强制 — schema 只查结构;求和不变式是 adapter 单测)。覆盖 string `totalProfitLoss` 的 `decimal.Parse`。
- **No-lookahead spot checks**:用合成 `state_trace.jsonl`(`ts==entry` 行 `dir_coef` 反映 t−1 数据,`ts>entry` 行带 post-fill 权重)→ 断言 adapter 从 `ts>entry` 行读 `P`,绝不读 `ts==entry` 行。断言 `entry_signal` 按 `(symbol, entryTime)==(ticker, generatedTime)` 精确匹配,~0.1% 不匹配落 `entry_signal_source='absent...'`。

### 6.2 已知局限

1. **TCA 需 orderSubmissionData** — `avg_slippage_bps` 在 `<algo>.json orders.*.orderSubmissionData` 缺失(trimmed artifacts)时 null+reason。market-order fill model 下 `fillPrice` 跟同 bar close,slippage-vs-mid 可能小但**非结构性零**(退化 `fillPrice-vs-entryPrice` 代理明确**不**用)。
2. **归因质量受限于策略序列化状态** — gold2 当前无 `state_trace.jsonl`(`SerializeRlState` 从未调用,§3.3),完整望远镜数学不可用,adapter fallback 残差分解(`trend`=Alpha 归因;其余=`unattributed_residual` + `attribution_method:"residual"` + WARN)。完整 per-layer 归因需接 OnData callsite + 扩 `SerializeRlState`(dir_coef/w_after_vol/extreme_cap/trend_disabled)— 策略侧 C# 改动(允许;非 LEAN core)。
3. **gold2 是唯一 first-party adapter** — 其他策略 fallback 到通用残差/sourceModel 路径直到写策略特定 adapter。
4. **dead-zone hold bar 近似** — persistence 归入 vol_target(rebalance bar 精确)。

---

## 7. 文件清单(实现时创建/修改)

### 7.1 新建(Python review 层)

- `Scripts/review/__init__.py`
- `Scripts/review/run_review.py`
- `Scripts/review/cli.py`
- `Scripts/review/adapters/__init__.py`
- `Scripts/review/adapters/base.py`(ABC + TradeRecord + TradeContext)
- `Scripts/review/adapters/gold2.py`(Gold2ReviewAdapter,4 层望远镜)
- `Scripts/review/schema/review_schema.json`
- `Scripts/review/schema/artifact_map.yaml`
- `Scripts/review/tearsheet/builder.py`
- `Scripts/review/tearsheet/templates/gold2.html.j2`
- `Scripts/review/influx_export.py`
- `Scripts/review/pipeline_overlay.py`
- `Scripts/test_strategy_review_dashboard_regression.py`
- `Tests/test_review_*.py`(adapter ABC / gold2 求和 / schema / no-lookahead)

### 7.2 新建(Grafana)

- `monitoring/grafana/dashboards/lean/strategy-review.json`
- `Scripts/_strategy_review_baseline_6.json`(regression baseline)

### 7.3 修改(gold2 策略侧 C# — 非 LEAN core)

- `Algorithm.CSharp/Gold2BetaVolTargetStrategy.cs` — `SerializeRlState` +4 字段 + `OnData` 加 `WriteRlStateTraceIfNeeded` 调用点
- `Algorithm.CSharp/Models/Gold2/Gold2TrendAlphaModel.cs` — 存 `LastDirCoef`
- `Algorithm.CSharp/Models/Gold2/Gold2VolTargetPortfolioModel.cs` — `_lastActualWeight` 提升 public getter
- `Scripts/auto_optimize/strategies/gold2_beta_vol_target/manifest.yaml` — 加 `review:` 段 + parameter_space 补 `trend-disable` + state_schema `dim_hint` 9→13

### 7.4 修改(管线侧 Python)

- `Scripts/auto_optimize/run_e2e.py` — baseline 后插 review step(gated by `review_schedule`)
- `Scripts/auto_optimize/manifest_lint.py` — `LintResult` 加 `warnings: list`

### 7.5 不改

- LEAN core `Common/`、`Engine/`、`Report/` 任何文件
- 现有 14 个 Grafana dashboard
- 现有 manifest(gold2_beta_vol_target 除外)

---

## 8. 与现有复盘类设施的关系

review 层**复用**而非重造:

| 现有设施 | review 层如何用 |
|---|---|
| `Engine/Results/BaseResultsHandler.StoreOrderEvents` | 读 `*-order-events.json` |
| `Common/Statistics/Trade.cs`(MAE/MFE/ProfitLoss/TotalFees/OrderIds) | per_trade_narrative 直接消费 |
| `Report/PortfolioLooper.FromOrders` | 不直接用(后置重建,review 走 state_trace 路径) |
| `Scripts/export_barra_cne5_unified.py`(esc/write_influx) | influx_export.py 复用 |
| `Scripts/export_backtest_results_to_influx.py`(escape_string_field) | influx_export.py 复用 |
| `Scripts/live_paper_console.py:314 build_trade_executions_preview` | trade-row 归一化参考 |
| `Scripts/gold2_compare_curves.py` | 背测对比模式参考(不直接用) |
| `Scripts/test_barra_dashboard_regression.py` | Grafana regression test 模式参考 |

review 层**填补**的空白:PnL 因子归因 / 逐单 narrative + 信号对账 / 回撤成因归因 / 统一 review.json 结构化产物 + manifest `review:` 段 + 管线阶段 / HTML tearsheet(gold2 当前只有 comparison.csv 一张表)/ InfluxDB+Grafana per-trade review 面板。

---

## 9. 开放问题(实现前需用户决策)

无。所有关键决策已在本次 brainstorming 中确认:
- SerializeRlState 扩展:同意(§3.3 方案 a)
- 求和目标 = Trade.ProfitLoss gross(fees 单列):确认(§3.1)
- 整体 6 节设计:认可
