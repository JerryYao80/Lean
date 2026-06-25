#!/usr/bin/env python3
"""Offline factor screening protocol for causal-factor-mirage strategy.

Implements López de Prado & Zoonekynd (2025) geometric sufficiency conditions:
  - Directional alignment (DA)
  - Ranking preservation (IC, Spearman)
  - Calibration (R^2)
Walk-forward: factors passing thresholds get IC-weighted composite score.
"""
import argparse, glob
from pathlib import Path
import numpy as np
import pandas as pd

TUSHARE_ROOT = Path('/home/project/tushare-downloader/tushare_data_v2')
OUT_DIR = Path('Data/alternative/causal-factor-mirage')

DA_THRESHOLD = 0.48
IC_THRESHOLD = 0.005
CAL_THRESHOLD = 0.001
FORWARD_DAYS = 5


def load_index_constituents():
    base = TUSHARE_ROOT / 'index_weight'
    files = sorted(glob.glob(str(base / 'trade_date=*')))
    for f in files[::10]:
        df = pd.read_parquet(f)
        if 'index_code' in df.columns and (df['index_code'] == '000300.SH').any():
            sub = df[df['index_code'] == '000300.SH']
            col = 'con_code' if 'con_code' in sub.columns else 'ts_code'
            return sub[col].astype(str).unique().tolist()
    return []


def load_panel(ts_codes, dataset, field, start_date, end_date):
    frames = {}
    for code in ts_codes:
        p = TUSHARE_ROOT / dataset / f'ts_code={code}' / 'data.parquet'
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        df['trade_date'] = df['trade_date'].astype(str).str.zfill(8)
        df = df[(df['trade_date'] >= start_date) & (df['trade_date'] <= end_date)]
        if field in df.columns:
            frames[code] = df.set_index('trade_date')[field].astype(float)
    return pd.DataFrame(frames)


def build_factor_matrix(codes, start_date, end_date):
    panels = {}
    panels['pct_chg'] = load_panel(codes, 'daily', 'pct_chg', start_date, end_date)
    panels['turnover_rate'] = load_panel(codes, 'daily_basic', 'turnover_rate', start_date, end_date)
    panels['pe'] = load_panel(codes, 'daily_basic', 'pe', start_date, end_date)
    panels['pb'] = load_panel(codes, 'daily_basic', 'pb', start_date, end_date)
    panels['circ_mv'] = load_panel(codes, 'daily_basic', 'circ_mv', start_date, end_date)
    factors = {}
    factors['momentum_20d'] = panels['pct_chg'].rolling(20).sum()
    factors['reversal_5d'] = -panels['pct_chg'].rolling(5).sum()
    factors['turnover'] = panels['turnover_rate']
    factors['pe_inv'] = -panels['pe']
    factors['pb_inv'] = -panels['pb']
    factors['circ_mv_inv'] = -panels['circ_mv']
    factors['volatility_20d'] = -panels['pct_chg'].rolling(20).std()
    return factors, panels['pct_chg']


def cross_sectional_rank(df):
    return df.rank(axis=1, pct=True) - 0.5


def screen_and_composite(factors, fwd_ret, rebalance_every=21, window=60):
    dates = fwd_ret.index
    T = len(dates)
    rows = []
    codes = fwd_ret.columns
    for t in range(window, T - FORWARD_DAYS, rebalance_every):
        date = dates[t]
        fwd_window = fwd_ret.iloc[t - window:t].shift(-FORWARD_DAYS)
        composite = pd.Series(0.0, index=codes)
        n_pass = 0
        for fname, fpanel in factors.items():
            fwin = fpanel.iloc[t - window:t]
            fr = fwd_window.iloc[-(window - FORWARD_DAYS):]
            fwin_a = fwin.iloc[-(window - FORWARD_DAYS):]
            fr_rank = cross_sectional_rank(fr)
            f_rank = cross_sectional_rank(fwin_a)
            # align on common dates/columns
            common_idx = fr_rank.index.intersection(f_rank.index)
            common_cols = fr_rank.columns.intersection(f_rank.columns)
            fr_a = fr_rank.loc[common_idx, common_cols]
            f_a = f_rank.loc[common_idx, common_cols]
            mask = fr_a.notna() & f_a.notna()
            if mask.sum().sum() < 100:
                continue
            da = (np.sign(f_a[mask]) == np.sign(fr_a[mask])).mean().mean()
            ics = []
            for d in common_idx:
                a = f_a.loc[d].dropna()
                b = fr_a.loc[d].reindex(a.index).dropna()
                if len(b) > 20:
                    ics.append(a.corr(b, method='spearman'))
            ic = float(np.nanmean(ics)) if ics else 0.0
            fv = f_a.values[mask.values]
            rv = fr_a.values[mask.values]
            if len(fv) > 50 and np.std(fv) > 0 and np.std(rv) > 0:
                cal = float(np.corrcoef(fv, rv)[0, 1] ** 2)
            else:
                cal = 0.0
            if da > DA_THRESHOLD and ic > IC_THRESHOLD and cal > CAL_THRESHOLD:
                cur_rank = cross_sectional_rank(fpanel.iloc[[t]]).iloc[0]
                composite = composite.add(cur_rank.fillna(0) * ic)
                n_pass += 1
        if n_pass == 0:
            continue
        for code in codes:
            rows.append({'trade_date': date, 'ts_code': code,
                         'composite_score': float(composite.get(code, 0.0)),
                         'n_passing_factors': n_pass})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start-date', default='20200101')
    ap.add_argument('--end-date', default='20260623')
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    codes = load_index_constituents()
    print(f'[causal] CSI300 constituents: {len(codes)}')
    if not codes:
        return
    if len(codes) > 300:
        codes = codes[:300]
    factors, fwd_ret = build_factor_matrix(codes, args.start_date, args.end_date)
    print(f'[causal] factor panels built, dates={fwd_ret.shape[0]}')
    composite = screen_and_composite(factors, fwd_ret)
    out = OUT_DIR / 'csi300_screened_factors.csv'
    composite.to_csv(out, index=False)
    print(f'[causal] wrote {len(composite)} rows -> {out}')


if __name__ == '__main__':
    main()
