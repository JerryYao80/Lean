"""Feature preparation for vol-regime-hf-china strategy.

Reads tushare fund_daily parquet for 510300.SH and computes daily-proxy
realized-volatility quantities described in Fang & Slepaczuk (2026).
"""
import argparse
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
        # res.params is a numpy array; model.param_names gives the labels.
        names = model.param_names
        params = pd.Series(res.params, index=names)
        consts = params[[f'const[{i}]' for i in range(n_regimes)]]
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
