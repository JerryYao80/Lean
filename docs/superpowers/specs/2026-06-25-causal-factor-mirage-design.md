# Causal Factor Mirage — A-Share Factor Screening Protocol

**Source paper:** *Correcting the Factor Mirage: A Research Protocol for Causal Factor Investing* (López de Prado & Zoonekynd, JPM 2025, DOI 10.3905/jpm.2025.1.794)
**Strategy ID:** `causal-factor-mirage`
**Date:** 2026-06-25

## 1. Paper Core Idea

Portfolio efficiency does NOT require causal identification. Efficiency is governed by **geometric sufficiency conditions** on predictive signals:
1. **Directional alignment** — signal sign matches expected-return sign
2. **Ranking preservation** — signal preserves cross-sectional return ordering (IC)
3. **Calibration** — signal magnitude scales linearly with return magnitude (R²)

## 2. Approximation Mapping

| Paper concept | A-share implementation |
|---|---|
| Geometric sufficiency | Factor screening metrics (DA, IC, R²) on tushare factors |
| Factor pool | momentum_20d, reversal_5d, pe_inv, pb_inv, turnover, circ_mv_inv, volatility_20d |
| Walk-forward evaluation | Rolling 60d window, monthly re-screen |
| Factor combination | IC-weighted composite of passing factors |
| Portfolio | Top-20 equal-weight long |

## 3. Architecture (LEAN 5-Step, Python)

| Step | Model |
|------|-------|
| Universe | ManualUniverseSelectionModel (CSI300) |
| Alpha | CausalFactorScreenAlphaModel : AlphaModel |
| Portfolio | TopNEqualWeightPCM (reuse, Top-20 weekly) |
| Risk | MaximumDrawdownPercentPortfolio(0.15) |
| Execution | ImmediateExecutionModel |

A-share: T+1, AShareStock models, SetBenchmark(0), ChinaInterestRateProvider, CNY.

## 4. Factor Screening Protocol

For each factor f, monthly: DA>0.52, IC>0.02, Cal>0.005 → passes; weight = IC.
