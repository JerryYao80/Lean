# Delay-Aware Graph Neural SDE (DGNSDE) — A-Share Core-Idea Approximation

**Source paper:** *Delay-Aware Graph Neural Stochastic Differential Equations for Financial Time Series Modeling and Forecasting* (You, Cheng, Zhang, Zhu, Liang; WWW 2026; DOI 10.1145/3774904.3792829)
**Strategy ID:** `delay-aware-gnn-sde-forecast`
**Date:** 2026-06-25
**Original market:** large-scale stock cross-section → A-share (CSI300 + CSI500)
**Adaptation:** Core-idea approximation — Neural SDE/GNN replaced by offline delay-aware correlation graph + walk-forward XGBoost slope prediction. Optimized for high Sharpe.

---

## 1. Paper Core Idea

DGNSDE is a two-stage deep-learning framework for stock selection:
1. **Delay-aware graph construction** — learns the lag (1-5 days) at which asset A's returns affect asset B. Captures delayed information propagation that instantaneous-correlation graphs miss.
2. **Two-stage prediction** — Stage 1 reconstructs continuous-time return trajectories (Neural SDE); Stage 2 predicts future trend slope via a GNN that aggregates cross-sectional delayed signals.

The slope predictions are alpha signals for large-scale cross-sectional stock selection.

## 2. Approximation Mapping (paper → A-share LEAN)

| Paper component (heavy DL) | Approximation (LEAN-native) | Rationale |
|---|---|---|
| Neural SDE trajectory reconstruction | Offline lagged-correlation graph + AR momentum features | SDE continuous dynamics ≈ "history predicts trend"; daily bars suffice |
| Delay-aware graph (learned lag) | N×N×5 cross-correlation tensor, computed offline | Lag search 1-5d; lead/follow strength per stock |
| GNN slope prediction | XGBoost regression (walk-forward) | Captures non-linear lead-lag→slope; LEAN Python.NET supports xgboost |
| Two-stage | Single XGBoost with lead-lag features | Simplify; preserve alpha source |

## 3. Architecture (LEAN 5-Step Framework, Python)

| Step | Model | Notes |
|------|-------|-------|
| Universe | `ManualUniverseSelectionModel` | CSI300 + CSI500 constituents (dynamic per rebalance) |
| Alpha | `DelayAwareSlopeAlphaModel : AlphaModel` | reads feature CSV, walk-forward XGBoost, slope prediction → Insight |
| Portfolio | `TopNEqualWeightPCM : PortfolioConstructionModel` | slope-ranked Top-20 long-only equal weight |
| Risk | `MaximumDrawdownPercentPortfolio(0.15)` | drawdown cap |
| Execution | `ImmediateExecutionModel` | weekly rebalance, A-share lot rounding |

Algorithm base: `QCAlgorithm`. A-share compliance: T+1, `AShareStockFeeModel`, `AShareStockFillModel`, `AShareStockBuyingPowerModel`, `SetBenchmark(0)`, `ChinaInterestRateProvider`, no `FineFundamental`.

## 4. Data Flow

```
OFFLINE (Script: compute_delay_aware_graph.py, monthly/quarterly run):
  tushare daily (CSI300+CSI500, ~800 stocks, last 252d)
    → log returns r_i[t]
    → for each pair (i,j), lag τ in {1..5}: corr(r_i[t], r_j[t-τ])
    → per-stock features:
        lead_strength_i   = max over j,τ of corr(r_i[t-τ], r_j[t])   (i leads others)
        follow_strength_i = max over j,τ of corr(r_i[t], r_j[t-τ])   (i follows others)
    → + momentum: ret_5d, ret_10d, ret_20d
    → + volume/turnover features
    → Data/alternative/delay-aware-gnn-sde/{csi300,csi500}_features.csv

ONLINE (LEAN algorithm):
  DelayAwareSlopeAlphaModel:
    1. load feature CSV (warmup)
    2. quarterly walk-forward: XGBoost fit(features → next-5d return slope)
       - slope target = OLS slope of rolling 5d returns
       - expanding window, train 252d, validate last 20%
    3. predict current cross-section slopes
    4. rank, emit Insight(UP, magnitude=slope) for Top-20
  → TopNEqualWeightPCM: equal weight Top-20, weekly rebalance, no-trade band
  → LEAN InfluxDbResultExporter → lean_metric (native)
```

## 5. Delay-Aware Graph Details

The lead-lag correlation for stocks i,j at lag τ:
```
C_ij(τ) = corr(r_i[t], r_j[t-τ])    over rolling window W=60d
```
- `lead_strength_i  = max_{j, τ∈1..5} C_ij(τ)` — stock i's strongest predictive link to a future stock
- `follow_strength_i = max_{j, τ∈1..5} corr(r_i[t], r_j[t-τ])` — stock i's strongest dependence on a past stock
- The "delay-aware" essence: τ (the lag) is searched explicitly, not assumed 0.

Computation: N≈800, N×N×5 ≈ 3.2M correlations per rebalance snapshot, computed offline in ~seconds.

## 6. Parameters (Sharpe-optimized)

| Parameter | Value | Note |
|---|---|---|
| Universe | CSI300 + CSI500 | ~800 stocks |
| Lag window | 1-5 trading days | weekly lead-lag |
| Prediction target | next-5d return slope (rolling OLS slope) | paper's "slope prediction" |
| Training window | 252 days | 1 year |
| Re-estimation | 63 days (quarterly) | walk-forward |
| Top-N holdings | 20 | equal-weight long |
| Rebalance | weekly (Friday) | T+1 cost control |
| No-trade band | 0.02 | turnover suppression |
| Max drawdown | 15% | risk gate |

## 7. A-Share Compliance Checklist

- [x] CSI300+CSI500 plain tickers, Market.SSE/SZSE inferred from first digit
- [x] T+1 settlement via `DelayedSettlementModel(1, ...)`
- [x] `AShareStockFeeModel` (commission + stamp + transfer)
- [x] `AShareStockFillModel` (price limits, 100-share lots)
- [x] `SetBenchmark(0)`
- [x] SHIBOR 1Y via `ChinaInterestRateProvider`
- [x] No `FineFundamental`
- [x] No shorting (long-only Top-N)

## 8. Files

```
Scripts/compute_delay_aware_graph.py             # offline lead-lag graph + features
Scripts/tests/test_compute_delay_aware_graph.py  # unit tests
Data/alternative/delay-aware-gnn-sde/csi300_features.csv  # warmup features
Data/alternative/delay-aware-gnn-sde/csi500_features.csv
Algorithm.Python/TopNEqualWeightPCM.py           # Top-N equal-weight PCM + lot rounding
Algorithm.Python/DelayAwareSlopeAlphaModel.py    # walk-forward XGBoost slope prediction
Algorithm.Python/DelayAwareGnnSdeAlgorithm.py    # main algorithm
Launcher/config/config-delay-aware-gnn-sde.json  # backtest config
Launcher/config/smoke/config-delay-aware-smoke.json
Results/soloquant/strategy-traces/delay-aware-gnn-sde-forecast/  # trace
```

## 9. Metrics (LEAN-native only)

All metrics from LEAN `InfluxDbResultExporter` (`lean_metric`): Sharpe, Sortino, Compounding Annual Return, Total Return, Max Drawdown, Win Rate, etc. No Python-computed metrics. GATE 3 requires Sharpe ≥ 0.

## 10. Gates

- GATE 0 paper_download: PASS (semantic scholar, WWW 2026)
- GATE 1 data_availability: tushare daily cross-sectional returns cover all required fields → PASS
- GATE 2 code_smoke: 1-day backtest no exception
- GATE 3 backtest_sharpe: Sharpe ≥ 0

## 11. Sharpe Optimization Notes

User priority: high Sharpe. Optimization levers if GATE 3 < 0:
- Top-N: 20 → 30 / 10 (concentration vs diversification)
- Lag window: 1-5 → 1-3 / 1-10
- Target horizon: 5d slope → 3d / 10d
- Add cross-sectional rank features (industry-relative momentum)
- Add turnover/illiquidity filters to avoid high-cost small caps
