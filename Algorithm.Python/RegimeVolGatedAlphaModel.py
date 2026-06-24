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

        # A-share ETFs cannot be shorted: implement long-flat (paper's "Long-Flat"
        # variant). Only go long when the signal is positive; otherwise stay flat.
        if scaled <= 1e-4:
            return []

        insight = Insight.price(self.symbol, timedelta(days=7),
                                InsightDirection.UP, float(scaled), None)
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
