"""Offline delay-aware lead-lag graph + features for DGNSDE approximation.

Reads tushare daily returns for CSI300+CSI500, computes an N x N x max_lag
lagged-correlation tensor, derives per-stock lead/follow strength + momentum,
and writes warmup CSVs consumed by the LEAN algorithm.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

TUSHARE_ROOT = Path('/home/project/tushare-downloader/tushare_data_v2')
OUT_DIR = Path('Data/alternative/delay-aware-gnn-sde')
INDEX_MAP = {'csi300': '000300.SH', 'csi500': '000905.SH'}


def lagged_correlation(returns, max_lag=5, window=60):
    """For each pair (i,j) and lag tau in 1..max_lag, compute correlation
    of r_i[t] with r_j[t-tau] over the last `window` days.
    returns: DataFrame (T x N). Returns array (N x N x max_lag).
    """
    codes = list(returns.columns)
    n = len(codes)
    R = returns.values
    C = np.full((n, n, max_lag), np.nan)
    for tau in range(1, max_lag + 1):
        a = R[tau:]
        b = R[:-tau]
        k = min(window, a.shape[0])
        a_w = a[-k:]
        b_w = b[-k:]
        a_std = a_w.std(axis=0)
        b_std = b_w.std(axis=0)
        for i in range(n):
            ai = (a_w[:, i] - a_w[:, i].mean())
            for j in range(n):
                bj = (b_w[:, j] - b_w[:, j].mean())
                denom = k * (a_std[i] * b_std[j])
                if denom > 1e-12:
                    C[i, j, tau - 1] = float(np.dot(ai, bj) / denom)
    return C


def lead_follow_strength(C):
    """Derive per-stock lead/follow strength from lagged-correlation tensor.
    C[i,j,tau] = corr(r_i[t], r_j[t-tau]); i LEADS j if high.
    lead_strength[i]  = max over (j,tau) of C[i,j,tau]
    follow_strength[i] = max over (j,tau) of C[j,i,tau]
    """
    n = C.shape[0]
    lead_strength = np.nanmax(np.nanmax(C, axis=1), axis=1)
    follow_strength = np.nanmax(np.nanmax(C, axis=0), axis=1)
    return lead_strength, follow_strength


def rolling_slope(series, window=5):
    """OLS slope over a rolling window."""
    x = np.arange(window)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()

    def _slope(arr):
        if np.isnan(arr).any():
            return np.nan
        y = arr
        y_mean = y.mean()
        return float(((x - x_mean) * (y - y_mean)).sum() / x_var)

    return series.rolling(window).apply(_slope, raw=True)


def build_features(returns, max_lag=5, window=60):
    """Build per-(date, ts_code) feature frame from a returns panel."""
    C = lagged_correlation(returns, max_lag=max_lag, window=window)
    lead_s, follow_s = lead_follow_strength(C)
    codes = list(returns.columns)
    rows = []
    last_date = returns.index[-1]
    close_proxy = (1 + returns).cumprod()
    for idx, code in enumerate(codes):
        r = returns[code]
        slope = rolling_slope(close_proxy[code], window=5).iloc[-1]
        rows.append({
            'trade_date': last_date,
            'ts_code': code,
            'lead_strength': float(lead_s[idx]) if not np.isnan(lead_s[idx]) else 0.0,
            'follow_strength': float(follow_s[idx]) if not np.isnan(follow_s[idx]) else 0.0,
            'ret_5d': float(r.iloc[-5:].sum()),
            'ret_10d': float(r.iloc[-10:].sum()),
            'ret_20d': float(r.iloc[-20:].sum()),
            'vol_ratio': float(r.iloc[-20:].std() / (r.iloc[-60:].std() + 1e-9)),
            'slope_target': float(slope) if not np.isnan(slope) else 0.0,
        })
    return pd.DataFrame(rows)


def load_index_constituents(index_code):
    """Load latest constituent ts_codes for an index."""
    import glob
    base = TUSHARE_ROOT / 'index_weight'
    files = sorted(glob.glob(str(base / 'trade_date=*')))
    for f in reversed(files):
        try:
            df = pd.read_parquet(f)
            if 'index_code' in df.columns:
                sub = df[df['index_code'] == index_code]
            else:
                sub = df
            if len(sub) > 0:
                col = 'con_code' if 'con_code' in sub.columns else 'ts_code'
                return sub[col].astype(str).tolist()
        except Exception:
            continue
    return []


def load_daily_returns(ts_codes, start_date, end_date):
    """Load daily log returns -> DataFrame (T x N)."""
    frames = {}
    for code in ts_codes:
        path = TUSHARE_ROOT / 'daily' / f'ts_code={code}' / 'data.parquet'
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df['trade_date'] = df['trade_date'].astype(str)
        df = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]
        df = df.sort_values('trade_date').set_index('trade_date')
        if 'pct_chg' in df.columns:
            frames[code] = df['pct_chg'].astype(float) / 100.0
        elif 'close' in df.columns:
            frames[code] = np.log(df['close'].astype(float)).diff()
    if not frames:
        return pd.DataFrame()
    panel = pd.DataFrame(frames)
    return panel.dropna(how='all').fillna(0.0)


def build_time_series_features(returns, max_lag=5, window=60, rebalance_every=21):
    """Build per-(date, ts_code) time-series feature frame for walk-forward training.

    - lead_strength/follow_strength: recomputed every `rebalance_every` days using
      the trailing `window` of returns (controls N*N*max_lag compute cost).
    - momentum/volatility/slope_target: computed daily.
    - slope_target = future 5-day return (training target; uses forward data, only
      consumed as label y, never as a feature at prediction time).

    returns: DataFrame (T x N), index = trade_date str, columns = ts_code.
    Returns DataFrame sorted by (ts_code, trade_date).
    """
    codes = list(returns.columns)
    n = len(codes)
    dates = list(returns.index)
    T = len(dates)

    # Precompute lead/follow strength per rebalance date (cache by rebalance bucket).
    lead_cache = {}
    follow_cache = {}
    for t in range(window + max_lag, T, rebalance_every):
        R_win = returns.iloc[t - window:t]
        try:
            C = lagged_correlation(R_win, max_lag=max_lag, window=window)
            ls, fs = lead_follow_strength(C)
            lead_cache[t] = np.nan_to_num(ls, nan=0.0)
            follow_cache[t] = np.nan_to_num(fs, nan=0.0)
        except Exception:
            lead_cache[t] = np.zeros(n)
            follow_cache[t] = np.zeros(n)

    rows = []
    for t in range(window + max_lag, T - 5):
        bucket = max((b for b in lead_cache if b <= t), default=None)
        if bucket is None:
            continue
        lead_s = lead_cache[bucket]
        follow_s = follow_cache[bucket]
        date = dates[t]
        for j, code in enumerate(codes):
            r = returns[code].iloc[:t + 1]
            if len(r) < 60:
                continue
            future_ret = returns[code].iloc[t + 1:t + 6].sum()
            rows.append({
                'trade_date': date,
                'ts_code': code,
                'lead_strength': float(lead_s[j]),
                'follow_strength': float(follow_s[j]),
                'ret_5d': float(r.iloc[-5:].sum()),
                'ret_10d': float(r.iloc[-10:].sum()),
                'ret_20d': float(r.iloc[-20:].sum()),
                'vol_ratio': float(r.iloc[-20:].std() / (r.iloc[-60:].std() + 1e-9)),
                'slope_target': float(future_ret),
            })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start-date', default='20200101')
    ap.add_argument('--end-date', default='20260623')
    ap.add_argument('--max-lag', type=int, default=5)
    ap.add_argument('--window', type=int, default=60)
    ap.add_argument('--rebalance-every', type=int, default=21)
    ap.add_argument('--universe', nargs='+', default=['csi300', 'csi500'])
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for uni in args.universe:
        idx = INDEX_MAP[uni]
        codes = load_index_constituents(idx)
        print(f'[delay-aware] {uni} ({idx}): {len(codes)} constituents')
        if not codes:
            continue
        R = load_daily_returns(codes, args.start_date, args.end_date)
        if R.shape[0] < 60:
            print(f'[delay-aware] {uni}: only {R.shape[0]} days, skip')
            continue
        if R.shape[1] > 300:
            vol = R.iloc[-60:].std().sort_values(ascending=False)
            R = R[vol.head(300).index]
        feats = build_time_series_features(
            R, max_lag=args.max_lag, window=args.window,
            rebalance_every=args.rebalance_every)
        out = OUT_DIR / f'{uni}_features.csv'
        feats.to_csv(out, index=False)
        print(f'[delay-aware] wrote {len(feats)} time-series rows -> {out}')


if __name__ == '__main__':
    main()
