# IV 实现审计结论（对照 `docs/iv-update8.md` 反思）

**审计日期：** 2026-07-06
**审计范围：** LEAN IV 实现（C# `Indicators/ImpliedVolatility.cs` + Python `Scripts/ashare_implied_volatility.py`）+ A 股消费者
**审计方法：** 4 路并行 workflow 审计（optionModel 用法 / CRR 提前行权 / 利率注入 / VIX 年化）+ 人工复核
**对照文档：** `docs/iv-update8.md`（4 条反思点）、`docs/iv-update9.md`（T 口径修复方案）

---

## 总账

| # | 反思点 | iv-update8 担心 | 实际核对 | 严重度 | 处理 |
|---|---|---|---|---|---|
| ① | 二叉树误用欧式 | 系统性压低深实值 IV | A 股消费者全显式 BSM，不触发 | 无 | 仅硬化 Delta 那处 |
| ② | CRR 提前行权 | 二叉树给欧式偏高价 | 代码非对称（call 美式/put 欧式），注释数学错误；A 股不走此路径 | 中（潜在 core bug） | 记录，不在本次修 |
| ③ | 利率注入 | C# 用默认 5% | 全注入 SHIBOR model，5% 不触发 | 无 | 无需改 |
| ④ | VIX 年化口径 | 242 vs CBOE 自然日 | 实为自然日/242 混合口径，比 CBOE 低 ~19% | 中 | 本次修 |
| ⑤ | **C# vs Python T 口径**（新发现） | 未提 | **C# 自然日/365，Python 自然日/242，两套 IV 不可比** | **中高** | **本次修** |

---

## ① 二叉树是否被误用于欧式期权 —— 无 bug（对 A 股消费者）

三个 A 股 IV 消费者**全部显式传 `OptionPricingModelType.BlackScholes`**：

| 文件 | 行 | 传参 |
|---|---|---|
| `AShareOptionVolatilityArbitrageAlgorithm.cs` | 212-213 | `optionModel: OptionPricingModelType.BlackScholes` |
| `AShareIVExportAlgorithm.cs` | 149-150 | `optionModel: OptionPricingModelType.BlackScholes` |
| `AShareHOVixAlgorithm.cs` | 117 | `optionModel: OptionPricingModelType.BlackScholes` |

唯一一处默认：`AShareOptionVolatilityArbitrageAlgorithm.cs:450` 的 `new Delta(optionSymbol, _rateModel, _divModel)` 没传 optionModel。但 `OptionIndicatorBase.GetOptionModel(null, European)`（`OptionIndicatorBase.cs:202-215`）对 European 样式返回 `BlackScholes`，而 `AShareOptionChainProvider.cs:79` 把 510050 ETF 期权标记为 `OptionStyle.European`，所以默认解析仍是 BSM，不走二叉树分支。

**结论：** iv-update8 担心的"二叉树误用于欧式 → 系统性压低深实值 IV"在本项目 A 股路径上不成立。
**硬化建议：** 把 Delta 那处也写死 `optionModel: OptionPricingModelType.BlackScholes`，让正确性不依赖 style-tag（本次未改，记录待办）。

---

## ② CRR 二叉树的提前行权 —— 有 bug（潜在，不影响 A 股路径）

`OptionGreekIndicatorsHelper.BinomialTheoreticalPrice`（`Indicators/OptionGreekIndicatorsHelper.cs:121-164`）的节点更新逻辑**非对称**：

```csharp
// line 148-161
for (var period = steps - 1; period >= 0; period--)
{
    for (var i = 0; i <= period; i++)
    {
        var binomialValue = values[i] * factorA + values[i + 1] * factorB;
        // No advantage for American put option to exercise early in risk-neutral setting   ← 数学上错误
        if (optionType == OptionRight.Put)
        {
            values[i] = binomialValue;          // Put: 纯欧式回推，无提前行权
            continue;
        }
        values[i] = Math.Max(binomialValue, exerciseValues[2 * i - period + steps]);  // Call: 美式 max(intrinsic, continuation)
    }
}
```

- **Call 分支是美式语义**（每个节点 `Math.Max(续期值, 内含价值)`），`exerciseValues` 缓存只在 `optionType == Call` 时分配（line 127）
- **Put 分支是欧式语义**（纯回推，无提前行权检查）
- Line 153 的注释 "No advantage for American put option to exercise early in risk-neutral setting" **数学上错误**——美式 put 在深实值（尤其有股息）时确有提前行权价值

**与 iv-update8 担心的方向相反：** iv-update8 担心二叉树给欧式期权算出"偏高的美式价 → 压低 IV"。实际代码里 put 是纯欧式（不会偏高），call 才是美式（会偏高）。所以：
- 美式 put 用此代码 → 模型价偏低 → Brent 解出**偏高**的 IV
- 欧式 call（如 SPX）用 CRR → 模型价偏高 → Brent 解出**偏低**的 IV（这才是 iv-update8 担心的机制，但发生在 call 腿，不是 put 腿）

**对本项目的实际影响：nil**——A 股消费者都走 BSM，不进二叉树分支。
**这是 LEAN 核心辅助类里的潜在 bug**，但本次不修（影响范围超出 A 股，需 LEAN 上游对齐）。记录待办：给 `CRRTheoreticalPrice`/`ForwardTreeTheoreticalPrice`/`BinomialTheoreticalPrice` 加 `OptionStyle` 参数，美式时 call 和 put 都做 `Math.Max`（put 也要分配 `exerciseValues`），欧式时两边都不做；删掉 line 153 的错误注释。

---

## ③ 无风险利率是否真注入 SHIBOR —— 无 bug

三个 A 股算法都构造 `AShareShiborRateModel(tusharePath)` 并作为 `IRiskFreeInterestRateModel` 传入：

```csharp
// AShareOptionVolatilityArbitrageAlgorithm.cs 等
_rateModel = new AShareShiborRateModel(tusharePath);
var iv = new ImpliedVolatility(c.Symbol, _rateModel, _divModel, optionModel: OptionPricingModelType.BlackScholes);
```

`AShareShiborRateModel`（`Common/Data/AShare/AShareShiborRateModel.cs`）：
- 实现 `IRiskFreeInterestRateModel`
- 从 `tushare_data_v2/shibor/year=*/data.parquet` 读 `1y` 列、`/100`（SHIBOR 报价是百分数）
- 按日期键查；缺失日期兜底 `0.02m`（2%），**不是** LEAN 的 5% 默认
- `ImpliedVolatility` 的 5% 常数只在 decimal 重载（`ImpliedVolatility.cs:228`）里，A 股算法用的是 model 重载（line 106），5% 永远不触发

**结论：** C# 与 Python 侧都用了真实 1Y SHIBOR，**利率口径一致**。iv-update8 的担心不成立。
**数据待办：** 确保 `tushare_data_v2/shibor/year=*/data.parquet` 覆盖回测区间，否则 2% 兜底会大批量触发。

---

## ④ VIX 年化口径 —— 方法学问题（且比 iv-update8 说的更严重）

Python 脚本用的是**混合口径**：分子是自然日（`days_to_maturity` line 371 用 `datetime` 减法），分母是交易日（`TRADING_DAYS_PER_YEAR = 242`）：

```python
# Scripts/ashare_implied_volatility.py
TRADING_DAYS_PER_YEAR = 242                                  # line 56
def days_to_maturity(trade_date, maturity_date):             # line 371
    td = datetime.strptime(trade_date, "%Y%m%d")
    md = datetime.strptime(maturity_date, "%Y%m%d")
    return (md - td).days                                    # ← 自然日
T_near = near_dte / TRADING_DAYS_PER_YEAR                    # line 473 ← 自然日/242
```

- 30 自然日：`T_code = 30/242 = 0.1240` vs CBOE `T_cboe = 30/365 = 0.0822`，**T 偏大 ~51%**
- 在 `compute_vix_for_term` 的 `σ² = (2/T)·Σ... - (1/T)·[F/K₀-1]²` 里，T 偏大 → `(2/T)` 偏小 → 方差偏小 → **VIX 偏低约 `√(242/365) ≈ 0.814`，即比 CBOE 口径低 ~19%**（比 iv-update8 估的 ~0.5% 大得多）
- 脚本**内部自洽**（ATM IV、25-delta、VIX 都用同一 T 基），所以同脚本内跨期限/跨标的比较有效；但绝对水平**不能**和 CBOE iVIX / SSE iVIX 横比

---

## ⑤ C# vs Python 的 T 年化口径不一致 —— 新发现，最值得修

这是四个审计角度各自独立看时没拼出来的问题：

| 管线 | T 年化基 | 代码位置 |
|---|---|---|
| **C# `ImpliedVolatility`** | **自然日 / 365** | `OptionGreekIndicatorsHelper.TimeTillExpiry` line 169：`(expiry - referenceDate).TotalDays / 365d` |
| **Python `ashare_implied_volatility.py`** | **自然日 / 242** | line 473/507/691：`dte / TRADING_DAYS_PER_YEAR` |

**同一张 510050 期权，C# 算的 IV 和 Python 算的 IV 不可直接比较**——Python 的 T 比 C# 大 ~51%（`365/242 ≈ 1.508`），对同一期权价格，BS 反推出的 sigma 会**系统性偏低**。理论上 `IV_python / IV_csharp ≈ √(242/365) ≈ 0.814`，即 Python 比 C# 低约 19%。

这与 ③ 的"利率口径一致"结论相互独立：利率对齐了，但 T 没对齐。如果用 Python 导出的 IV CSV（`AShareImpliedVolatilityData`）做信号、又用 C# `ImpliedVolatility` 指标做实盘希腊字母/套利，**两边的 IV 数字本来就不是一个口径**，套利阈值（如 `iv-rv-z-score-threshold`）在两套数字上含义不同。

---

## 修复方案（参考 `docs/iv-update9.md`）

### 本次修复：统一 Python IV 的 T 口径为 `calendar_dte / 365`

**改动文件：** `Scripts/ashare_implied_volatility.py`、`Tests/Python/Scripts/AShareImpliedVolatilityTests.py`

**改动点：**
1. 引入 `CALENDAR_DAYS_PER_YEAR = 365` 常量，IV 相关的 T 全部改用它
2. `TRADING_DAYS_PER_YEAR = 242` 保留（RV 模块仍用交易日年化是行业标准；本仓库 RV 模块 `export_vol_regime_feature_data.py` 用 Garman-Klass 日方差 + `log_rv` 对数尺度，无显式年化常数，不受影响）
3. `compute_atm_iv` / `compute_25delta_iv` / `compute_iv_surface_skew` / `compute_vix_for_term` / 30 天插值的 T 全改 `calendar_dte / 365`
4. 测试文件 `Tests/Python/Scripts/AShareImpliedVolatilityTests.py` 里 `30 / 242` 改 `30 / 365`

**预期影响：**
- 同一期权价格下，T 变小（`30/365=0.0822` vs `30/242=0.1240`），BS 反解的 sigma 变大
- `IV_new / IV_old ≈ √(365/242) ≈ 1.226`，即 IV 上升约 22.6%
- VIX 同比例上升约 19%（`√(365/242) ≈ 1.226`，但 VIX 经过方差插值，非纯 1:1）
- `iv-rv-z-score-threshold` 等阈值需用改口径后至少 3-6 个月滚动窗口重新校准 z-score 分布（**不在本次代码改动范围**，需策略侧 separately 处理）

**不改动：**
- RV 模块（`export_vol_regime_feature_data.py`）：Garman-Klass 日方差 + log 尺度，无年化常数
- C# 侧 `TimeTillExpiry`：已经是 `calendar/365`，无需改
- 二叉树提前行权（②）：影响范围超出 A 股，需 LEAN 上游对齐，本次不动

### 待办（不在本次修）
- ② 二叉树 call/put 非对称：LEAN core 改动，需上游对齐
- ① Delta 那处写死 BSM：硬化，低优先级
- CBOE 分钟级 T 精度：iv-update9 建议近月（<7 天）用分钟折算，本次先用整数自然日，短端精度待后续

---

## 验证步骤（iv-update9 建议）

1. 挑 1-2 个历史日期，用改前/改后 T 分别算 50ETF ATM IV，验证比值收敛到 `√(365/242) ≈ 1.226` 附近
2. 找外部 50ETF 波动率指数历史值做同日对比，改后应显著更接近
3. `iv_rv_z_score` 阈值用改口径后 3-6 个月滚动窗口重新校准
4. 锁定"改前 T → 改后 T → IV 变化比例"链路，防止以后再被误改

---

## 来源文件

- `docs/iv-update8.md`（4 条反思点）
- `docs/iv-update9.md`（T 口径修复方案）
- `Indicators/ImpliedVolatility.cs`
- `Indicators/OptionGreekIndicatorsHelper.cs`（line 121-164 二叉树，line 169 TimeTillExpiry）
- `Indicators/OptionIndicatorBase.cs`（line 202-215 GetOptionModel 默认解析）
- `Algorithm.CSharp/AShareOptionVolatilityArbitrageAlgorithm.cs`（line 212-213, 450）
- `Algorithm.CSharp/AShareIVExportAlgorithm.cs`（line 149-150）
- `Algorithm.CSharp/AShareHOVixAlgorithm.cs`（line 117）
- `Common/Data/AShare/AShareShiborRateModel.cs`
- `Common/Data/AShare/AShareOptionChainProvider.cs`（line 79 European tag）
- `Scripts/ashare_implied_volatility.py`（line 56, 371, 473, 507, 523, 691）
- `Scripts/export_vol_regime_feature_data.py`（RV 模块，无年化常数）
- `Tests/Python/Scripts/AShareImpliedVolatilityTests.py`（line 86, 93, 197, 218, 241）
