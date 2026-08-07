# Volatility Forecasting & Return Prediction under Market Regimes — A-Share Reproduction Design

**Source paper:** *Volatility Forecasting and Return Prediction under Market Regimes: Evidence from High-Frequency Chinese Equity Data* (Fang & Ślepaczuk, arXiv:2606.09478, 2026)
**Strategy ID:** `vol-regime-hf-china`
**Date:** 2026-06-24
**Original market:** Chinese A-share (CSI 300) — no market mapping required.
**Adaptation:** No high-frequency intraday data available in `tushare_data_v2`; daily-frequency proxies are used for realized-volatility measures.

---

## 1. Paper Core Idea

A two-stage index-timing framework:

1. **Stage 1 — Regime-aware volatility forecasting.** Realized volatility (RV) is modeled with a regime-augmented HARQ specification combined with Markov-switching GJR-GARCH filtering. Captures long-memory, asymmetry, and structural market regimes.
2. **Stage 2 — ML return prediction.** Volatility forecasts, regime indicators, and lagged-return predictors feed an XGBoost return-prediction model, estimated strictly walk-forward.
3. **Trading strategy — Low-vol Gated Weekly Signal×Risk.** The empirical value comes primarily from implementation design (volatility scaling, low-vol gating, threshold calibration, turnover control), *not* from strong unconditional return forecasts. Predictive content is concentrated in low-volatility regimes.

## 2. Data Adaptation (Daily Proxy)

| Paper quantity (HF) | Daily proxy | Source |
|---|---|---|
| Realized Variance RV | Garman–Klass estimator `0.5·ln(H/L)² − 0.39·ln(C/O)²` | `daily` OHLC |
| Realized Quarticity RQ | Rolling 5-day 4th moment of daily returns | `daily` close |
| Signed Jump | `sign(r_t) · |r_t − σ̄_t|` | `daily` close |
| Regime probability p_t | Markov-switching 2-state on log RV (EM), walk-forward | derived |

Available tushare datasets: `fund_daily` (510300.SH ETF OHLCV, 2017-11-10 → 2026-06-23, ~2083 days), `daily_basic` (turnover_rate, pe, pb, etc. — note: ETFs use `fund_daily`, not `daily`). HF intraday datasets (`stk_mins`, tick) are absent — this is the documented adaptation.

## 3. Architecture (LEAN 5-Step Framework, Python algorithm)

| Step | Model | Notes |
|------|-------|-------|
| Universe | `ManualUniverseSelectionModel` | Fixed 510300 (CSI300 ETF, SZSE) |
| Alpha | `RegimeVolGatedAlphaModel : AlphaModel` | XGBoost forecast + HARQ/regime signal + threshold filter → Insight |
| Portfolio | `VolatilityScalingPCM : PortfolioConstructionModel` | signal→target weight; target \|w\|=0.5, cap=0.6, no-trade band |
| Risk | `MaximumDrawdownPercentPortfolio(0.15)` | Drawdown constraint |
| Execution | `ImmediateExecutionModel` | Weekly rebalance execution |

Algorithm base: `QCAlgorithm`. A-share compliance: T+1 settlement, `AShareStockFeeModel`, `AShareStockFillModel`, `SetBenchmark(_ => 0m)`, `ChinaInterestRateProvider` (SHIBOR 1Y), no `FineFundamental`.

## 4. Signal Pipeline

```
tushare daily(510300) + daily_basic
  → [Python script] export_vol_regime_feature_data.py
    → Data/alternative/vol-regime/510300_features.csv  (GK-RV, RQ, signed-jump, regime history)
  → [LEAN algorithm] warmup-reads features
    RegimeVolGatedAlphaModel:
      1. rolling σ̂ (GK / HARQ forecast)
      2. regime prob p_t  (EM warm-started from precomputed data, incremental)
      3. XGBoost walk-forward 3M re-estimation → r̂_{t+1}
      4. s_t = r̂/σ̂ ; gated (p_t>0.5 → κ=0) ; threshold q=0.60
      5. Insight(magnitude=filtered signal, direction=sign)
  → VolatilityScalingPCM:
      w*_t = clip(c_WF · s̃_t, ±0.6) ; no-trade band 0.02 ; weekly
  → LEAN InfluxDbResultExporter → lean_metric/chart/portfolio (native)
```

## 5. Technical Risks & Mitigations

1. **EM Markov-switching online cost.** Mitigation: precompute regime history offline (script); algorithm warm-starts and uses a rolling-σ̂ vs 60-day mean comparison as incremental fallback during the live walk-forward loop.
2. **XGBoost walk-forward.** Strictly expanding window, N_min=300, validation-based hyperparameter selection over a conservative grid (tree depth, learning rate, γ, λ).
3. **Python ML deps.** Requires `xgboost`, `statsmodels`, `numpy`, `pandas` in the Python.NET environment.

## 6. A-Share Compliance Checklist

- [x] 510300 plain ticker, Market.SZSE
- [x] T+1 settlement
- [x] `AShareStockFeeModel` (commission + stamp duty + transfer fee)
- [x] `AShareStockFillModel` (price limits, 100-share lots)
- [x] `SetBenchmark(_ => 0m)`
- [x] SHIBOR 1Y risk-free via `ChinaInterestRateProvider`
- [x] No `FineFundamental`
- [x] No original-market residue (already A-share)

## 7. Trading Parameters (paper baseline)

- Quarterly (3M) model re-estimation
- Weekly portfolio rebalancing
- Threshold quantile q = 0.60
- No-trade band b = 0.02
- Target mean absolute exposure 0.5
- Maximum portfolio weight w_max = 0.6
- Transaction cost: paper 5bp → LEAN uses real A-share fee model

## 8. Files

```
Scripts/export_vol_regime_feature_data.py          # feature preparation
Data/alternative/vol-regime/510300_features.csv   # warmup features
Algorithm.Python/VolRegimeHfChinaAlgorithm.py     # main algorithm
Algorithm.Python/Alphas/RegimeVolGatedAlphaModel.py
Algorithm.Python/Portfolio/VolatilityScalingPCM.py
Launcher/config/config-vol-regime-hf-china.json   # backtest config
Results/soloquant/strategy-traces/vol-regime-hf-china/  # trace
```

## 9. Metrics (LEAN-native only)

All performance metrics come from LEAN `InfluxDbResultExporter` (`lean_metric`): Sharpe, Sortino, Compounding Annual Return, Total Return, Max Drawdown, Win Rate, etc. No Python-computed metrics. GATE 3 requires Sharpe ≥ 0.

## 10. Gates

- GATE 0 paper_download: PASS (136,938 chars from arXiv)
- GATE 1 data_availability: daily proxies cover all 12 predictors → PASS
- GATE 2 code_smoke: 1-day backtest no exception
- GATE 3 backtest_sharpe: Sharpe ≥ 0
