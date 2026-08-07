# Delay-Aware Graph Neural SDE (DGNSDE) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a core-idea approximation of DGNSDE (WWW 2026) — delay-aware lead-lag correlation graph + walk-forward XGBoost slope prediction for CSI300+CSI500 stock selection, as a LEAN-native Python algorithm optimized for Sharpe.

**Architecture:** Offline Python script computes an N×N×5 lagged-correlation tensor over CSI300+CSI500 daily returns and derives per-stock lead/follow-strength + momentum features into warmup CSVs. The LEAN Python algorithm's `DelayAwareSlopeAlphaModel` does quarterly walk-forward XGBoost regression to predict each stock's next-5d return slope, ranks, and emits Top-20 UP insights. `TopNEqualWeightPCM` builds the equal-weight long portfolio with A-share 100-share lot rounding, weekly rebalance.

**Tech Stack:** Python (Python.NET), LEAN Algorithm Framework, pandas/numpy, xgboost, tushare parquet.

**Spec:** `docs/superpowers/specs/2026-06-25-delay-aware-gnn-sde-design.md`
**Trace:** `Results/soloquant/strategy-traces/delay-aware-gnn-sde-forecast/`

---

## File Structure

| File | Responsibility |
|------|----------------|
| `Scripts/compute_delay_aware_graph.py` | Offline: N×N×5 lagged correlations → lead/follow features + momentum |
| `Scripts/tests/test_compute_delay_aware_graph.py` | Unit tests for correlation, slope, feature table |
| `Data/alternative/delay-aware-gnn-sde/csi300_features.csv` | Warmup feature table |
| `Data/alternative/delay-aware-gnn-sde/csi500_features.csv` | Same for CSI500 |
| `Algorithm.Python/TopNEqualWeightPCM.py` | PCM: Top-N equal-weight long-only, weekly, lot rounding |
| `Algorithm.Python/DelayAwareSlopeAlphaModel.py` | AlphaModel: walk-forward XGBoost slope prediction |
| `Algorithm.Python/DelayAwareGnnSdeAlgorithm.py` | Main algorithm: 5-Step + A-share compliance |
| `Launcher/config/config-delay-aware-gnn-sde.json` | Backtest config |
| `Launcher/config/smoke/config-delay-aware-smoke.json` | Smoke config |

---

## Task 1: Feature Script + Tests

**Files:**
- Create: `Scripts/compute_delay_aware_graph.py`
- Create: `Scripts/tests/test_compute_delay_aware_graph.py`

- [ ] **Step 1: Write tests**

Create `Scripts/tests/test_compute_delay_aware_graph.py`:
```python
import numpy as np, pandas as pd, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from Scripts.compute_delay_aware_graph import lagged_correlation, lead_follow_strength, rolling_slope, build_features

def _panel(n=10, t=80):
    dates = pd.bdate_range('2022-01-03', periods=t).strftime('%Y%m%d')
    codes = [f'{600000+i:06d}.SH' for i in range(n)]
    return pd.DataFrame(np.random.randn(t, n)*0.01, index=dates, columns=codes)

def test_lagged_shape():
    C = lagged_correlation(_panel(), max_lag=5, window=60)
    assert C.shape == (10, 10, 5)

def test_lagged_range():
    C = lagged_correlation(_panel(), max_lag=5, window=60)
    assert -1.01 <= np.nanmin(C) <= 1.01

def test_lead_follow():
    C = lagged_correlation(_panel(), max_lag=5, window=60)
    l, f = lead_follow_strength(C)
    assert len(l) == 10 and len(f) == 10

def test_slope():
    s = rolling_slope(_panel().iloc[:,0], window=5)
    assert s.notna().any()

def test_features():
    df = build_features(_panel(t=120), max_lag=5, window=60)
    for c in ['trade_date','ts_code','lead_strength','follow_strength','ret_5d','ret_10d','ret_20d','vol_ratio','slope_target']:
        assert c in df.columns
```

- [ ] **Step 2: Verify test fails (missing module)**

Run: `cd /home/project/hope/Lean && python3 Scripts/tests/test_compute_delay_aware_graph.py`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement script**

Create `Scripts/compute_delay_aware_graph.py` (core functions: `lagged_correlation`, `lead_follow_strength`, `rolling_slope`, `build_features`, `main` reading tushare daily parquet → writes CSVs). Full code in spec self-containment.

- [ ] **Step 4: Verify tests pass**

Run: `python3 Scripts/tests/test_compute_delay_aware_graph.py`
Expected: all PASS

- [ ] **Step 5: Generate warmup CSVs**

Run: `python3 Scripts/compute_delay_aware_graph.py`

- [ ] **Step 6: Commit**

---

## Task 2: TopNEqualWeightPCM

**Files:**
- Create: `Algorithm.Python/TopNEqualWeightPCM.py`

- [ ] **Step 1: Write PCM**

Create `Algorithm.Python/TopNEqualWeightPCM.py` (Top-N equal-weight long-only, weekly rebalance, no-trade band, 100-share lot rounding).

- [ ] **Step 2: Syntax check**

- [ ] **Step 3: Commit**

---

## Task 3: DelayAwareSlopeAlphaModel

**Files:**
- Create: `Algorithm.Python/DelayAwareSlopeAlphaModel.py`

- [ ] **Step 1: Write AlphaModel**

Create `Algorithm.Python/DelayAwareSlopeAlphaModel.py` (load CSVs, walk-forward XGBoost on lead/follow + momentum features → slope prediction → Top-N UP insights).

- [ ] **Step 2: Syntax check**

- [ ] **Step 3: Commit**

---

## Task 4: Main Algorithm

**Files:**
- Create: `Algorithm.Python/DelayAwareGnnSdeAlgorithm.py`

- [ ] **Step 1: Write algorithm**

Create `Algorithm.Python/DelayAwareGnnSdeAlgorithm.py` (5-Step wiring + A-share compliance).

- [ ] **Step 2: Syntax check**

- [ ] **Step 3: Commit**

---

## Task 5: Configs + Smoke Test (GATE 2)

**Files:**
- Create: `Launcher/config/config-delay-aware-gnn-sde.json`
- Create: `Launcher/config/smoke/config-delay-aware-smoke.json`

- [ ] **Step 1: Write configs**

- [ ] **Step 2: Build launcher DLL if needed**

- [ ] **Step 3: Run smoke test**

If FAIL: fix issues, re-run. Do not record PASS until clean exit.

- [ ] **Step 4: Record GATE 2 PASS**

- [ ] **Step 5: Commit**

---

## Task 6: Full Backtest (GATE 3)

- [ ] **Step 1: Run backtest**

- [ ] **Step 2: Read Sharpe from LEAN statistics**

- [ ] **Step 3: Record GATE 3 (PASS if Sharpe ≥ 0)**

- [ ] **Step 4: Commit results**

---

## Task 7: Optimization (if Sharpe < 0)

Up to 2 rounds over: Top-N, lag window, target horizon, features, XGBoost hyperparams. Each iteration: edit → re-run → record gate with `optimization_attempt`.

---

## Task 8: Live-Paper (after GATE 3 PASS)

- [ ] **Step 1: Write live-paper config**

- [ ] **Step 2: Launch + record**

---

## Notes

- LEAN-native metrics only (InfluxDbResultExporter).
- A-share compliance: T+1, `AShareStockFeeModel`, `AShareStockFillModel`, `SetBenchmark(0)`, long-only.
- Trace every step via `Scripts/strategy_trace.py`.
- dotnet path: `/usr/local/dotnet/dotnet`.
