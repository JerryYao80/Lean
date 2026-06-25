# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
# LEAN Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
# Licensed under the Apache License, Version 2.0.

from AlgorithmImports import *
import pandas as pd


class NetworkCentralityAlphaModel(AlphaModel):
    """Network centrality alpha (Huaxi Securities 2021-03-14).

    Reads SCC/TCC/CC factors computed offline from PMFG networks. Uses CC
    (combined centrality) as the ranking signal: LOW CC (far from network
    center, more independent from market) -> UP insight. Emits Top-N insights.

    Reference: 华西证券《股票网络与网络中心度因子研究》(2021-03-14)
    """

    INSIGHT_HORIZON_DAYS = 22  # monthly rebalance horizon

    def __init__(self, feature_csv_path, top_n=60):
        self.feature_csv_path = feature_csv_path
        self.top_n = int(top_n)
        self._features = None
        self._last_emit_month = None  # gate: emit only once per factor month

    def on_securities_changed(self, algorithm, changes):
        pass

    def update(self, algorithm, data):
        if self._features is None:
            self._load_features(algorithm)
        if self._features is None or len(self._features) == 0:
            return []

        # Find this month's factor rows (CC = combined centrality).
        # Use most recent trade_date on or before algorithm.time.
        current_date = algorithm.time.strftime('%Y%m%d')
        current = self._features[self._features['trade_date'] == current_date]
        if current.empty:
            recent = self._features[self._features['trade_date'] <= current_date]
            if recent.empty:
                return []
            latest = recent['trade_date'].max()
            current = self._features[self._features['trade_date'] == latest]
        else:
            latest = current_date

        # Monthly rebalance gate: only emit insights when a NEW factor month
        # becomes available. Without this, update() runs every trading day and
        # the PCM churns the book daily (91k orders over 10y observed before).
        if latest == self._last_emit_month:
            return []
        self._last_emit_month = latest

        if 'cc' not in current.columns:
            return []

        cc_series = current.set_index('ts_code')['cc']

        # Map active securities to their CC factor value.
        # LOW CC -> UP (long low-centrality = independent stocks).
        out = {}
        for symbol in algorithm.active_securities.keys():
            ts_code = self._symbol_to_ts_code(symbol)
            if ts_code in cc_series.index:
                val = cc_series[ts_code]
                if pd.notna(val):
                    out[symbol] = float(val)

        if not out:
            return []

        # Sort ascending (low CC first), take top N -> these get UP insights.
        ranked = sorted(out.items(), key=lambda kv: kv[1])[:self.top_n]

        insights = []
        for symbol, score in ranked:
            insights.append(Insight.price(
                symbol,
                timedelta(days=self.INSIGHT_HORIZON_DAYS),
                InsightDirection.UP,
                1.0,
                None
            ))
        return insights

    def _load_features(self, algorithm):
        try:
            df = pd.read_csv(self.feature_csv_path)
            df['trade_date'] = df['trade_date'].astype(str)
            self._features = df
            algorithm.debug(f'[huaxi-centrality] loaded {len(df)} factor rows')
        except Exception as e:
            algorithm.debug(f'[huaxi-centrality] skip feature CSV {self.feature_csv_path}: {e}')
            self._features = None

    @staticmethod
    def _symbol_to_ts_code(symbol):
        """Convert LEAN Symbol to tushare ts_code (e.g. '600519' -> '600519.SH')."""
        ticker = str(symbol.value) if hasattr(symbol, 'value') else str(symbol)
        t = ticker.replace('.SH', '').replace('.SZ', '').strip()
        if t.startswith('6'):
            return f'{t}.SH'
        return f'{t}.SZ'
