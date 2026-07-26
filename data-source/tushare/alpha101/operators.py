# data-source/tushare/alpha101/operators.py
"""WorldQuant 101-alpha primitive operators on wide DataFrames.

All operators act on/return wide DataFrames (index=date, columns=ts_code) unless noted.
Non-integer window d is floored per the paper (ts_{O}(x, d) => floor(d)).
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd


def _floor(d) -> int:
    return int(math.floor(d))


def rank(x: pd.DataFrame) -> pd.DataFrame:
    return x.rank(axis=1, pct=True)


def delay(x: pd.DataFrame, d) -> pd.DataFrame:
    return x.shift(_floor(d))


def delta(x: pd.DataFrame, d) -> pd.DataFrame:
    return x - x.shift(_floor(d))


def correlation(x: pd.DataFrame, y: pd.DataFrame, d) -> pd.DataFrame:
    return x.rolling(_floor(d)).corr(y)


def covariance(x: pd.DataFrame, y: pd.DataFrame, d) -> pd.DataFrame:
    return x.rolling(_floor(d)).cov(y)


def scale(x: pd.DataFrame, a: float = 1.0) -> pd.DataFrame:
    s = x.abs().sum(axis=1).replace(0, np.nan)
    return x.div(s, axis=0) * a


def signedpower(x, a) -> pd.DataFrame:
    return x ** a


def decay_linear(x: pd.DataFrame, d) -> pd.DataFrame:
    w = _floor(d)
    weights = np.arange(w, 0, -1, dtype=float)
    weights = weights / weights.sum()
    return x.rolling(w).apply(lambda v: (v * weights).sum(), raw=True)


def ts_min(x: pd.DataFrame, d) -> pd.DataFrame:
    return x.rolling(_floor(d)).min()


def ts_max(x: pd.DataFrame, d) -> pd.DataFrame:
    return x.rolling(_floor(d)).max()


def ts_argmax(x: pd.DataFrame, d) -> pd.DataFrame:
    w = _floor(d)
    def _argmax(v):
        return (w - 1) - int(np.argmax(v))
    return x.rolling(w).apply(_argmax, raw=True)


def ts_argmin(x: pd.DataFrame, d) -> pd.DataFrame:
    w = _floor(d)
    def _argmin(v):
        return (w - 1) - int(np.argmin(v))
    return x.rolling(w).apply(_argmin, raw=True)


def ts_rank(x: pd.DataFrame, d) -> pd.DataFrame:
    return x.rolling(_floor(d)).rank(pct=True)


def sum(x: pd.DataFrame, d) -> pd.DataFrame:  # noqa: A001
    return x.rolling(_floor(d)).sum()


def product(x: pd.DataFrame, d) -> pd.DataFrame:
    return x.rolling(_floor(d)).apply(np.prod, raw=True)


def stddev(x: pd.DataFrame, d) -> pd.DataFrame:
    return x.rolling(_floor(d)).std(ddof=1)


def min(x: pd.DataFrame, d) -> pd.DataFrame:  # noqa: A001
    return ts_min(x, d)


def max(x: pd.DataFrame, d) -> pd.DataFrame:  # noqa: A001
    return ts_max(x, d)


def indneutralize(x: pd.DataFrame, groups: pd.Series) -> pd.DataFrame:
    """Cross-sectional within-group demean (simple arithmetic mean). groups: ts_code->label.

    A stock missing a group label -> NaN (excluded from group mean, value NaN).
    One-member group -> demean to 0 (not NaN).
    """
    out = x.copy()
    for idx in x.index:
        row = x.loc[idx]
        g = groups.reindex(row.index)
        valid = g.dropna()
        if valid.empty:
            out.loc[idx] = np.nan
            continue
        means = row.reindex(valid.index).groupby(valid).transform("mean")
        out.loc[idx] = row - means
    return out


def log(x: pd.DataFrame) -> pd.DataFrame:
    return x.where(x > 0, np.nan).apply(np.log)


def abs(x: pd.DataFrame) -> pd.DataFrame:  # noqa: A001
    return x.abs()


def sign(x: pd.DataFrame) -> pd.DataFrame:
    return np.sign(x)
