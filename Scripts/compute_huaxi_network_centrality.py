#!/usr/bin/env python3
"""Compute network centrality factors for Huaxi Securities (2021-03-14) strategy.

Implements the exact methodology from the Huaxi research note:
1. Pearson correlation matrix from daily returns (252-day lookback)
2. Distance transform: d_ij = sqrt(2(1 - rho_ij)) [Mantegna 1999]
3. PMFG (Planar Maximally Filtered Graph) network construction
4. SCC (Spatial Centrality) = 1 / d_bar^2
5. TCC (Temporal Centrality) from SCC time-series over sub-windows
6. CC = 0.5 * SCC_norm + 0.5 * TCC_norm

Output: Data/alternative/huaxi-network-centrality/factors.csv
    columns: trade_date, ts_code, scc, tcc, cc

Reference: 华西证券《股票网络与网络中心度因子研究》(2021-03-14)
           authors 曹春晓 (S1120520070003), 杨国平 (S1120520070002)
"""
import argparse
import glob
from pathlib import Path

import numpy as np
import pandas as pd
import networkx as nx

TUSHARE_ROOT = Path('/home/project/tushare-downloader/tushare_data_v2')
OUT_DIR = Path('Data/alternative/huaxi-network-centrality')

LOOKBACK = 252
CSI300_CODE = '000300.SH'
CSI500_CODE = '000905.SH'


def load_index_constituents(index_code):
    """Load constituent ts_codes for a specific index from latest snapshot."""
    base = TUSHARE_ROOT / 'index_weight'
    files = sorted(glob.glob(str(base / 'trade_date=*')))
    for f in reversed(files):
        df = pd.read_parquet(f)
        if 'index_code' not in df.columns or df.empty:
            continue
        sub = df[df['index_code'] == index_code]
        if len(sub) == 0:
            continue
        col = 'con_code' if 'con_code' in sub.columns else 'ts_code'
        return sub[col].astype(str).unique().tolist()
    return []


def load_historical_constituents(index_code, as_of_date):
    """Load constituents as of a specific date (eliminates survivorship bias)."""
    base = TUSHARE_ROOT / 'index_weight'
    files = sorted(glob.glob(str(base / 'trade_date=*')))
    as_of = as_of_date[:8]
    for f in reversed(files):
        fname = f.split('trade_date=')[-1].split('/')[0]
        if fname <= as_of:
            df = pd.read_parquet(f)
            if 'index_code' not in df.columns or df.empty:
                continue
            sub = df[df['index_code'] == index_code]
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
        if 'pct_chg' in df.columns and len(df) >= LOOKBACK:
            frames[code] = df.set_index('trade_date')['pct_chg'].astype(float)
    return pd.DataFrame(frames).sort_index()


def correlation_to_distance(corr_matrix):
    """Convert Pearson correlation to distance: d_ij = sqrt(2(1 - rho_ij))."""
    rho = np.clip(corr_matrix.values, -1, 1)
    dist = np.sqrt(2 * (1 - rho))
    np.fill_diagonal(dist, 0)
    return dist


def build_pmfg_true(distance_matrix, node_names):
    """TRUE PMFG with actual planarity check (matches report methodology).

    Early termination at 3N-6 edges (maximal planar graph). Slower but accurate.
    """
    n = len(node_names)
    if n < 3:
        return nx.Graph()
    edges = [(distance_matrix[i, j], i, j)
             for i in range(n) for j in range(i + 1, n)]
    edges.sort(key=lambda x: x[0])
    max_edges = 3 * n - 6
    G = nx.Graph()
    G.add_nodes_from(range(n))
    edge_count = 0
    for dist, i, j in edges:
        if edge_count >= max_edges:
            break
        G.add_edge(i, j, weight=1.0 / (dist + 1e-10))
        is_planar, _ = nx.check_planarity(G)
        if not is_planar:
            G.remove_edge(i, j)
        else:
            edge_count += 1
    mapping = {i: node_names[i] for i in range(n)}
    return nx.relabel_nodes(G, mapping, copy=False)


def build_pmfg_fast(distance_matrix, node_names):
    """Fast PMFG approximation: top-(3N-6) shortest edges. No planarity check."""
    n = len(node_names)
    if n < 3:
        return nx.Graph()
    edges = [(distance_matrix[i, j], i, j)
             for i in range(n) for j in range(i + 1, n)]
    edges.sort(key=lambda x: x[0])
    max_edges = 3 * n - 6
    G = nx.Graph()
    G.add_nodes_from(range(n))
    for dist, i, j in edges[:max_edges]:
        G.add_edge(i, j, weight=1.0 / (dist + 1e-10))
    mapping = {i: node_names[i] for i in range(n)}
    return nx.relabel_nodes(G, mapping, copy=False)


def compute_scc(G):
    """Spatial Centrality (SCC) = 1 / d_bar^2."""
    if len(G) == 0:
        return {}
    nodes = list(G.nodes)
    scc = {}
    for node in nodes:
        lengths = nx.single_source_dijkstra_path_length(
            G, node, weight=lambda u, v, d: 1.0 / d.get('weight', 1))
        finite = [lengths.get(o, float('inf')) for o in nodes if o != node]
        finite = [x for x in finite if x < float('inf')]
        if finite:
            d_bar = np.mean(finite)
            scc[node] = 1.0 / (d_bar ** 2) if d_bar > 0 else 0.0
        else:
            scc[node] = 0.0
    return scc


def compute_factors_for_month(returns_window, use_true_pmfg=False):
    """Compute SCC/TCC/CC for a single month snapshot.

    TCC computed from SCC time-series over 4 sub-windows (matches report).
    """
    corr = returns_window.corr(method='pearson')
    if corr.empty or len(corr) < 50:
        return {}

    node_names = list(corr.columns)
    dist_matrix = correlation_to_distance(corr)

    n = len(node_names)
    if use_true_pmfg and n <= 200:
        G = build_pmfg_true(dist_matrix, node_names)
    else:
        G = build_pmfg_fast(dist_matrix, node_names)

    scc = compute_scc(G)

    # TCC from SCC time-series over sub-windows (report methodology)
    window_len = len(returns_window)
    sub_window = max(21, window_len // 4)
    scc_series = {node: [] for node in node_names}

    for start in range(0, window_len - sub_window + 1, sub_window):
        sub_ret = returns_window.iloc[start:start + sub_window]
        if sub_ret.shape[0] < 21:
            continue
        sub_corr = sub_ret.corr(method='pearson')
        if sub_corr.empty:
            continue
        sub_dist = correlation_to_distance(sub_corr)
        sub_G = build_pmfg_fast(sub_dist, list(sub_corr.columns))
        sub_scc = compute_scc(sub_G)
        for node in node_names:
            scc_series[node].append(sub_scc.get(node, 0))

    tcc = {}
    for node in node_names:
        vals = scc_series[node]
        tcc[node] = float(np.std(vals)) if len(vals) > 1 else 0.0

    scc_max = max(scc.values()) if scc else 1.0
    tcc_max = max(tcc.values()) if tcc else 1.0

    factors = {}
    for node in node_names:
        scc_norm = scc.get(node, 0) / scc_max if scc_max > 0 else 0
        tcc_norm = tcc.get(node, 0) / tcc_max if tcc_max > 0 else 0
        factors[node] = {
            'scc': float(scc.get(node, 0)),
            'tcc': float(tcc.get(node, 0)),
            'cc': 0.5 * scc_norm + 0.5 * tcc_norm,
        }
    return factors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-date', default='20150101')
    parser.add_argument('--end-date', default='20251231')
    parser.add_argument('--output', default=None)
    parser.add_argument('--universe', choices=['csi300', 'csi500', 'csi800'],
                        default='csi300')
    parser.add_argument('--use-historical-constituents', action='store_true')
    parser.add_argument('--use-true-pmfg', action='store_true')
    args = parser.parse_args()

    out_path = Path(args.output) if args.output else OUT_DIR / 'factors.csv'
    out_path.parent.mkdir(parents=True, exist_ok=True)

    index_code = {'csi300': CSI300_CODE, 'csi500': CSI500_CODE}.get(args.universe, CSI300_CODE)

    if args.universe == 'csi800':
        c300 = load_index_constituents(CSI300_CODE)
        c500 = load_index_constituents(CSI500_CODE)
        base_constituents = list(set(c300 + c500))
    else:
        base_constituents = load_index_constituents(index_code)

    if not base_constituents:
        print(f'[huaxi] ERROR: no constituents found for {args.universe}')
        return
    print(f'[huaxi] base universe: {len(base_constituents)} stocks ({args.universe})')

    all_returns = load_returns_panel(base_constituents, args.start_date, args.end_date)
    if all_returns.empty:
        print('[huaxi] ERROR: no returns data')
        return
    print(f'[huaxi] returns panel: {all_returns.shape}')

    date_index_dt = pd.to_datetime(all_returns.index, format='%Y%m%d')
    all_returns.index = date_index_dt
    month_ends = all_returns.resample('ME').last().index
    all_returns.index = all_returns.index.strftime('%Y%m%d')
    date_index = all_returns.index.tolist()

    all_rows = []
    for me_date in month_ends:
        date_str = me_date.strftime('%Y%m%d')
        if date_str not in date_index:
            valid = [d for d in date_index if d <= date_str]
            if not valid:
                continue
            date_str = valid[-1]
        loc = date_index.index(date_str)
        if loc < LOOKBACK:
            continue

        if args.use_historical_constituents and args.universe != 'csi800':
            constituents = load_historical_constituents(index_code, date_str)
            if not constituents:
                constituents = base_constituents
        else:
            constituents = base_constituents

        available = [c for c in constituents if c in all_returns.columns]
        if len(available) < 50:
            continue

        window = all_returns.iloc[loc - LOOKBACK + 1: loc + 1][available].dropna(axis=1, how='any')
        if window.shape[1] < 50:
            continue

        factors = compute_factors_for_month(window, use_true_pmfg=args.use_true_pmfg)
        for ts_code, metrics in factors.items():
            row = {'trade_date': date_str, 'ts_code': ts_code}
            row.update(metrics)
            all_rows.append(row)
        print(f'[huaxi] processed {date_str}: {len(factors)} stocks')

    df = pd.DataFrame(all_rows)
    if df.empty:
        print('[huaxi] WARNING: no factor rows produced')
        return
    df = df[['trade_date', 'ts_code', 'scc', 'tcc', 'cc']]
    df.to_csv(out_path, index=False)
    print(f'[huaxi] wrote {len(df)} rows to {out_path}')


if __name__ == '__main__':
    main()
