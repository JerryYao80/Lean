# P2-C: Barra 因子风险分解 设计

> **日期**: 2026-08-07
> **子项目**: P2-C (3351 框架 P2 五层架构缺口 — L4 风险层)
> **前置**: P2-A (IC/IR 回灌) 已完成、P2-B (真 MVO + T1 执行) 已完成
> **状态**: 设计已确认，待写实现计划

## 1. 背景与动机

### 1.1 现状问题

P2-B 把 L3 换成真 MVO、L5 换成 T1 顺序执行，但 **L4 风险层仍是空的**：
- `AShareCSI300MVOStrategy` 没有设置任何 `IRiskManagementModel`
- `AShareBarraCNE5V4RiskManagementModel` 名为 Barra，实为 Sharpe/波动率缩放，**不含 Barra 因子风险分解**
- `AShareBarraCNE5V2Algorithm` 把 Barra 因子当 **L2 alpha 信号** 用，不是 L4 风险

也就是说：项目里有 Barra 因子数据（`Data/alternative/barra-cne5v2-factors_csi300_0608/`，300 symbols × 2005-2026 × 15 因子），但**因子风险分解这一半完全没做**。Barra 的核心价值——`w'BΣ_fB'w + w'Δw` 结构化组合方差——从未被计算过。

### 1.2 P2-C 要做什么

补齐 L4：用 Barra CNE5 因子风险模型做**结构化风险分解**，分四块：

1. **Offline 风险数据生成**（Python）：从 Barra 因子暴露 + 收益率回归出因子协方差 Σ_f (15×15) 和个股特异性方差 Δ
2. **MVO 协方差来源可替换**：给 `export_mvo_weights.py` 加 `ICovarianceProvider` 接口，Barra 结构化协方差 Σ = BΣ_fB' + diag(Δ) 可注入，与历史样本协方差二选一
3. **L4 BarraFactorRiskModel**（C#）：`IRiskManagementModel`，读 barra_risk JSON，算组合方差，做因子暴露预算 + 波动率目标
4. **策略装配 + 回测对比**：`AShareCSI300BarraStrategy` + `config_barra.json`，与 P2-B MVO 基线对比

### 1.3 与 P2-B 的解耦

P2-B 用历史样本协方差（Ledoit-Wolf 收缩）。P2-C 通过 `ICovarianceProvider` 接口让 Σ 来源可替换——这是 P2-B spec 里预留的接口，现在落地。**不修改 P2-B 已交付的默认行为**（默认仍是 `HistoricalCovarianceProvider`，向后兼容）。优化器与策略解耦原则不变：多个量化策略可共享同一优化器，Σ 来源是优化器内部可选项，不绑定具体策略。

## 2. 设计目标

1. **结构化风险分解**：实现 Barra `w'BΣ_fB'w + w'Δw` 组合方差，区分系统性因子风险与特异性风险
2. **协方差来源可替换**：`ICovarianceProvider` 接口，historical（默认）/ barra 二选一，CLI 控制
3. **L4 风险约束**：因子暴露预算 + 组合波动率目标，对 MVO 出来的权重做风险层调整
4. **零侵入**：不动 P2-B 的 `MVOAlphaPortfolioConstructionModel`、`AShareT1SequentialExecutionModel`、`export_mvo_weights.py` 默认行为；不动 V2/Barra V2 等成熟功能
5. **热路径无 pythonnet**：C# runtime 只读 JSON，scipy 回归/Ledoit-Wolf 在 offline
6. **无前视**：Σ_f 估计窗口 `[as_of - est_window, as_of - 1]`，不含 as_of 当日
7. **LEAN-native**：L4 实现 `IRiskManagementModel.ManageRisk`，返回调整后的 `IPortfolioTarget[]`

## 3. 架构

```
┌─ Offline (Python, 月度) ─────────────────────────────────┐
│ export_barra_risk.py                                      │
│   Barra因子暴露B + 日线收益率 → 截面OLS r=B·f+ε          │
│   → Σ_f (15×15 Ledoit-Wolf) + Δ (per-symbol 指数衰减)    │
│   → result/barra-risk/barra_risk_YYYY-MM-DD.json         │
│   (est_window=504, decay_halflife=252, 无前视)           │
└───────────────────────────────────────────────────────────┘
                        ↓ JSON
┌─ Offline MVO (Python, 月度, P2-B 扩展) ──────────────────┐
│ export_mvo_weights.py + ICovarianceProvider              │
│   --covariance-source {historical,barra}                 │
│   BarraCovarianceProvider: Σ = BΣ_fB' + diag(Δ)          │
│   → mvo_weights_YYYY-MM-DD.json (schema 新增 cov_source) │
└───────────────────────────────────────────────────────────┘
                        ↓ JSON
┌─ Runtime (C#, 月度再平衡) ────────────────────────────────┐
│ MVOAlphaPortfolioConstructionModel (L3, P2-B 不动)        │
│ BarraFactorRiskModel (新 L4)                              │
│   读 barra_risk_*.json → 算 w'BΣ_fB'w+w'Δw              │
│   因子暴露预算 + vol targeting → 调整后 IPortfolioTarget  │
│ AShareT1SequentialExecutionModel (L5, P2-B 不动)          │
└───────────────────────────────────────────────────────────┘
```

### 3.1 数据流

1. **Offline 风险数据**（月度，对每个再平衡日 t）：
   - 取 t 向前 504 日 Barra 因子暴露 B (dates×symbols×15) + 后复权日线收益率 r (dates×symbols)
   - 截面 OLS 回归 `r_t = B_t·f_t + ε_t` 逐日估因子收益 f_t → 时序得 f (dates×15)
   - `Σ_f = LedoitWolf(f) × 252`（年化）
   - `Δ_s = exp_decay_var(ε_s, halflife=252) × 252`（per-symbol 特异性方差年化）
   - 输出 `barra_risk_{t}.json`

2. **Offline MVO**（月度，P2-B 扩展）：
   - `--covariance-source barra` 时 `BarraCovarianceProvider` 读 `barra_risk_{t}.json` + 当日因子暴露 B_t
   - `Σ = B_t · Σ_f · B_t' + diag(Δ)`（结构化协方差 N×N）
   - 其余 MVO 流程不变 → `mvo_weights_{t}.json`（新增 `cov_source` 字段）

3. **Runtime**（C# 月度再平衡）：
   - L3 `MVOAlphaPortfolioConstructionModel` 出 PortfolioTarget（不动）
   - L4 `BarraFactorRiskModel.ManageRisk` 读 `barra_risk_*.json`，算组合方差，因子暴露超预算则缩放，vol targeting 调整体位
   - L5 `AShareT1SequentialExecutionModel` 先卖后买（不动）

## 4. 组件设计

### 4.1 `Scripts/factor_zoo/export_barra_risk.py`（新建）

**职责**: offline 月度 Barra 因子风险数据生成器

**输入**:
- Barra 因子数据目录（`Data/alternative/barra-cne5v2-factors_csi300_0608/{sse,szse}/daily/<code>.csv`）
- 后复权日线数据（`tushare_data_v2/daily/` + `adj_factor/`）
- 再平衡日列表（月末交易日）

**核心逻辑**:
```python
class BarraRiskExporter:
    FACTORS = ["beta","momentum","size","earnyld","resvol","growth",
               "btop","leverage","liquidity","nlsize","moneyflow",
               "quality","northbound","margin","chipcost"]  # 15

    def __init__(self, factor_data_dir, daily_data_dir, adj_factor_dir,
                 est_window=504, decay_halflife=252):
        ...

    def export_for_dates(self, rebalance_dates, output_dir):
        for dt in rebalance_dates:
            risk = self._compute_risk_for_date(dt)
            self._write_json(dt, risk, output_dir)

    def _compute_risk_for_date(self, as_of):
        # 1. 取 as_of 前 504 日因子暴露 B (dates×symbols×15) + 收益率 r (dates×symbols)
        B, r = self._load_factor_and_returns(as_of, self.est_window)
        # 2. 截面 OLS 逐日回归 r_t = B_t·f_t + ε_t (带截距, dropna symbols)
        f, residuals = self._cross_sectional_regression(B, r)
        # 3. Σ_f = LedoitWolf(f) × 252 (15×15)
        sigma_f = self._estimate_factor_cov(f)
        # 4. Δ_s = exp_decay_var(ε_s, halflife) × 252 (per-symbol)
        delta = self._estimate_specific_var(residuals)
        # 5. 取 as_of 当日因子暴露 B_t (供 MVO 构造 Σ)
        B_t = self._load_factor_exposure_asof(as_of)
        return {"sigma_f": sigma_f, "delta": delta, "B_t": B_t}

    def _cross_sectional_regression(self, B, r):
        """逐日截面 OLS: r_t = B_t·f_t + ε_t, 返回因子收益时序 + 残差"""

    def _estimate_factor_cov(self, f):
        """LedoitWolf(f) × 252 + 1e-8 ridge"""

    def _estimate_specific_var(self, residuals):
        """per-symbol 指数衰减方差, halflife=252, ×252 年化"""
```

**expanding-window 无前视约束**:
- 估计窗口 `[as_of - est_window, as_of - 1]`（不含 as_of 当日）
- as_of 当日因子暴露 B_t 仅用于 MVO 构造 Σ，不参与 Σ_f 估计
- 缺因子数据的 symbol 在截面回归当日 dropna 剔除

**JSON schema** (`barra_risk_YYYY-MM-DD.json`):
```json
{
  "as_of": "2024-01-31",
  "factors": ["beta","momentum","size","earnyld","resvol","growth",
              "btop","leverage","liquidity","nlsize","moneyflow",
              "quality","northbound","margin","chipcost"],
  "sigma_f": [[15×15 双精度数组]],
  "delta": {"600519.SH": 0.12, "000858.SZ": 0.09},
  "exposures": {"600519.SH": [15 个因子暴露]},
  "est_window": 504,
  "decay_halflife": 252,
  "n_symbols": 300,
  "n_obs": 504,
  "fallback": null
}
```

**降级策略**:
- 因子数据缺失的 symbol → 当日截面回归剔除，特异性方差记 NaN
- 估计窗口内有效观测 < 252（半窗）→ 跳过该日，记 warning
- Σ_f 非正定 → 加大 ridge
- 全市场截面 symbol < 30 → 回退对角 Σ_f（标记 `"fallback": "diagonal"`）

### 4.2 `Scripts/factor_zoo/export_mvo_weights.py`（扩展，不改默认行为）

**职责**: 加 `ICovarianceProvider` 接口，Σ 来源可替换

**关键设计**:
- 新增抽象接口 `ICovarianceProvider`，方法 `estimate(symbols, as_of) -> (sigma, meta)`
- `HistoricalCovarianceProvider`（默认，P2-B 已有逻辑搬入，**行为完全不变**）
- `BarraCovarianceProvider`：读 `barra_risk_{as_of}.json`，`Σ = B_t·Σ_f·B_t' + diag(Δ)`
- CLI `--covariance-source {historical,barra}`，默认 `historical`
- JSON schema 新增 `cov_source` 字段（`"historical"` / `"barra"`），历史默认值不变

```python
class ICovarianceProvider:
    def estimate(self, symbols, as_of) -> tuple[np.ndarray, dict]:
        """返回 (sigma NxN, meta dict)"""

class HistoricalCovarianceProvider(ICovarianceProvider):
    # P2-B 已有 _estimate_covariance 逻辑搬入, 行为不变
    ...

class BarraCovarianceProvider(ICovarianceProvider):
    def __init__(self, barra_risk_dir):
        self.barra_risk_dir = barra_risk_dir

    def estimate(self, symbols, as_of):
        risk = self._load_latest_barra_risk(as_of)  # ≤ as_of 最新
        B_t = np.array([risk["exposures"][s] for s in symbols])  # N×15
        sigma_f = np.array(risk["sigma_f"])  # 15×15
        delta = np.array([risk["delta"].get(s, np.nan) for s in symbols])  # N
        sigma = B_t @ sigma_f @ B_t.T + np.diag(np.nan_to_num(delta))
        return sigma, {"cov_source": "barra", "barra_risk_used": risk["as_of"]}
```

**向后兼容**: 不传 `--covariance-source` → `HistoricalCovarianceProvider` → 与 P2-B 输出逐字节一致（除新增 `cov_source: "historical"` 字段）。

### 4.3 `Algorithm.CSharp/Models/Risk/BarraFactorRiskModel.cs`（新建）

**职责**: L4 风险管理，读 barra_risk JSON，算组合方差，因子暴露预算 + vol targeting

**关键设计**:
- 实现 `IRiskManagementModel`
- `ManageRisk` 接收 L3 出来的 `IPortfolioTarget[]`，读最新 `barra_risk_*.json`
- 算当前组合方差 `w'BΣ_fB'w + w'Δw`
- **因子暴露预算**：单因子暴露 `|b_i·w|` 超阈值 → 该方向缩放
- **vol targeting**：组合年化波动率 > target_vol → 整体降杠杆（缩放权重 + 剩余转现金）
- 返回调整后的 `IPortfolioTarget[]`

```csharp
public class BarraFactorRiskModel : IRiskManagementModel
{
    private readonly string _barraRiskDir;
    private readonly decimal _targetVol;          // 年化目标波动率, 如 0.20
    private readonly decimal _maxFactorExposure;  // 单因子暴露预算, 如 0.5
    private BarraRiskData _riskCache;
    private DateTime _riskAsOf;

    public BarraFactorRiskModel(string barraRiskDir,
        decimal targetVol = 0.20m, decimal maxFactorExposure = 0.5m) { ... }

    public IEnumerable<IPortfolioTarget> ManageRisk(
        QCAlgorithm algorithm, IPortfolioTarget[] targets)
    {
        // 1. 月度刷新风险数据 LoadLatestRisk(algorithm.Time)
        // 2. 构造权重向量 w (从 targets + 当前持仓)
        // 3. 组合方差 = w'BΣ_fB'w + w'Δw
        // 4. 因子暴露预算: |B'w|_i > max → 缩放
        // 5. vol targeting: sqrt(var)*sqrt(252) > targetVol → 整体缩放
        // 6. 返回调整后 targets
    }

    private void LoadLatestRisk(DateTime asOf) { ... }  // ≤ asOf 最新 JSON
}
```

**与 L3 的边界**: L3 (MVO) 已经在权重生成时考虑了 Σ（无论 historical 还是 barra）。L4 的职责不是重新优化，而是**风险约束后处理**——因子暴露预算防集中、vol targeting 防整体过杠杆。两层职责清晰分离。

**降级策略**:
- 风险数据加载失败 → 记 error log，返回原 targets 不调整（可见降级，不静默吞错）
- 某 symbol 缺因子暴露 → 该 symbol 不参与因子风险计算，特异性方差用 0（保守）
- 组合方差算出 NaN → 不调整，记 error

### 4.4 `Algorithm.CSharp/AShareCSI300BarraStrategy.cs`（新建）

镜像 `AShareCSI300MVOStrategy`，差异:
- 新增 L4: `SetRiskManagement(new BarraFactorRiskModel(barraRiskDir, targetVol, maxFactorExposure))`
- L3 仍 `MVOAlphaPortfolioConstructionModel`（读 MVO JSON，可指定 cov_source=barra 生成的）
- L5 仍 `AShareT1SequentialExecutionModel`
- 新增 `barraRiskDir` 参数

### 4.5 `Launcher/config_barra.json`（新建）

指向 `AShareCSI300BarraStrategy` + `mvoWeightsDir` + `barraRiskDir` 参数。

## 5. 关键约束与原则

| 约束 | 实现 |
|---|---|
| 优化器与策略解耦 | `ICovarianceProvider` 接口隔离 Σ 来源，多个策略可共享 MVO 产物 |
| 零侵入 | 不动 P2-B 已交付组件、不动 V2/Barra V2 等成熟功能 |
| 无前视 | Σ_f 估计窗口 `[as_of-504, as_of-1]`；B_t 仅用于构造 Σ 不参与估计 |
| 热路径无 pythonnet | C# 只读 JSON，截面回归/Ledoit-Wolf 在 offline |
| LEAN-native | L4 实现 `IRiskManagementModel`，返回 `IPortfolioTarget[]` |
| 不绕开硬阻塞 | Barra 风险分解正面实现，不靠"加大 ridge 掩盖非正定"假装通过 |
| 可见降级 | 风险数据缺失 → log + 不调整（不静默返回 0 权重） |

## 6. 验证标准

### 6.1 Offline 风险数据 (pytest, ≥8)
- 截面 OLS 回归正确性（已知 B、r → 已知 f）
- Σ_f 15×15 对称正定
- Δ per-symbol 非负
- 指数衰减方差 halflife 正确
- expanding-window 无前视（est 窗口不含 as_of）
- 缺因子数据 symbol 当日剔除
- 有效观测 < 半窗跳过
- JSON schema 完整

### 6.2 MVO Provider (pytest, ≥4)
- `HistoricalCovarianceProvider` 与 P2-B 输出一致（回归测试）
- `BarraCovarianceProvider` 构造 Σ = BΣ_fB' + diag(Δ) 正确
- CLI `--covariance-source` 切换
- 向后兼容（不传参 = historical）

### 6.3 Runtime L4 (NUnit, ≥5)
- JSON 加载（≤ asOf 选最新）
- 组合方差 w'BΣ_fB'w+w'Δw 计算正确
- 因子暴露超预算缩放
- vol targeting 降杠杆
- 风险数据缺失可见降级（不静默）

### 6.4 端到端回测
- `AShareCSI300BarraStrategy` 跑通，无 0 订单回归
- 与 P2-B MVO 基线对比：组合年化波动率应更接近 target_vol
- 拒单不显著上升（< 1000，基线 P2-B 为 267）
- portfolio value 不显著低于 P2-B 基线（1,429,398 CNY）

## 7. 不做（YAGNI）

- 在线 pythonnet 截面回归（破坏热路径原则）
- 修改 P2-B `MVOAlphaPortfolioConstructionModel` / `AShareT1SequentialExecutionModel`（成熟功能不动）
- 修改 `AShareBarraCNE5V2Algorithm` / `AShareBarraCNE5V4RiskManagementModel`（成熟功能不动）
- 多策略风险模型注册表（YAGNI，当前只有 CSI300）
- Barra 因子收益预测（这是 alpha 不是 risk，留 L2）
- 协方差多源混合（historical+barra 加权，YAGNI，二选一即可）
