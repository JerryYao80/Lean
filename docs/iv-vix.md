# IV 与 VIX 的区别及量化策略用途

> 日期：2026-06-29
> 配套文档：`docs/ashare_implied_volatility.md`（脚本功能与调度机制）、`docs/advance-factor.md`（前瞻性量化指标）

---

## 一、核心区别

| 维度 | IV (Implied Volatility) | VIX (Model-Free IV) |
|------|------------------------|---------------------|
| **定义** | Black-Scholes 模型反解的隐含波动率（针对特定行权价） | 模型无关隐含波动率（跨所有行权价积分） |
| **计算方法** | 从期权价格反推单个 σ 值（`compute_atm_iv` 取 ATM 期权 IV 的中位数） | CBOE VIX 公式：σ² = (2/T) Σ [ΔK/K²] e^(RT) Q(K)（积分方差） |
| **依赖假设** | 依赖 Black-Scholes 假设（欧式期权、无分红、波动率恒定） | **无模型假设**，从期权价格直接提取市场对波动率的预期 |
| **覆盖范围** | 单个行权价（ATM 或 25Delta） | **所有行权价**（OTM call + OTM put） |
| **输出单位** | 年化小数（如 0.25） | 年化百分比（如 25.0） |

---

## 二、脚本中的具体实现

源文件：`Scripts/ashare_implied_volatility.py`

### IV 计算（line 525-531）

```python
def compute_atm_iv(options, S, T, r):
    """Compute ATM implied volatility as median IV of near-the-money options."""
    # 1. 筛选 ±5% 内的期权
    # 2. 对每个期权用 Newton-Raphson 反解 BS 模型的 σ
    # 3. 返回中位数
```

**局限**：
- 只反映 ATM 附近的市场预期
- 受 BS 模型假设约束（尤其是波动率恒定假设）
- 忽略了偏度（skew）信息——不同行权价的 IV 不同

### VIX 计算（line 142-215）

```python
def compute_vix_for_term(options_df, F, T, r):
    """Compute model-free variance using CBOE VIX methodology.

    σ² = (2/T) Σ [ΔK/K²] exp(R*T) Q(K) - (1/T) [F/K₀ - 1]²
    """
    # 1. 找到 K₀（低于远期价格 F 的最高行权价）
    # 2. 对每个行权价 K：
    #    - K < K₀：用 put 价格（OTM put）
    #    - K > K₀：用 call 价格（OTM call）
    #    - K = K₀：用 (call + put)/2
    # 3. 加权积分所有贡献
    # 4. 返回方差 → σ = sqrt(variance)
```

**优势**：
- 模型无关——不依赖 BS 或任何定价模型
- 捕获**整个波动率曲面**的信息（all strikes）
- 反映市场对**未来 30 天波动率的完整预期**

---

## 三、输出字段（13 列）

```
trade_date, atm_iv, iv_call_25delta, iv_put_25delta, skew,
term_days_near, term_days_next, option_count, vix, sigma_near, sigma_next, t_near, t_next
```

### 字段用途对比

| 指标 | 用途 | 适用策略 |
|------|------|---------|
| **ATM IV** | 判断当前市场情绪（恐慌/平静） | 波动率择时、仓位调整 |
| **IV 25Delta (call/put)** | 捕获偏度——市场对单边风险的定价 | 偏度策略、tail risk hedging |
| **Skew** | 衡量看涨 vs 看跌情绪差异 | 方向性策略的风险管理 |
| **VIX** | 30 天整体波动率预期（最全面） | 波动率交易、动态风控 |

---

## 四、策略选择建议

### 1. 如果需要单一波动率指标

用 **VIX**（模型无关、覆盖所有 strikes）。

脚本输出：
- `vix` = 30 天模型无关隐含波动率
- `sigma_near` / `sigma_next` = 近月/次月单独计算的模型无关波动率

### 2. 如果需要偏度信息

用 `iv_call_25delta` / `iv_put_25delta` + `skew`：

- **skew > 0**：市场预期上涨风险更大（call IV > put IV）
- **skew < 0**：市场预期下跌风险更大（put IV > call IV）——典型的"恐惧偏度"

### 3. 如果需要 Black-Scholes 定价参数

用 `atm_iv`——这是 BS 模型的 σ 参数，适合：
- 期权定价/回测
- Greeks 计算
- 风险中性概率推导

---

## 五、学术研究警示

根据 `docs/advance-factor.md`（deep-research 报告）：

- **IV spread/skew 的"前瞻力"大部分是借券费 artifact**（Muravyev/Pearson/Pollet 2025, JFE 172）
- **Open-buy call-put ratio** 才是真正独立于 IV 的前瞻信号（预测分析师公告日前后收益）

**结论**：如果用 IV/VIX 做预测因子，要警惕"借券费嵌入"问题。A 股借券机制不同，需独立验证该效应是否适用。

---

## 六、对当前项目的建议

结合已有的筹码分布因子（PTR_2/TG）：

| 因子类型 | 信号维度 | 数据源 |
|---------|---------|--------|
| **筹码分布** | 持仓成本结构 | Tushare cyq_perf |
| **VIX** | 未来 30 天波动率预期 | 本脚本 |
| **IV skew** | 单边风险定价差异 | 本脚本 |

### 推荐组合

- 用 **VIX 做风控**（高 VIX → 降低杠杆或加波动率保护）
- 用 **筹码分布因子做选股**（识别主力成本结构）
- **避免**用 IV spread/skew 做收益预测（除非验证借券费效应在 A 股不存在）

### 当前消费状态

脚本当前只有 **Grafana 仪表盘**在消费 VIX 数据（`lean_ashare_iv`/`lean_ashare_vix`/`lean_ashare_iv_skew` 测量点），**回测端尚未接通**。

下一步：让策略通过 `AddData(AShareImpliedVolatilityData)` 加载 `Data/alternative/ashare-implied-volatility/sse/daily/*.csv` 作为因子。

---

**文档生成日期**：2026-06-29
