#!/usr/bin/env python3
"""Compute network centrality factors for Huaxi Securities (2021-03-14) strategy.

Implements the network-based asset pricing methodology from the Huaxi research
note: build a correlation network of CSI300 constituent daily returns (top 30%
edges by absolute correlation), then compute four node-centralities per stock
per month-end as alternative factors.

Output: Data/alternative/huaxi-network-centrality/factors.csv with columns
    trade_date, ts_code, degree, closeness, betweenness, eigenvector

This is a pure offline data-preparation step. The LEAN algorithm reads the CSV.
"""
import argparse
import glob
from pathlib import Path

import numpy as np
import pandas as pd
import networkx as nx

# tushare_data_v2 is the populated store (tushare_data/index_weight files are
# empty placeholders). Matches Scripts/compute_causal_factor_screen.py.
TUSHARE_ROOT = Path('/home/project/tushare-downloader/tushare_data_v2')
OUT_DIR = Path('Data/alternative/huaxi-network-centrality')

LOOKBACK = 252          # trading days in the correlation lookback window
EDGE_QUANTILE = 0.70    # keep top 30% edges (correlations >= 70th percentile)
CSI300_CODE = '000300.SH'


def load_csi300_constituents():
    """Return the most recent CSI300 constituent list from index_weight.

    Scans partitions from newest to oldest, skipping empty placeholders, and
    returns the first non-empty CSI300 list. CSI300 weight tables persist
    across rebalances so the latest snapshot is a good constituent universe.
    """
    base = TUSHARE_ROOT / 'index_weight'
    files = sorted(glob.glob(str(base / 'trade_date=*')))
    for f in reversed(files):
        df = pd.read_parquet(f)
        if 'index_code' not in df.columns or df.empty:
            continue
        sub = df[df['index_code'] == CSI300_CODE]
        if len(sub) == 0:
            continue
        col = 'con_code' if 'con_code' in sub.columns else 'ts_code'
        return sub[col].astype(str).unique().tolist()
    return []


def load_returns_panel(ts_codes, start_date, end_date):
    """Load daily pct_chg for each ts_code into a wide DataFrame [date x code]."""
    frames = {}
    for code in ts_codes:
        p = TUSHARE_ROOT / 'daily' / f'ts_code={code}' / 'data.parquet'
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        df['trade_date'] = df['trade_date'].astype(str).str.zfill(8)
        df = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]
        if 'pct_chg' in df.columns:
            frames[code] = df.set_index('trade_date')['pct_chg'].astype(float)
    panel = pd.DataFrame(frames).sort_index()
    return panel


def build_network(returns_window):
    """Pearson-correlation MST-style network keeping the top-30% strongest edges.

    A complete weighted graph is pruned: only edges whose Pearson correlation
    is at or above the EDGE_QUANTILE-th percentile of all pairwise correlations
    are retained. Edge weight is the raw correlation.
    """
    corr = returns_window.corr(method='pearson')
    n = len(corr)
    if n < 2:
        return nx.Graph()
    # threshold on the upper-triangle pairwise correlations
    upper = corr.values[np.triu_indices(n, k=1)]
    if len(upper) == 0:
        return nx.Graph()
    threshold = float(np.quantile(upper, EDGE_QUANTILE))
    G = nx.Graph()
    cols = list(corr.columns)
    G.add_nodes_from(cols)
    vals = corr.values
    for i in range(n):
        for j in range(i + 1, n):
            r = float(vals[i, j])
            if r >= threshold:
                G.add_edge(cols[i], cols[j], weight=r)
    return G


def compute_centrality(G):
    """Compute degree, closeness, betweenness, eigenvector centrality per node."""
    if len(G) == 0:
        return {}
    deg = nx.degree_centrality(G)
    clo = nx.closeness_centrality(G)
    bet = nx.betweenness_centrality(G)
    try:
        eig = nx.eigenvector_centrality(G, max_iter=500)
    except (nx.NetworkXException, nx.PowerIterationFailedConvergence):
        eig = {node: 0.0 for node in G.nodes}
    out = {}
    for node in G.nodes:
        out[node] = {
            'degree': float(deg.get(node, 0.0)),
            'closeness': float(clo.get(node, 0.0)),
            'betweenness': float(bet.get(node, 0.0)),
            'eigenvector': float(eig.get(node, 0.0)),
        }
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-date', default='20150101')
    parser.add_argument('--end-date', default='20251231')
    parser.add_argument('--output', default=None)
    args = parser.parse_args()

    out_path = Path(args.output) if args.output else OUT_DIR / 'factors.csv'
    out_path.parent.mkdir(parents=True, exist_ok=True)

    constituents = load_csi300_constituents()
    if not constituents:
        print('[huaxi] ERROR: no CSI300 constituents found')
        return
    print(f'[huaxi] {len(constituents)} constituents')

    returns = load_returns_panel(constituents, args.start_date, args.end_date)
    if returns.empty:
        print('[huaxi] ERROR: no returns data')
        return
    print(f'[huaxi] returns panel: {returns.shape}')

    # Build month-end schedule using last trading day of each calendar month
    date_index_dt = pd.to_datetime(returns.index, format='%Y%m%d')
    returns.index = date_index_dt
    month_ends = returns.resample('ME').last().index
    returns.index = returns.index.strftime('%Y%m%d')
    date_index = returns.index.tolist()

    all_rows = []
    for me_date in month_ends:
        date_str = me_date.strftime('%Y%m%d')
        if date_str not in date_index:
            # snap to the most recent trading day on/before the month end
            valid = [d for d in date_index if d <= date_str]
            if not valid:
                continue
            date_str = valid[-1]
        loc = date_index.index(date_str)
        if loc < LOOKBACK:
            continue
        window = returns.iloc[loc - LOOKBACK + 1: loc + 1].dropna(axis=1, how='any')
        if window.shape[1] < 50:
            continue
        G = build_network(window)
        cent = compute_centrality(G)
        for ts_code, metrics in cent.items():
            row = {'trade_date': date_str, 'ts_code': ts_code}
            row.update(metrics)
            all_rows.append(row)
        print(f'[huaxi] processed {date_str}: {len(cent)} stocks')

    df = pd.DataFrame(all_rows)
    if df.empty:
        print('[huaxi] WARNING: no factor rows produced')
        return
    df = df[['trade_date', 'ts_code', 'degree', 'closeness', 'betweenness', 'eigenvector']]
    df.to_csv(out_path, index=False)
    print(f'[huaxi] wrote {len(df)} rows to {out_path}')


if __name__ == '__main__':
    main()
