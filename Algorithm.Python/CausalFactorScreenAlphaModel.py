# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
# LEAN Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
# Licensed under the Apache License, Version 2.0.

from AlgorithmImports import *
import numpy as np
import pandas as pd


class CausalFactorScreenAlphaModel(AlphaModel):
    """Causal Factor Mirage alpha: composite score from geometric-sufficiency-screened
    factors (Lopez de Prado & Zoonekynd 2025). Emits Top-N UP insights ranked by score.
    """

    INSIGHT_HORIZON_DAYS = 5
    TOP_N = 20

    def __init__(self, feature_csv_path, top_n=20):
        self.feature_csv_path = feature_csv_path
        self.top_n = int(top_n)
        self._features = None

    def on_securities_changed(self, algorithm, changes):
        pass

    def update(self, algorithm, data):
        if self._features is None:
            self._load_features(algorithm)
        if self._features is None or len(self._features) == 0:
            return []

        current_date = algorithm.time.strftime('%Y%m%d')
        current = self._features[self._features['trade_date'] == current_date]
        if current.empty:
            recent = self._features[self._features['trade_date'] <= current_date]
            if recent.empty:
                return []
            latest = recent['trade_date'].max()
            current = self._features[self._features['trade_date'] == latest]

        feats = current.set_index('ts_code')['composite_score']
        out = {}
        for symbol in algorithm.active_securities.keys():
            code = self._symbol_to_ts_code(symbol)
            if code in feats.index:
                score = float(feats[code])
                if score > 0:
                    out[symbol] = score
        if not out:
            return []
        ranked = sorted(out.items(), key=lambda kv: kv[1], reverse=True)[:self.top_n]
        insights = []
        for symbol, score in ranked:
            insights.append(Insight.price(symbol, timedelta(days=self.INSIGHT_HORIZON_DAYS),
                                          InsightDirection.UP, float(score), None))
        return insights

    def _load_features(self, algorithm):
        try:
            df = pd.read_csv(self.feature_csv_path)
            df['trade_date'] = df['trade_date'].astype(str)
            self._features = df
            algorithm.debug(f'[causal] loaded {len(df)} feature rows')
        except Exception as e:
            algorithm.debug(f'[causal] skip feature CSV {self.feature_csv_path}: {e}')
            self._features = None

    @staticmethod
    def _symbol_to_ts_code(symbol):
        ticker = str(symbol.value) if hasattr(symbol, 'value') else str(symbol)
        t = ticker.replace('.SH', '').replace('.SZ', '').strip()
        if t.startswith('6'):
            return f'{t}.SH'
        return f'{t}.SZ'
