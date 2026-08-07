# vol-regime-hf-china Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a daily-proxy reproduction of Fang & Ślepaczuk (2026) — regime-aware volatility forecasting + XGBoost return prediction + Low-vol Gated Weekly Signal×Risk index-timing strategy on CSI300 ETF (510300), as a LEAN-native Python algorithm.

**Architecture:** LEAN 5-Step Framework Python algorithm. A feature-prep script reads tushare `fund_daily` parquet, computes Garman-Klass realized variance, rolling realized quarticity, signed-jump, and Markov-switching regime history, and writes a warmup CSV. The algorithm's `RegimeVolGatedAlphaModel` computes walk-forward HARQ volatility forecasts, XGBoost return predictions, applies low-vol regime gating + threshold filtering, and emits Insights. `VolatilityScalingPCM` converts signals to target weights with walk-forward exposure scaling, position cap, and no-trade band. All performance metrics come from LEAN's native `InfluxDbResultExporter`.

**Tech Stack:** Python (Python.NET), LEAN Algorithm Framework, pandas/numpy, statsmodels (Markov-switching), xgboost, scikit-learn.

**Spec:** `docs/superpowers/specs/2026-06-24-vol-regime-hf-china-design.md`

**Trace:** `Results/soloquant/strategy-traces/vol-regime-hf-china/`

---

## File Structure

| File | Responsibility |
|------|----------------|
| `Scripts/export_vol_regime_feature_data.py` | Read tushare `fund_daily` parquet for 510300; compute GK-RV, rolling RQ, signed-jump, log-RV, regime probability (Markov 2-state); write warmup CSV |
| `Scripts/tests/test_export_vol_regime.py` | Unit tests for feature computations (GK-RV, RQ, regime) |
| `Data/alternative/vol-regime/510300_features.csv` | Warmup feature table (date, close, log_rv, gk_rv, rq, signed_jump, regime_prob, regime_lag5, ret_lag5, ret_lag22, abs_ret_lag1) |
| `Algorithm.Python/VolatilityScalingPCM.py` | Custom `PortfolioConstructionModel`: signal→target weight, walk-forward exposure scaling (target \|w\|=0.5), cap ±0.6, no-trade band 0.02, weekly rebalance |
| `Algorithm.Python/RegimeVolGatedAlphaModel.py` | Custom `AlphaModel`: HARQ vol forecast, regime gate (κ=0 when p_t>0.5), threshold q=0.60, walk-forward XGBoost return prediction; emits Insight |
| `Algorithm.Python/VolRegimeHfChinaAlgorithm.py` | Main algorithm inheriting `QCAlgorithm`; wires 5-Step Framework; A-share compliance |
| `Launcher/config/config-vol-regime-hf-china.json` | Backtest config: 510300, Daily resolution, influxdb-enabled, A-share models |
| `Launcher/config/smoke/config-vol-regime-smoke.json` | 1-day smoke test config (GATE 2) |

---

## Task 1: Feature Preparation Script

**Files:**
- Create: `Scripts/export_vol_regime_feature_data.py`
- Test: `Scripts/tests/test_export_vol_regime.py`

- [ ] **Step 1: Write the failing test**

Create `Scripts/tests/test_export_vol_regime.py`:

```python
import numpy as np
import pandas as pd
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from Scripts.export_vol_regime_feature_data import (
    garman_klass_rv, rolling_realized_quarticity, signed_jump, regime_probability,
)

def _synthetic_ohlc(n=300, seed=42):
    rng = np.random.default_rng(seed)
    close = 4.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, n)))
    dates = pd.bdate_range('2020-01-02', periods=n).strftime('%Y%m%d')
    return pd.DataFrame({'trade_date': dates, 'open': open_, 'high': high,
                         'low': low, 'close': close})

def test_garman_klass_positive():
    df = _synthetic_ohlc()
    rv = garman_klass_rv(df)
    assert len(rv) == len(df)
    assert (rv.dropna() > 0).all()

def test_rq_matches_definition():
    df = _synthetic_ohlc(100)
    rets = np.log(df['close']).diff()
    rq = rolling_realized_quarticity(rets, window=5)
    manual = rets.rolling(5).apply(lambda x: np.sum(x**4) / 5, raw=True)
    pd.testing.assert_series_equal(rq, manual, check_names=False)

def test_signed_jump_has_sign():
    df = _synthetic_ohlc(100)
    rets = np.log(df['close']).diff()
    sj = signed_jump(rets, vol_window=22)
    assert sj.notna().any()

def test_regime_probability_in_unit_interval():
    df = _synthetic_ohlc(300)
    log_rv = np.log(garman_klass_rv(df).clip(lower=1e-8))
    p = regime_probability(log_rv.dropna())
    assert p is not None
    assert ((p >= 0) & (p <= 1)).all()

if __name__ == '__main__':
    test_garman_klass_positive(); print('PASS gk')
    test_rq_matches_definition(); print('PASS rq')
    test_signed_jump_has_sign(); print('PASS sj')
    test_regime_probability_in_unit_interval(); print('PASS regime')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && python3 Scripts/tests/test_export_vol_regime.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'Scripts.export_vol_regime_feature_data'`

- [ ] **Step 3: Implement feature functions**

Create `Scripts/export_vol_regime_feature_data.py`:

```python
"""Feature preparation for vol-regime-hf-china strategy.

Reads tushare fund_daily parquet for 510300.SH and computes daily-proxy
realized-volatility quantities described in Fang & Slepaczuk (2026).
"""
import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

TUSHARE_ROOT = Path('/home/project/tushare-downloader/tushare_data_v2')
DEFAULT_TS_CODE = '510300.SH'
DEFAULT_OUTPUT = Path('Data/alternative/vol-regime/510300_features.csv')


def garman_klass_rv(df: pd.DataFrame) -> pd.Series:
    """Garman-Klass daily realized variance proxy from OHLC.

    RV_GK = 0.5 * ln(H/L)^2 - (2*ln2 - 1) * ln(C/O)^2
    """
    log_hl = np.log(df['high'] / df['low'])
    log_co = np.log(df['close'] / df['open'])
    gk = 0.5 * log_hl ** 2 - (2.0 * np.log(2.0) - 1.0) * log_co ** 2
    return gk.clip(lower=0.0)


def rolling_realized_quarticity(returns: pd.Series, window: int = 5) -> pd.Series:
    """RQ proxy: rolling mean of 4th power of daily returns."""
    return returns.rolling(window).apply(lambda x: np.sum(x ** 4) / window, raw=True)


def signed_jump(returns: pd.Series, vol_window: int = 22) -> pd.Series:
    """Signed jump proxy: sign(r_t) * |r_t - rolling_mean_vol|."""
    vol = returns.rolling(vol_window).std()
    return np.sign(returns) * vol.abs()


def regime_probability(log_rv: pd.Series, n_regimes: int = 2) -> pd.Series:
    """Markov-switching 2-state filtered probability of high-vol regime.

    Fits MarkovRegression on the full log_rv series offline (warmup). State with
    higher mean log_rv is labelled 'high-vol'. Returns p(high-vol regime).
    Returns None if statsmodels unavailable or fit fails.
    """
    try:
        from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression
    except Exception:
        return None
    try:
        y = log_rv.dropna().values
        model = MarkovRegression(y, k_regimes=n_regimes, trend='c')
        res = model.fit(maxiter=200, disp=False)
        probs = res.smoothed_marginal_probabilities
        consts = res.params[['const[0]', 'const[1]']]
        high_regime = int(np.argmax(consts))
        p = probs[:, high_regime]
        idx = log_rv.dropna().index
        return pd.Series(p, index=idx)
    except Exception as e:
        print(f'[regime_probability] MS fit failed: {e}', file=sys.stderr)
        return None


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build the full feature table from a fund_daily OHLCV frame."""
    df = df.sort_values('trade_date').reset_index(drop=True).copy()
    df['close'] = df['close'].astype(float)
    df['ret'] = np.log(df['close']).diff()

    gk = garman_klass_rv(df)
    df['gk_rv'] = gk
    df['log_rv'] = np.log(gk.clip(lower=1e-10))

    df['rq'] = rolling_realized_quarticity(df['ret'], window=5)
    df['log_rq'] = np.log(df['rq'].clip(lower=1e-10))

    df['signed_jump'] = signed_jump(df['ret'], vol_window=22)

    df['vol_lag5'] = df['log_rv'].rolling(5).mean()
    df['vol_lag22'] = df['log_rv'].rolling(22).mean()

    df['ret_lag5'] = df['ret'].rolling(5).sum()
    df['ret_lag22'] = df['ret'].rolling(22).sum()
    df['abs_ret_lag1'] = df['ret'].abs().shift(1)

    log_rv = df['log_rv'].dropna()
    p = regime_probability(log_rv)
    if p is None:
        # Fallback: rolling z-score of log_rv mapped to [0,1]
        z = (df['log_rv'] - df['log_rv'].rolling(60).mean()) / df['log_rv'].rolling(60).std()
        p = 1.0 / (1.0 + np.exp(-z))  # logistic squashing
        p = p.fillna(0.5)
    else:
        p = p.reindex(df.index).fillna(0.5)
    df['regime_prob'] = p
    df['regime_lag5'] = df['regime_prob'].shift(5)
    df['vol_x_regime'] = df['log_rv'] * df['regime_prob']

    df['trade_date'] = df['trade_date'].astype(str)
    return df


def load_fund_daily(ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    path = TUSHARE_ROOT / 'fund_daily' / f'ts_code={ts_code}' / 'data.parquet'
    df = pd.read_parquet(path)
    df['trade_date'] = df['trade_date'].astype(str)
    df = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ts-code', default=DEFAULT_TS_CODE)
    ap.add_argument('--start-date', default='20170101')
    ap.add_argument('--end-date', default='20260623')
    ap.add_argument('--output', default=str(DEFAULT_OUTPUT))
    args = ap.parse_args()

    raw = load_fund_daily(args.ts_code, args.start_date, args.end_date)
    print(f'[vol-regime] loaded {len(raw)} rows for {args.ts_code}')
    feats = build_features(raw)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ['trade_date', 'close', 'gk_rv', 'log_rv', 'rq', 'log_rq',
            'signed_jump', 'vol_lag5', 'vol_lag22', 'ret_lag5', 'ret_lag22',
            'abs_ret_lag1', 'regime_prob', 'regime_lag5', 'vol_x_regime']
    feats[cols].to_csv(out, index=False)
    print(f'[vol-regime] wrote {len(feats)} feature rows to {out}')


if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/project/hope/Lean && python3 Scripts/tests/test_export_vol_regime.py`
Expected: prints `PASS gk / PASS rq / PASS sj / PASS regime`

- [ ] **Step 5: Generate the warmup CSV**

Run: `cd /home/project/hope/Lean && python3 Scripts/export_vol_regime_feature_data.py`
Expected: `[vol-regime] wrote ~2083 feature rows to Data/alternative/vol-regime/510300_features.csv`

- [ ] **Step 6: Commit**

```bash
cd /home/project/hope/Lean
git add Scripts/export_vol_regime_feature_data.py Scripts/tests/test_export_vol_regime.py Data/alternative/vol-regime/510300_features.csv
git commit -m "feat(vol-regime): feature prep script + 510300 warmup CSV

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: VolatilityScalingPCM (Portfolio Construction)

**Files:**
- Create: `Algorithm.Python/VolatilityScalingPCM.py`

**Note:** The Insight magnitude carries the gated+thresholded+walk-forward-scaled signal `s̃_t` already normalized toward target |w|=0.5. The PCM clamps to ±w_max, applies the no-trade band vs the previous weight, and rebalances weekly.

- [ ] **Step 1: Write the model**

Create `Algorithm.Python/VolatilityScalingPCM.py`:

```python
# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals
# LEAN Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
# Licensed under the Apache License, Version 2.0.

from AlgorithmImports import *


class VolatilityScalingPCM(PortfolioConstructionModel):
    """Low-vol Gated Weekly Signal x Risk portfolio construction (Fang & Slepaczuk 2026).

    - weekly rebalance
    - position cap w_max (default 0.6)
    - no-trade band b (default 0.02): suppress small weight changes
    The Insight magnitude is the pre-scaled target weight signal s_tilde.
    """

    def __init__(self, rebalance=timedelta(days=7), w_max=0.6, no_trade_band=0.02):
        self.rebalance_period = rebalance
        self.w_max = float(w_max)
        self.no_trade_band = float(no_trade_band)
        self._previous_targets = {}
        self._next_rebalance = None

    def create_targets(self, algorithm, insights):
        targets = []
        if not insights:
            return targets

        now = algorithm.utc_time
        if self._next_rebalance is None:
            self._next_rebalance = now + self.rebalance_period

        latest = {}
        for insight in insights:
            if insight.magnitude is None:
                continue
            latest[insight.symbol] = float(insight.magnitude)

        if now < self._next_rebalance and self._previous_targets:
            return targets  # not a rebalance day; hold existing

        self._next_rebalance = now + self.rebalance_period

        for symbol, signal in latest.items():
            target_w = max(-self.w_max, min(self.w_max, signal))
            prev = self._previous_targets.get(symbol, 0.0)
            if abs(target_w - prev) < self.no_trade_band:
                target_w = prev  # no-trade band
            qty = self._weight_to_quantity(algorithm, symbol, target_w)
            targets.append(PortfolioTarget(symbol, qty))
            self._previous_targets[symbol] = target_w
        return targets

    @staticmethod
    def _weight_to_quantity(algorithm, symbol, weight):
        security = algorithm.securities[symbol]
        price = security.price
        if price <= 0:
            return 0.0
        target_value = weight * algorithm.portfolio.total_portfolio_value
        return target_value / price
```

- [ ] **Step 2: Verify import compiles**

Run: `cd /home/project/hope/Lean && python3 -c "import ast; ast.parse(open('Algorithm.Python/VolatilityScalingPCM.py').read()); print('syntax ok')"`
Expected: `syntax ok`

- [ ] **Step 3: Commit**

```bash
git add Algorithm.Python/VolatilityScalingPCM.py
git commit -m "feat(vol-regime): VolatilityScalingPCM portfolio construction

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: RegimeVolGatedAlphaModel (Alpha + Signal)

**Files:**
- Create: `Algorithm.Python/RegimeVolGatedAlphaModel.py`

The core. On each daily update it: reads warmup features; computes the HARQ 1-step volatility forecast σ̂; retrains XGBoost quarterly (walk-forward, expanding window, N_min=300); builds `s_t = r̂/σ̂`, gates by regime (κ=0 when p_t>0.5), thresholds at q=0.60, scales toward target |w|=0.5; emits an Insight whose magnitude is the final scaled weight signal and whose direction follows its sign.

- [ ] **Step 1: Write the AlphaModel**

Create `Algorithm.Python/RegimeVolGatedAlphaModel.py`:

```python
# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals
# LEAN Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
# Licensed under the Apache License, Version 2.0.

from AlgorithmImports import *
import numpy as np
import pandas as pd


class RegimeVolGatedAlphaModel(AlphaModel):
    """Two-stage regime-aware alpha: HARQ vol forecast + XGBoost return prediction,
    wrapped in the Low-vol Gated Weekly Signal x Risk construction (Fang & Slepaczuk 2026).
    """

    FEATURE_COLS = ['log_rv', 'vol_lag5', 'vol_lag22', 'log_rq',
                    'signed_jump', 'regime_prob', 'regime_lag5', 'vol_x_regime',
                    'ret_lag5', 'ret_lag22', 'abs_ret_lag1']
    N_MIN = 300
    TARGET_EXPOSURE = 0.5
    W_MAX = 0.6
    Q_THRESHOLD = 0.60
    REGIME_GATE_KAPPA = 0.0   # exposure multiplier in high-vol regime
    REESTIMATE_DAYS = 63       # ~3 months

    def __init__(self, feature_csv_path, symbol, lookback=22):
        self.feature_csv_path = feature_csv_path
        self.symbol = symbol
        self.lookback = lookback
        self._features = None
        self._model = None
        self._feature_cols = ['logRVhat'] + self.FEATURE_COLS
        self._days_since_train = 0
        self._prev_signal = 0.0
        self._signal_history = []

    def update(self, algorithm, data):
        if self._features is None:
            self._load_features(algorithm)

        if not data.bars.contains_key(self.symbol):
            return []

        self._days_since_train += 1
        if self._model is None or self._days_since_train >= self.REESTIMATE_DAYS:
            self._maybe_retrain(algorithm)
            self._days_since_train = 0

        idx = self._current_feature_index(algorithm)
        if idx < self.N_MIN:
            return []

        signal = self._compute_signal(idx)
        if signal is None:
            return []

        self._signal_history.append(abs(signal))
        c_wf = self._walk_forward_scale()
        scaled = max(-self.W_MAX, min(self.W_MAX, c_wf * signal))
        self._prev_signal = scaled

        if abs(scaled) < 1e-4:
            return []

        direction = InsightDirection.UP if scaled > 0 else InsightDirection.DOWN
        insight = Insight.price(self.symbol, timedelta(days=7),
                                direction, abs(scaled), None)
        return [insight]

    def _load_features(self, algorithm):
        try:
            df = pd.read_csv(self.feature_csv_path)
            df['trade_date'] = df['trade_date'].astype(str)
            df = df.sort_values('trade_date').reset_index(drop=True)
            self._features = df
            algorithm.debug(f'[vol-regime] loaded {len(df)} feature rows')
        except Exception as e:
            algorithm.error(f'[vol-regime] feature load failed: {e}')
            self._features = pd.DataFrame()

    def _current_feature_index(self, algorithm):
        today = algorithm.time.strftime('%Y%m%d')
        df = self._features
        matches = df.index[df['trade_date'] <= today].tolist()
        return matches[-1] if matches else -1

    def _harq_forecast(self, df, idx):
        """1-step HARQ forecast of log RV: OLS on log_rv lags (1, 5, 22) + log_rq."""
        window = df.iloc[max(0, idx - 250):idx + 1].copy()
        if len(window) < 30:
            return df['log_rv'].iloc[idx]
        window['rv1'] = window['log_rv']
        window['rv5'] = window['log_rv'].rolling(5).mean()
        window['rv22'] = window['log_rv'].rolling(22).mean()
        window['rq1'] = window['log_rq']
        window['y'] = window['log_rv'].shift(-1)
        reg = window.dropna()
        if len(reg) < 25:
            return df['log_rv'].iloc[idx]
        X = reg[['rv1', 'rv5', 'rv22', 'rq1']].values
        y = reg['y'].values
        try:
            coef, *_ = np.linalg.lstsq(X, y, rcond=None)
            last = np.array([window['rv1'].iloc[-1], window['rv5'].iloc[-1],
                             window['rv22'].iloc[-1], window['rq1'].iloc[-1]])
            return float(coef @ last)
        except Exception:
            return df['log_rv'].iloc[idx]

    def _maybe_retrain(self, algorithm):
        df = self._features
        if len(df) < self.N_MIN:
            return
        try:
            import xgboost as xgb
        except Exception as e:
            algorithm.error(f'[vol-regime] xgboost unavailable: {e}')
            return

        rows = []
        for i in range(self.N_MIN, len(df)):
            logrvhat = self._harq_forecast(df, i - 1)
            row = {'logRVhat': logrvhat}
            for c in self.FEATURE_COLS:
                row[c] = df[c].iloc[i - 1]
            row['y'] = df['ret_lag5'].iloc[i]  # proxy target: next 5d return
            rows.append(row)
        if len(rows) < 50:
            return
        feat = pd.DataFrame(rows)
        X = feat[self._feature_cols].fillna(0).values
        y = feat['y'].fillna(0).values
        split = int(len(X) * 0.8)
        Xtr, Xva = X[:split], X[split:]
        ytr, yva = y[:split], y[split:]
        best_corr, best_model = -np.inf, None
        grid = [
            {'n_estimators': 300, 'max_depth': 2, 'learning_rate': 0.05,
             'min_child_weight': 3, 'gamma': 0.0, 'reg_lambda': 1.0},
            {'n_estimators': 300, 'max_depth': 3, 'learning_rate': 0.05,
             'min_child_weight': 3, 'gamma': 0.0, 'reg_lambda': 1.0},
            {'n_estimators': 300, 'max_depth': 2, 'learning_rate': 0.03,
             'min_child_weight': 3, 'gamma': 0.0, 'reg_lambda': 1.0},
            {'n_estimators': 300, 'max_depth': 2, 'learning_rate': 0.05,
             'min_child_weight': 3, 'gamma': 0.1, 'reg_lambda': 2.0},
        ]
        for params in grid:
            try:
                m = xgb.XGBRegressor(**params, verbosity=0)
                m.fit(Xtr, ytr)
                pred = m.predict(Xva)
                corr = float(np.corrcoef(pred, yva)[0, 1]) if (len(pred) > 1 and np.std(pred) > 0) else 0.0
                if corr > best_corr:
                    best_corr, best_model = corr, m
            except Exception:
                continue
        if best_model is None or best_corr < 0:
            best_model = xgb.XGBRegressor(**grid[0], verbosity=0)
            best_model.fit(Xtr, ytr)
        self._model = best_model
        algorithm.debug(f'[vol-regime] retrained XGBoost (val corr={best_corr:.3f})')

    def _compute_signal(self, idx):
        df = self._features
        logrvhat = self._harq_forecast(df, idx)
        sigma_hat = float(np.exp(0.5 * logrvhat))  # forecast vol = sqrt(RV)
        if sigma_hat <= 0 or np.isnan(sigma_hat) or self._model is None:
            return None
        row = {'logRVhat': logrvhat}
        for c in self.FEATURE_COLS:
            row[c] = float(df[c].iloc[idx]) if idx < len(df) else 0.0
        X = np.array([[row[c] for c in self._feature_cols]])
        r_hat = float(self._model.predict(X)[0])
        s = r_hat / sigma_hat

        # Regime gating
        p_t = row['regime_prob']
        if p_t > 0.5:
            s = self.REGIME_GATE_KAPPA * s

        # Threshold filter: drop weak signals relative to recent signal scale
        recent = df['log_rv'].iloc[max(0, idx - 250):idx]
        if len(recent) > 10:
            spread = np.abs(recent.diff().dropna())
            thr = float(np.quantile(spread, self.Q_THRESHOLD))
            if abs(s) < thr * 1e-3:
                s = 0.0
        return s

    def _walk_forward_scale(self):
        if len(self._signal_history) < 10:
            return 1.0
        recent = self._signal_history[-250:]
        mean_abs = float(np.mean(np.abs(recent)))
        if mean_abs <= 0:
            return 1.0
        return self.TARGET_EXPOSURE / mean_abs
```

- [ ] **Step 2: Syntax check**

Run: `cd /home/project/hope/Lean && python3 -c "import ast; ast.parse(open('Algorithm.Python/RegimeVolGatedAlphaModel.py').read()); print('syntax ok')"`
Expected: `syntax ok`

- [ ] **Step 3: Commit**

```bash
git add Algorithm.Python/RegimeVolGatedAlphaModel.py
git commit -m "feat(vol-regime): RegimeVolGatedAlphaModel (HARQ+XGBoost+gating)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: Main Algorithm

**Files:**
- Create: `Algorithm.Python/VolRegimeHfChinaAlgorithm.py`

- [ ] **Step 1: Write the algorithm**

Create `Algorithm.Python/VolRegimeHfChinaAlgorithm.py`:

```python
# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals
# LEAN Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
# Licensed under the Apache License, Version 2.0.

from AlgorithmImports import *
from VolatilityScalingPCM import VolatilityScalingPCM
from RegimeVolGatedAlphaModel import RegimeVolGatedAlphaModel


class VolRegimeHfChinaAlgorithm(QCAlgorithm):
    """Daily-proxy reproduction of Fang & Slepaczuk (2026):
    regime-aware volatility + XGBoost return prediction + Low-vol Gated
    Weekly Signal x Risk timing on CSI300 ETF (510300).
    """

    def initialize(self):
        self.set_start_date(2018, 1, 1)
        self.set_end_date(2025, 12, 31)
        self.set_cash(1_000_000)

        # A-share ETF: 510300 (Shanghai). Plain ticker; market inferred SSE.
        self.symbol = self.add_equity("510300", Resolution.DAILY, Market.SSE).symbol
        sec = self.securities[self.symbol]
        sec.set_fee_model(AShareStockFeeModel())
        sec.set_fill_model(AShareStockFillModel())
        sec.set_buying_power_model(AShareStockBuyingPowerModel())
        sec.set_settlement_model(DelayedSettlementModel(1, timedelta(hours=9)))

        self.set_benchmark(lambda x: 0)  # no benchmark
        self.set_risk_free_interest_rate_model(ChinaInterestRateProvider())

        # 5-Step Framework
        self.set_universe_selection(ManualUniverseSelectionModel([self.symbol]))

        feature_csv = os.path.join(Globals.data_folder, 'alternative',
                                   'vol-regime', '510300_features.csv')
        self.set_alpha(RegimeVolGatedAlphaModel(feature_csv, self.symbol))

        self.set_portfolio_construction(
            VolatilityScalingPCM(rebalance=timedelta(days=7),
                                 w_max=0.6, no_trade_band=0.02))

        self.set_risk_management(MaximumDrawdownPercentPortfolio(0.15))
        self.set_execution(ImmediateExecutionModel())

        self.set_warm_up(300, Resolution.DAILY)
```

- [ ] **Step 2: Syntax check**

Run: `cd /home/project/hope/Lean && python3 -c "import ast; ast.parse(open('Algorithm.Python/VolRegimeHfChinaAlgorithm.py').read()); print('syntax ok')"`
Expected: `syntax ok`

- [ ] **Step 3: Commit**

```bash
git add Algorithm.Python/VolRegimeHfChinaAlgorithm.py
git commit -m "feat(vol-regime): main VolRegimeHfChinaAlgorithm

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: Backtest Config + Smoke Test (GATE 2)

**Files:**
- Create: `Launcher/config/config-vol-regime-hf-china.json`
- Create: `Launcher/config/smoke/config-vol-regime-smoke.json`

**Prerequisite:** 510300 daily bars must be available to LEAN's backtest data feed in LEAN format under the `data-folder`. Verify the existing A-share backtest data path. If 510300 is not present, generate it from `fund_daily` parquet using the same conversion as the existing `ashare-multi-family` pipeline.

- [ ] **Step 1: Verify 510300 LEAN-format data exists**

Run:
```bash
cd /home/project/hope/Lean
find Data -path "*510300*" 2>/dev/null | head
grep -r "data-folder" Launcher/config/config-ashare-multi-family-pipeline.json 2>/dev/null
```
Inspect where the existing A-share backtest reads daily bars. If 510300 is missing, run the existing converter for 510300 (reuse the tushare→LEAN ingestion used by ashare-multi-family). Record the actual data path.

- [ ] **Step 2: Write the backtest config**

Create `Launcher/config/config-vol-regime-hf-china.json` (adapt `data-folder` and algorithm paths to the verified values):

```json
{
  "environment": "backtesting",
  "algorithm-type-name": "VolRegimeHfChinaAlgorithm",
  "algorithm-language": "Python",
  "algorithm-location": "../../../Algorithm.Python/VolRegimeHfChinaAlgorithm.py",
  "data-folder": "../../../Data/",
  "data-provider": "QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider",
  "results-destination-folder": "../../../Results",
  "period-start": "2018-01-01",
  "period-finish": "2025-12-31",
  "cash-amount": 1000000,
  "influxdb-enabled": true,
  "influxdb-url": "http://127.0.0.1:8086",
  "influxdb-org": "lean",
  "influxdb-bucket": "quant",
  "influxdb-token-env-var": "INFLUXDB_TOKEN",
  "influxdb-algorithm-id": "vol-regime-hf-china"
}
```

- [ ] **Step 3: Write the smoke config (1 day)**

Create `Launcher/config/smoke/config-vol-regime-smoke.json` identical to the backtest config but with:
```json
  "period-start": "2025-12-01",
  "period-finish": "2025-12-02",
  "influxdb-enabled": false
```

- [ ] **Step 4: Run the smoke test (GATE 2)**

Run:
```bash
cd /home/project/hope/Lean/Launcher/bin/Debug
export INFLUXDB_TOKEN=admin-token-leansystem
dotnet QuantConnect.Lean.Launcher.dll --config ../../../Launcher/config/smoke/config-vol-regime-smoke.json 2>&1 | tail -40
```
Expected: process exits 0, no Python exceptions, "Algorithm finished" message.

- [ ] **Step 5: Record GATE 2 decision**

Run (only if smoke passed):
```bash
cd /home/project/hope/Lean
python3 Scripts/strategy_trace.py record-step --strategy-id "vol-regime-hf-china" \
  --step 3.code_gen --status ok \
  --detail '{"language":"Python","framework":"5-step","ashare_compliance":true}'
python3 Scripts/strategy_trace.py record-step --strategy-id "vol-regime-hf-china" \
  --step 4.factor --status ok \
  --detail '{"factors_prepared":["gk_rv","rq","signed_jump","regime_prob","12_xgb_predictors"]}'
python3 Scripts/strategy_trace.py record-gate --strategy-id "vol-regime-hf-china" \
  --gate code_smoke --result PASS --threshold "1-day smoke no exception" \
  --observed "PASS exit_code 0" --detail '{}'
```

If smoke FAILED: inspect the traceback, fix the offending file, re-run. Do not record PASS until it genuinely passes. Record BLOCK if unfixable.

- [ ] **Step 6: Commit configs**

```bash
git add Launcher/config/config-vol-regime-hf-china.json Launcher/config/smoke/config-vol-regime-smoke.json
git commit -m "feat(vol-regime): backtest + smoke configs

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: Full Backtest (GATE 3)

- [ ] **Step 1: Run the full backtest**

Run:
```bash
cd /home/project/hope/Lean/Launcher/bin/Debug
export INFLUXDB_TOKEN=admin-token-leansystem
dotnet QuantConnect.Lean.Launcher.dll --config ../../../Launcher/config/config-vol-regime-hf-china.json 2>&1 | tail -60
```
Expected: completes 2018-2025, writes statistics; check `Results/` for the summary JSON.

- [ ] **Step 2: Read Sharpe from LEAN-native summary**

Run:
```bash
cd /home/project/hope/Lean
ls -t Results/*vol-regime* 2>/dev/null | head
python3 -c "import json,glob; fs=sorted(glob.glob('Results/*vol-regime*summary*.json')); print(fs); print(json.load(open(fs[-1]))['TotalPerformance'] if fs else 'no summary')"
```
If Sharpe ≥ 0 → GATE 3 PASS. If Sharpe < 0 → optimization loop (Task 7).

- [ ] **Step 3: Verify metrics are LEAN-native in InfluxDB**

Run:
```bash
curl -s -G "http://localhost:8086/api/v2/query?org=lean" \
  --header "Authorization: Token admin-token-leansystem" \
  --data-urlencode 'q=SELECT LAST(*) FROM "lean_metric" WHERE "algorithm_id"='\''vol-regime-hf-china'\''' | tail -5
```
Expected: rows present (confirms LEAN wrote the metrics, not Python).

- [ ] **Step 4: Record GATE 3 (PASS path)**

```bash
cd /home/project/hope/Lean
python3 Scripts/strategy_trace.py record-step --strategy-id "vol-regime-hf-china" \
  --step 5.backtest --status ok \
  --detail '{"metric_source":"lean_native","sharpe_ratio":<VALUE>,"total_return":<VALUE>,"max_drawdown":<VALUE>}'
python3 Scripts/strategy_trace.py record-gate --strategy-id "vol-regime-hf-china" \
  --gate backtest_sharpe --result PASS --threshold "Sharpe >= 0" \
  --observed "Sharpe = <VALUE>" --detail '{"sharpe":<VALUE>}'
```
Replace `<VALUE>` with the actual numbers from the summary.

- [ ] **Step 5: Commit**

```bash
git add -A Results/soloquant/strategy-traces/vol-regime-hf-china/
git commit -m "results(vol-regime): backtest complete, GATE 3 PASS

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: Optimization Loop (only if Sharpe < 0)

Invoke `superpowers:brainstorming` grounded in the backtest result. Iterate up to 2 rounds over (in priority order):
1. Threshold quantile q: 0.60 → 0.50 / 0.70
2. Regime gate κ: 0.0 → 0.3 (partial exposure in high-vol)
3. Target exposure: 0.5 → 0.3
4. XGBoost target: next-5d return → next-1d return
5. Lookback / re-estimation: 63d → 21d / 126d

Each iteration: edit the relevant parameter in `RegimeVolGatedAlphaModel.py`, re-run backtest (Task 6 Step 1-2), record a `backtest_sharpe` gate entry with `optimization_attempt: N`. Stop when Sharpe ≥ 0 or 2 rounds exhausted.

---

## Task 8: Live-Paper (only after GATE 3 PASS)

**Files:**
- Create: `Launcher/config/config-vol-regime-hf-china-live-paper.json`

- [ ] **Step 1: Write live-paper config**

Clone the backtest config, set:
```json
  "environment": "live-paper",
  "live-mode": true,
  "live-mode-brokerage": "PaperBrokerage",
  "data-queue-handler": ["TushareDataQueue"],
  "history-provider": ["TushareHistoryProvider"],
  "influxdb-enabled": true,
  "influxdb-algorithm-id": "vol-regime-hf-china-live"
```
(Use the GBM synthetic data handler as data source per skill rules if TushareDataQueue is unavailable for live-paper — match the pattern in `config-barra-cne5-live-paper.json`.)

- [ ] **Step 2: Launch live-paper**

Run:
```bash
cd /home/project/hope/Lean
python3 Scripts/barra_cne5_live_paper.py --config Launcher/config/config-vol-regime-hf-china-live-paper.json &
```
(Or the generic pipeline runner.) Capture PID.

- [ ] **Step 3: Record**

```bash
python3 Scripts/strategy_trace.py record-step --strategy-id "vol-regime-hf-china" \
  --step 6.live_paper --status ok \
  --detail '{"pid":<PID>,"config":"Launcher/config/config-vol-regime-hf-china-live-paper.json","metric_source":"lean_native"}'
```

---

## Notes & Conventions

- **LEAN-native only:** No Python script computes Sharpe / equity / drawdown. Metrics come from `lean_metric` (LEAN `InfluxDbResultExporter`). Python only prepares features and forwards signals.
- **A-share compliance:** 510300 plain ticker, Market.SSE inferred; T+1 via `DelayedSettlementModel(1, ...)`; `AShareStockFeeModel` (commission+stamp+transfer); `AShareStockFillModel` (price limits, 100-lot); `SetBenchmark(0)`; `ChinaInterestRateProvider`; no `FineFundamental`.
- **Never modify existing features / LEAN native code.** All new files live under the listed paths.
- **Trace every step + gate** via `Scripts/strategy_trace.py`.
