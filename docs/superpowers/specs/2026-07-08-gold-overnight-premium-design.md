# 黄金 ETF (518880) 隔夜溢价 T+0 日内策略 — 设计规范

**日期**: 2026-07-08
**状态**: 已批准（待写实现计划）
**作者**: brainstorming 流程产出，Block 1 + Block 2 逐节确认后合并

---

## 1. 概述

### 1.1 目标

把 `docs/gold-quantwe.md` 的黄金 ETF 粗略量化思路与 `docs/gold-geyeyijia.md` 的隔夜溢价因子信号构造公式，落成一个**独立、有效（非仅可用）**的 C# T+0 日内算法。对标的是 518880（华安黄金 ETF，跟踪国内金价），利用 A 股收盘到次日开盘之间国际金价已消化的变动，转化为开盘后的日内交易信号。

### 1.2 与现有体系的关系

- **镜像 `AShareOvernightAnomalyAlgorithm` + `export_ashare_etf_t0_feature_data.py` 模式**：Python 导出器离线算信号写 parquet，C# 算法 + 薄 IFactor 消费，风控在 LEAN 原生 `IRiskManagementModel` 强制。
- **`docs/gold-*.yaml`（rich manifest schema）降级为参考文档**，不做可执行 contract。可执行 manifest 落在 `Scripts/auto_optimize/strategies/gold_overnight_premium/manifest.yaml`，使用项目现有**简化 schema**（`parameter_space` / `state_schema` / `reward_config` / `universe`），与其余 4 个 strategy manifest 一致，避免 schema 长期漂移。
- **纯加法、零侵入**：不改现有 IFactor / 现有策略 / 现有风控。新增独立因子、独立风控、独立算法。

### 1.3 关键约束（用户三次 design-check 决策的累积）

| # | 约束 | 来源 |
|---|---|---|
| 1 | 国际金价主信号源用 **SHFE AU 期货**（人民币计价，免 FX 调整），不用 FXCM 日线代理，不用 yfinance | `gold-check-design2.md` |
| 2 | AU 数据 **一次性全量回填**（2008→至今），不做实时补取，保回测可复现性 | `gold-check-design3.md` |
| 3 | `fut_mapping` 换月映射表时间范围必须与 `fut_daily` 价格数据**对齐**，防换月跳空被误判为隔夜异常 | `gold-check-design3.md` §point 2 |
| 4 | 数据新鲜度守卫：AU 时间戳滞后 > 2h → `DATA_STALE` 跳过当天，不用陈旧价格强算 Z | `gold-check-design1.md` |
| 5 | 实际利率 regime 缺 DFII10 时输出显式 **`UNAVAILABLE`** 状态，与 `STABLE` 区分（行为相同、状态不同），为将来 FRED 接入留可解释性 | `gold-check-design1.md` |
| 6 | 项目当前 **USD:RMB = 1:1 非真实汇率**——这是采用人民币计价 AU 期货顺带规避的另一个理由 | 用户初始说明 |

### 1.4 已确认的设计决策

| # | 决策点 | 选择 |
|---|---|---|
| 1 | 目标产物形态 | 独立 C# T+0 算法 + 简化 manifest（方案1路径） |
| 2 | 架构 | 混合：Python 导出器 + 薄 C# IFactor + 薄 C# 算法（方案 C） |
| 3 | 国际金价数据源 | SHFE AU 期货（AU.SHF 连续 + AUL.SHF 换月映射），FXCM XAUUSD 降为交叉校验 |
| 4 | AU 数据获取 | 一次性全量回填到本地 parquet，运行时不打 API |
| 5 | 状态字段 | 双正交 `skip_reason` × `regime`，不共享枚举 |
| 6 | Gate 2 角色 | IC 有效性 kill switch：OOS IC 不达标 → `EFFECTIVENESS_FAIL` 排除灰度发布 + ft_mins 升级路径 |
| 7 | 多空 | long-only 基线（518880 不可融券），空头侧 deferred |

---

## 2. 架构与数据流

### 2.1 单向数据流，运行时零 API 调用

```
tushare pro（回填，一次性）
   └─► parquet: fut_daily AU.SHF（2008+全量）+ fut_mapping AUL.SHF（2008+全量）
                                   │
   Python 导出器（离线，确定性） ◄──┘
     读 AU.SHF + 518880.SH(fund_daily) + XAUUSD.FXCM(fx_daily, 交叉校验)
     算: R_au_overnight, Gap_expected, Gap_actual, Signal, Z_signal,
         regime(含 UNAVAILABLE), skip_reason, freshness_flag, cross_check_alert
     写: Data/alternative/gold-overnight-premium/signals.parquet（每 trade_date 一行）
                                   │
   C# 算法（LEAN 回测）         ◄──┘
     AddEquity("518880") + AddData<GoldOvernightSignal>()（custom BaseData, daily）
     OnData: 把信号 inject 进 OvernightPremiumFactor + RealRateRegimeFactor
     Schedule: 09:30 评估进场 | 14:45 强制平仓
```

回测只读本地 parquet，绝不在运行时打 tushare / FRED——这是 CI gate 确定性的前提（`gold-check-design3.md`）。

### 2.2 组件清单（每个单一职责、单一接口、可独立测试）

| 组件 | 语言 | 职责 | 镜像 |
|---|---|---|---|
| `export_gold_overnight_premium_signals.py` | Python | 算 Z_signal + regime + skip_reason + 交叉校验，写 parquet | `export_ashare_etf_t0_feature_data.py` |
| `GoldOvernightSignal`（custom BaseData） | C# | 每 trade_date 从 parquet/CSV 载入一行信号 | `CustomDataRegressionAlgorithm` 模式 |
| `OvernightPremiumFactor : IFactor` | C# | Precomputed factor，`InjectValue`，返回 Z_signal | `AuctionGapFactor` |
| `RealRateRegimeFactor : IFactor` | C# | Precomputed factor，返回 regime enum（含 `UNAVAILABLE`） | 新增 enum，`gold-check-design1.md` |
| `NoOvernightPositionRiskModel : IRiskManagementModel` | C# | **硬** 14:45 强制平仓，不靠策略自觉 | `IRiskManagementModel` |
| `GoldOvernightPremiumAlgorithm : QCAlgorithm, IOptimizableStrategy, IRlStateExportable` | C# | 进出场逻辑、ATR 止损、regime 上限、RL 状态导出 | `AShareOvernightAnomalyAlgorithm` |
| `manifest.yaml` | YAML | 简化 schema manifest，接入 auto_optimize | `overnight_anomaly/manifest.yaml` |
| `config-gold-overnight-premium-backtest.json` | JSON | LEAN launcher 配置 | `config-ashare-overnight-anomaly-backtest.json` |

---

## 3. 信号数学

### 3.1 主公式（AU.SHF 日线，人民币计价，免 FX 调整）

窗口意图：518880 收盘到开盘之间（15:00 T-1 → 09:30 T，北京时间）的国际金价变动。

```
R_au_overnight(T)  = open(T, AU.SHF) / pre_close(T, AU.SHF) − 1   # 人民币金价隔夜变动
Gap_expected(T)    = R_au_overnight(T)                              # 简化版（docs §2 通用版）
Gap_actual(T)      = open(T, 518880) / pre_close(T, 518880) − 1
Signal(T)          = Gap_expected(T) − Gap_actual(T)
Z(T)               = (Signal(T) − mean_60(Signal)) / std_60(Signal)
```

免 FX 调整——AU.SHF 本身人民币计价（选它而非 FXCM 的全部理由，`gold-check-design2.md`）。因果链：AU `open(T)` 在 21:00 cal T-1 已知，518880 `open(T)` 在 09:30 cal T 已知，Z(T) 在 09:30 cal T 开盘时算，无 lookahead。

### 3.2 SHFE `open` 约定歧义（必须诚实承认）

tushare `fut_daily` 对 SHFE 的 `open` 可能指夜盘开盘（21:00 cal T-1，只覆盖 15:00→21:00 这 6h 晚盘缺口）或日盘开盘（09:00 cal T，覆盖完整 18h 隔夜）。日线粒度**无法**干净切出 21:00→02:30 这段 COMEX/SHFE 夜盘主力交易窗口——那需要 `ft_mins` 分钟级数据（`api_registry.py` 中 `enabled=False`，需 10000+ 积分）。因此 AU.SHF 日线信号**至多是部分代理**。

**Gate 0 记录 `open` 约定诊断（不判定有效无效）；Gate 2（IC）才是有效性 kill switch**：Gate 0 只算并记录 AU `open/pre_close` 与 518880 `open/pre_close` 的相关性及强度分布，用于事后归因；真正判定"这个日线代理是否有效"的是 Gate 2 的 OOS IC。若 OOS IC 不达标，策略标记 `EFFECTIVENESS_FAIL`、排除灰度发布，附升级说明"需 `ft_mins`（10000 积分）做真夜盘隔离"。这是"有效而非仅可用"的硬执行——策略上线仅当 IC 证明日线代理有效。

### 3.3 交叉校验（FXCM XAUUSD 降为旁路，不替代主信号）

`gold-check-design2.md` §point 3：AU 夜盘与 FXCM XAUUSD 日线方向背离且 `|R_au − R_fxcm|` > 阈值 → `skip_reason = CROSS_CHECK_FAIL`，当天不交易。AU 与 SGE 现货金通常基差很小但不能假设永远可忽略。

---

## 4. 双正交状态字段（用户建议，已采纳）

两字段独立——任意 (skip_reason, regime) 组合合法，修复优先级完全不同。

### 4.1 `regime` ∈ {RISING_FAST, FALLING_FAST, STABLE, DRIFTING, UNAVAILABLE}

- `UNAVAILABLE` ≠ `STABLE`：行为相同（不降仓），状态不同。为将来 FRED 接入留可解释性。次要 filter 缺失，低修复优先级。
- 当前 FRED_API_KEY 未配置 → regime 恒输出 `UNAVAILABLE`。

### 4.2 `skip_reason` ∈ {NONE, DATA_STALE, INSUFFICIENT_HISTORY, CROSS_CHECK_FAIL, NO_EDGE}

- `DATA_STALE` — AU/518880 时间戳滞后 > 2h（实盘）或回测 T 日缺行。**核心信号故障，高修复优先级**。
- `INSUFFICIENT_HISTORY` — Z 计算所需 60 日信号历史不足（冷启动）。
- `CROSS_CHECK_FAIL` — AU 方向与 FXCM XAUUSD 方向相反且幅度差超阈值（§3.3）。
- `NO_EDGE` — `|Z|` ≤ 1.5，正常不进场，非故障。

回测复盘查询 `WHERE skip_reason='DATA_STALE'` 找核心信号损坏的天，与 `WHERE regime='UNAVAILABLE'`（次要 filter 缺失）完全可区分。两字段不共享枚举。

---

## 5. 风控规则

- **long-only 基线**。518880 是 T+0 但不可做空（无融券）。`Z > 1.5` → 09:30 开盘做多；`Z < −1.5` → 空仓观望（捕捉到超调反转但无法做空，故观望）；`|Z|` ≤ 1.5 → 空仓。非对称性有文档；空头侧未来用反向工具增强。
- **仓位**：vol-scaled（`target_vol / realized_vol`，clamp [0.25, 2.0]，镜像 `AShareOvernightAnomaly`）× `position-size` × regime cap。单标的（仅 518880）。
- **止损**：开盘价下方 0.5 × ATR(14)（多头侧）。
- **止盈**：达到 `0.6 × |Gap_expected(T)|` 绝对收益幅度即止盈离场。
- **14:45 强制平仓**：`NoOvernightPositionRiskModel`（一个 `IRiskManagementModel`）**硬**执行平仓——不靠策略自觉。这是 `INoOvernightPosition` marker 意图的落地，但**不新增 C# marker 接口**（risk model 即是强制点）。
- **regime cap**：`regime == RISING_FAST` → 仓位上限降到 0.3×。`regime == UNAVAILABLE` → 不降仓（按 `STABLE` 行为），记日志。

---

## 6. Gate 0 / 1 / 2

### 6.1 Gate 0 — 数据完整性（在导出器内，回测前跑）

- AU.SHF + AUL.SHF 回填完成：本地 parquet 最早日期 ≤ 回测 start − 60 交易日（`gold-check-design3.md` §point 3：查最早日期非总行数，防中间大段缺失）。
- `fut_mapping(AUL.SHF)` 时间范围与 `fut_daily(AU.SHF)` **对齐**（§1.3 约束 3）。
- 518880 fund_daily 覆盖回测窗口。
- 无 lookahead：校验 AU `open(T)` 时间戳 ≤ 518880 `open(T)` 时间戳（因果性）。
- `skip_reason` 分布有记录（多少 `DATA_STALE` 天等）。
- Z_signal 非 NaN/非零比例 > 5%（复用 `BarraCNE5V4`/`OvernightAnomaly` 的 zeroed-field 检测）。
- SHFE `open` 约定诊断：AU `open/pre_close` 与 518880 `open/pre_close` 之间相关性，记录信号强度。

### 6.2 Gate 1 — 成本敏感度

- 信号阈值净 of 往返交易成本 > 0。`transaction_cost_bps = 15`（518880 价差估算）。`|Gap_expected|` 期望收益须超 15bps 才值得交易。

### 6.3 Gate 2 — regime 稳定性 / IC kill switch（有效性守门）

- 全样本 20 日 rolling IC 符号一致性 > 60%。
- **OOS IC > 0**（在训练期之后计算）。**失败 → 策略标 `EFFECTIVENESS_FAIL`、排除灰度发布、附 ft_mins 升级说明**。这是"有效而非仅可用"的硬执行。

---

## 7. 测试

- **导出器信号数学单测**：合成 AU+518880 序列，已知 Z，验证输出 + 因果性（无 T+1 泄露）。
- **OvernightPremiumFactor / RealRateRegimeFactor 单测**：`InjectValue`/`Compute`，镜像 `AuctionGapFactorTests`。
- **skip_reason 正交性测试**：注入一个 `DATA_STALE` 日 → `skip_reason=DATA_STALE`, `regime=STABLE`（交易跳过，regime 正常）。注入一个 `UNAVAILABLE` 日 → `regime=UNAVAILABLE`, `skip_reason=NONE`（正常交易，不降仓）。证明两字段互不串扰。
- **NoOvernightPositionRiskModel 测试**：14:44 持仓在 14:45 被强平；无任何仓位能跨过 15:00。
- **CROSS_CHECK_FAIL 测试**：注入 AU 与 FXCM 背离 → `skip_reason=CROSS_CHECK_FAIL`。
- **集成回测 2020-2026**：Sharpe、drawdown、断言**从不**持隔夜仓、`skip_reason`/`regime` 分布日志。

---

## 8. 回填前置（Gate 0 硬前提，必须最先完成）

1. 扩展 `tushare-downloader` 的 `fut_daily` + `fut_mapping` 抓取范围，全量拉 `AU.SHF` + `AUL.SHF` 2008→至今到本地 parquet。
2. 回填未完成则算法**拒绝启动**（Gate 0 的最早日期检查即此语义）。
3. `fut_mapping` ↔ `fut_daily` 范围对齐作为并列硬检查（§6.1）。

---

## 9. 已知局限（显式记录，不假装覆盖）

1. **SHFE `open` 约定歧义**：日线粒度无法干净切 21:00→02:30 夜盘主力窗口，信号是部分代理。Gate 2 IC 是 kill switch。
2. **02:30→09:30 窗口空白**：AU 夜盘 21:00→02:30 覆盖亚欧盘大部分，但覆盖不到 02:30 之后到 A 股开盘前（美盘尾盘、亚太早间部分波动）。比 FXCM 日线窗口不精确要小，但存在，显式记录（`gold-check-design2.md` §point 2）。
3. **long-only**：518880 不可做空，`Z < −1.5` 只能观望。空头侧 deferred。
4. **regime = UNAVAILABLE 恒定**：FRED_API_KEY 未配置，实际利率 regime 暂不可用。当前行为与 `STABLE` 相同，状态区分开为将来留口子。
5. **USD:RMB = 1:1 项目设定**：本策略用人民币计价 AU 期货顺带规避此问题，不依赖 FX 换算。

---

## 10. 后续（写实现计划时展开）

- 组件 build 顺序：回填 → 导出器 → custom BaseData → 两 IFactor → risk model → 算法 → manifest/config → Gate 0/1/2 → 测试。
- 每个组件的文件路径、接口签名、错误处理细节、测试夹具在 writing-plans 阶段细化。
