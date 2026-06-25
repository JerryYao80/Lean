# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
# LEAN Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
# Licensed under the Apache License, Version 2.0.

from AlgorithmImports import *
import numpy as np
import pandas as pd


class DelayAwareSlopeAlphaModel(AlphaModel):
    """DGNSDE core-idea approximation: walk-forward XGBoost on delay-aware
    lead/follow-strength + momentum features to predict next-5d return slope.
    Emits Top-N UP insights ranked by predicted slope.
    """

    FEATURE_COLS = ['lead_strength', 'follow_strength', 'ret_5d', 'ret_10d',
                    'ret_20d', 'vol_ratio']
    N_MIN = 60
    REESTIMATE_DAYS = 63
    INSIGHT_HORIZON_DAYS = 5
    TOP_N = 20

    def __init__(self, feature_csv_paths, top_n=20):
        self.feature_csv_paths = feature_csv_paths
        self.top_n = int(top_n)
        self._features = None
        self._model = None
        self._days_since_train = 0

    def update(self, algorithm, data):
        if self._features is None:
            self._load_features(algorithm)

        self._days_since_train += 1
        if self._model is None or self._days_since_train >= self.REESTIMATE_DAYS:
            self._maybe_retrain(algorithm)
            self._days_since_train = 0

        if self._model is None:
            return []

        predictions = self._predict_current(algorithm)
        if not predictions:
            return []

        ranked = sorted(predictions.items(), key=lambda kv: kv[1], reverse=True)
        top = ranked[:self.top_n]
        insights = []
        for symbol, slope in top:
            if slope <= 0:
                continue
            insights.append(Insight.price(symbol, timedelta(days=self.INSIGHT_HORIZON_DAYS),
                                          InsightDirection.UP, float(slope), None))
        return insights

    def on_securities_changed(self, algorithm, changes):
        """Required by AlphaModelPythonWrapper - no-op for this model."""
        pass

    def _load_features(self, algorithm):
        frames = []
        for p in self.feature_csv_paths:
            try:
                frames.append(pd.read_csv(p))
                algorithm.debug(f'[delay-aware] loaded {p}')
            except Exception as e:
                # Non-fatal: skip missing CSVs (e.g. csi500 not available).
                # Use debug, NOT error — algorithm.error() calls SetRunTimeError and halts.
                algorithm.debug(f'[delay-aware] skip feature CSV {p}: {e}')
        if frames:
            self._features = pd.concat(frames, ignore_index=True)
            self._features['trade_date'] = self._features['trade_date'].astype(str)
            algorithm.debug(f'[delay-aware] loaded {len(self._features)} feature rows')
        else:
            self._features = pd.DataFrame()

    def _maybe_retrain(self, algorithm):
        df = self._features
        if len(df) < self.N_MIN * 5:
            return
        try:
            import xgboost as xgb
        except Exception as e:
            algorithm.error(f'[delay-aware] xgboost unavailable: {e}')
            return
        sub = df.dropna(subset=self.FEATURE_COLS + ['slope_target'])
        if len(sub) < self.N_MIN:
            return
        X = sub[self.FEATURE_COLS].fillna(0).values
        y = sub['slope_target'].fillna(0).values
        split = int(len(X) * 0.8)
        Xtr, Xva = X[:split], X[split:]
        ytr, yva = y[:split], y[split:]
        params = {'n_estimators': 200, 'max_depth': 3, 'learning_rate': 0.05,
                  'min_child_weight': 5, 'reg_lambda': 1.0, 'verbosity': 0}
        m = xgb.XGBRegressor(**params)
        m.fit(Xtr, ytr)
        self._model = m
        corr = 0.0
        if len(Xva) > 1 and np.std(m.predict(Xva)) > 0:
            corr = float(np.corrcoef(m.predict(Xva), yva)[0, 1])
        algorithm.debug(f'[delay-aware] retrained XGBoost (val corr={corr:.3f})')

    def _predict_current(self, algorithm):
        """Predict slope for symbols at the current algorithm date."""
        df = self._features
        if len(df) == 0:
            return {}

        # Get current date from algorithm (format: YYYYMMDD)
        current_date = algorithm.time.strftime('%Y%m%d')

        # Filter features for current date
        current_feats = df[df['trade_date'] == current_date]
        if current_feats.empty:
            # Fallback: use most recent date <= current
            recent = df[df['trade_date'] <= current_date]
            if recent.empty:
                return {}
            latest_date = recent['trade_date'].max()
            current_feats = df[df['trade_date'] == latest_date]

        feats = current_feats.set_index('ts_code')
        out = {}
        for symbol in algorithm.active_securities.keys():
            code = self._symbol_to_ts_code(symbol)
            if code not in feats.index:
                continue
            row = feats.loc[code]
            X = np.array([[float(row[c]) for c in self.FEATURE_COLS]])
            try:
                slope = float(self._model.predict(X)[0])
            except Exception:
                continue
            out[symbol] = slope
        return out

    @staticmethod
    def _symbol_to_ts_code(symbol):
        ticker = str(symbol.value) if hasattr(symbol, 'value') else str(symbol)
        t = ticker.replace('.SH', '').replace('.SZ', '').strip()
        if t.startswith('6'):
            return f'{t}.SH'
        return f'{t}.SZ'
