# data-source/tushare/alpha101/formulas.py
"""WorldQuant 101 Formulaic Alphas — faithful translation of docs/101.md Appendix A.

Each alpha_NNN(p: Alpha101Panel) -> wide DataFrame. Non-integer windows floored.
IndClass levels pinned in INDCLASS_LEVELS (per-occurrence, verified vs docs/101.md).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from alpha101.operators import (
    rank, delay, delta, correlation, covariance, scale, signedpower,
    decay_linear, ts_min, ts_max, ts_argmax, ts_argmin, ts_rank,
    sum as ts_sum, product as ts_product, stddev, indneutralize, log,
    abs as oabs, sign,
)

# Per-occurrence IndClass level, verified against docs/101.md Appendix A.
INDCLASS_LEVELS: dict[int, list[str]] = {
    48: ["subindustry"], 58: ["sector"], 59: ["industry"], 63: ["industry"],
    67: ["sector", "subindustry"], 69: ["industry"], 70: ["industry"], 76: ["sector"],
    79: ["sector"], 80: ["industry"], 82: ["sector"], 87: ["industry"], 89: ["industry"],
    90: ["subindustry"], 91: ["industry"], 93: ["industry"], 97: ["industry"],
    100: ["subindustry", "subindustry"],
}


def _ind(p, level: str) -> pd.Series:
    col = {"sector": "l1", "industry": "l2", "subindustry": "l3"}[level]
    if p.industry is None or p.industry.empty:
        return pd.Series(dtype=object)
    return p.industry.set_index("ts_code")[col]


def _adv(p, n):
    return p.adv[int(np.floor(n))]


def _where(cond, x, y, ref):
    """np.where wrapper that preserves DataFrame shape (np.where returns ndarray)."""
    return pd.DataFrame(np.where(cond, x, y), index=ref.index, columns=ref.columns)


def alpha_001(p):
    base = signedpower(_where(p.returns < 0, stddev(p.returns, 20), p.close, p.close), 2.0)
    return rank(ts_argmax(base, 5)) - 0.5

def alpha_002(p):
    return -1 * correlation(rank(delta(np.log(p.volume), 2)), rank((p.close - p.open) / p.open), 6)

def alpha_003(p):
    return -1 * correlation(rank(p.open), rank(p.volume), 10)

def alpha_004(p):
    return -1 * ts_rank(rank(p.low), 9)

def alpha_005(p):
    return rank(p.open - (ts_sum(p.vwap, 10) / 10)) * (-1 * oabs(rank(p.close - p.vwap)))

def alpha_006(p):
    return -1 * correlation(p.open, p.volume, 10)

def alpha_007(p):
    cond = _adv(p, 20) < p.volume
    return _where(cond, (-1 * ts_rank(oabs(delta(p.close, 7)), 60)) * sign(delta(p.close, 7)), -1.0, p.close)

def alpha_008(p):
    inner = ts_sum(p.open, 5) * ts_sum(p.returns, 5)
    return -1 * rank(inner - delay(inner, 10))

def alpha_009(p):
    dc1 = delta(p.close, 1)
    cond1 = 0 < ts_min(dc1, 5)
    cond2 = ts_max(dc1, 5) < 0
    return _where(cond1, dc1, _where(cond2, dc1, -1 * dc1, p.close), p.close)

def alpha_010(p):
    dc1 = delta(p.close, 1)
    cond1 = 0 < ts_min(dc1, 4)
    cond2 = ts_max(dc1, 4) < 0
    return rank(_where(cond1, dc1, _where(cond2, dc1, -1 * dc1, p.close), p.close))

def alpha_011(p):
    vc = p.vwap - p.close
    return (rank(ts_max(vc, 3)) + rank(ts_min(vc, 3))) * rank(delta(p.volume, 3))

def alpha_012(p):
    return sign(delta(p.volume, 1)) * (-1 * delta(p.close, 1))

def alpha_013(p):
    return -1 * rank(covariance(rank(p.close), rank(p.volume), 5))

def alpha_014(p):
    return (-1 * rank(delta(p.returns, 3))) * correlation(p.open, p.volume, 10)

def alpha_015(p):
    return -1 * ts_sum(rank(correlation(rank(p.high), rank(p.volume), 3)), 3)

def alpha_016(p):
    return -1 * rank(covariance(rank(p.high), rank(p.volume), 5))

def alpha_017(p):
    return ((-1 * rank(ts_rank(p.close, 10))) * rank(delta(delta(p.close, 1), 1))) * rank(ts_rank(p.volume / _adv(p, 20), 5))

def alpha_018(p):
    return -1 * rank((stddev(oabs(p.close - p.open), 5) + (p.close - p.open)) + correlation(p.close, p.open, 10))

def alpha_019(p):
    return ((-1 * sign((p.close - delay(p.close, 7)) + delta(p.close, 7))) * (1 + rank(1 + ts_sum(p.returns, 250))))

def alpha_020(p):
    return ((-1 * rank(p.open - delay(p.high, 1))) * rank(p.open - delay(p.close, 1))) * rank(p.open - delay(p.low, 1))

def alpha_021(p):
    s8 = ts_sum(p.close, 8) / 8
    s2 = ts_sum(p.close, 2) / 2
    sd8 = stddev(p.close, 8)
    return _where((s8 + sd8) < s2, -1.0, _where(s2 < (s8 - sd8), 1.0, _where(1 <= (p.volume / _adv(p, 20)), 1.0, -1.0, p.close), p.close), p.close)

def alpha_022(p):
    return -1 * (delta(correlation(p.high, p.volume, 5), 5) * rank(stddev(p.close, 20)))

def alpha_023(p):
    return _where((ts_sum(p.high, 20) / 20) < p.high, -1 * delta(p.high, 2), 0.0, p.high)

def alpha_024(p):
    s100 = ts_sum(p.close, 100) / 100
    ratio = delta(s100, 100) / delay(p.close, 100)
    return _where((ratio < 0.05) | (ratio == 0.05), -1 * (p.close - ts_min(p.close, 100)), -1 * delta(p.close, 3), p.close)

def alpha_025(p):
    return rank(((-1 * p.returns) * _adv(p, 20) * p.vwap) * (p.high - p.close))

def alpha_026(p):
    return -1 * ts_max(correlation(ts_rank(p.volume, 5), ts_rank(p.high, 5), 5), 3)

def alpha_027(p):
    return _where(0.5 < rank(ts_sum(correlation(rank(p.volume), rank(p.vwap), 6), 2) / 2.0), -1.0, 1.0, p.close)

def alpha_028(p):
    return scale((correlation(_adv(p, 20), p.low, 5) + ((p.high + p.low) / 2)) - p.close)

def alpha_029(p):
    inner = -1 * rank(delta((p.close - 1), 5))
    x1 = rank(rank(scale(log(ts_sum(ts_min(rank(rank(inner)), 2), 1)))))
    return ts_min(x1, 5) + ts_rank(delay(-1 * p.returns, 6), 5)

def alpha_030(p):
    s1 = sign(p.close - delay(p.close, 1)) + sign(delay(p.close, 1) - delay(p.close, 2)) + sign(delay(p.close, 2) - delay(p.close, 3))
    return ((1.0 - rank(s1)) * ts_sum(p.volume, 5)) / ts_sum(p.volume, 20)

def alpha_031(p):
    return (rank(rank(rank(decay_linear(-1 * rank(rank(delta(p.close, 10))), 10)))) + rank(-1 * delta(p.close, 3)) + sign(scale(correlation(_adv(p, 20), p.low, 12))))

def alpha_032(p):
    return scale((ts_sum(p.close, 7) / 7) - p.close) + (20 * scale(correlation(p.vwap, delay(p.close, 5), 230)))

def alpha_033(p):
    return rank(-1 * (1 - (p.open / p.close)) ** 1)

def alpha_034(p):
    return rank((1 - rank(stddev(p.returns, 2) / stddev(p.returns, 5))) + (1 - rank(delta(p.close, 1))))

def alpha_035(p):
    return (ts_rank(p.volume, 32) * (1 - ts_rank((p.close + p.high) - p.low, 16))) * (1 - ts_rank(p.returns, 32))

def alpha_036(p):
    return ((((2.21 * rank(correlation((p.close - p.open), delay(p.volume, 1), 15))) + (0.7 * rank(p.open - p.close))) + (0.73 * rank(ts_rank(delay(-1 * p.returns, 6), 5)))) + (rank(oabs(correlation(p.vwap, _adv(p, 20), 6))) + (0.6 * rank(((ts_sum(p.close, 200) / 200) - p.open) * (p.close - p.open)))))

def alpha_037(p):
    return rank(correlation(delay(p.open - p.close, 1), p.close, 200)) + rank(p.open - p.close)

def alpha_038(p):
    return (-1 * rank(ts_rank(p.close, 10))) * rank(p.close / p.open)

def alpha_039(p):
    return ((-1 * rank(delta(p.close, 7) * (1 - rank(decay_linear(p.volume / _adv(p, 20), 9))))) * (1 + rank(ts_sum(p.returns, 250))))

def alpha_040(p):
    return (-1 * rank(stddev(p.high, 10))) * correlation(p.high, p.volume, 10)

def alpha_041(p):
    return ((p.high * p.low) ** 0.5) - p.vwap

def alpha_042(p):
    return rank(p.vwap - p.close) / rank(p.vwap + p.close)

def alpha_043(p):
    return ts_rank(p.volume / _adv(p, 20), 20) * ts_rank(-1 * delta(p.close, 7), 8)

def alpha_044(p):
    return -1 * correlation(p.high, rank(p.volume), 5)

def alpha_045(p):
    return -1 * ((rank(ts_sum(delay(p.close, 5), 20) / 20) * correlation(p.close, p.volume, 2)) * rank(correlation(ts_sum(p.close, 5), ts_sum(p.close, 20), 2)))

def alpha_046(p):
    x = ((delay(p.close, 20) - delay(p.close, 10)) / 10) - ((delay(p.close, 10) - p.close) / 10)
    return _where(0.25 < x, -1.0, _where(x < 0, 1.0, (-1 * 1) * (p.close - delay(p.close, 1)), p.close), p.close)

def alpha_047(p):
    return ((((rank(1 / p.close) * p.volume) / _adv(p, 20)) * ((p.high * rank(p.high - p.close)) / (ts_sum(p.high, 5) / 5))) - rank(p.vwap - delay(p.vwap, 5)))

def alpha_048(p):
    inner = correlation(delta(p.close, 1), delta(delay(p.close, 1), 1), 250) * delta(p.close, 1) / p.close
    return indneutralize(inner, _ind(p, "subindustry")) / ts_sum((delta(p.close, 1) / delay(p.close, 1)) ** 2, 250)

def alpha_049(p):
    x = ((delay(p.close, 20) - delay(p.close, 10)) / 10) - ((delay(p.close, 10) - p.close) / 10)
    return _where(x < (-1 * 0.1), 1.0, (-1 * 1) * (p.close - delay(p.close, 1)), p.close)

def alpha_050(p):
    return -1 * ts_max(rank(correlation(rank(p.volume), rank(p.vwap), 5)), 5)

def alpha_051(p):
    x = ((delay(p.close, 20) - delay(p.close, 10)) / 10) - ((delay(p.close, 10) - p.close) / 10)
    return _where(x < (-1 * 0.05), 1.0, (-1 * 1) * (p.close - delay(p.close, 1)), p.close)

def alpha_052(p):
    return (((-1 * ts_min(p.low, 5)) + delay(ts_min(p.low, 5), 5)) * rank((ts_sum(p.returns, 240) - ts_sum(p.returns, 20)) / 220)) * ts_rank(p.volume, 5)

def alpha_053(p):
    inner = ((p.close - p.low) - (p.high - p.close)) / (p.close - p.low)
    return -1 * delta(inner, 9)

def alpha_054(p):
    return (-1 * ((p.low - p.close) * (p.open ** 5))) / ((p.low - p.high) * (p.close ** 5))

def alpha_055(p):
    inner = (p.close - ts_min(p.low, 12)) / (ts_max(p.high, 12) - ts_min(p.low, 12) + 1e-12)
    return -1 * correlation(rank(inner), rank(p.volume), 6)

def alpha_056(p):
    return 0 - (1 * (rank(ts_sum(p.returns, 10) / ts_sum(ts_sum(p.returns, 2), 3)) * rank(p.returns * p.cap)))

def alpha_057(p):
    return 0 - (1 * ((p.close - p.vwap) / decay_linear(rank(ts_argmax(p.close, 30)), 2)))

def alpha_058(p):
    return -1 * ts_rank(decay_linear(correlation(indneutralize(p.vwap, _ind(p, "sector")), p.volume, int(np.floor(3.92795))), int(np.floor(7.89291))), int(np.floor(5.50322)))

def alpha_059(p):
    blend = p.vwap * 0.728317 + p.vwap * (1 - 0.728317)
    return -1 * ts_rank(decay_linear(correlation(indneutralize(blend, _ind(p, "industry")), p.volume, int(np.floor(4.25197))), int(np.floor(16.2289))), int(np.floor(8.19648)))

def alpha_060(p):
    inner = (((p.close - p.low) - (p.high - p.close)) / (p.high - p.low + 1e-12)) * p.volume
    return 0 - (1 * ((2 * scale(rank(inner))) - scale(rank(ts_argmax(p.close, 10)))))

def alpha_061(p):
    return (rank(p.vwap - ts_min(p.vwap, int(np.floor(16.1219)))) < rank(correlation(p.vwap, _adv(p, 180), int(np.floor(17.9282))))).astype(float)

def alpha_062(p):
    left = rank(correlation(p.vwap, ts_sum(_adv(p, 20), int(np.floor(22.4101))), int(np.floor(9.91009))))
    right = ((rank(p.open) + rank(p.open)) < (rank((p.high + p.low) / 2) + rank(p.high))).astype(float)
    return (left < right).astype(float) * -1

def alpha_063(p):
    a = rank(decay_linear(delta(indneutralize(p.close, _ind(p, "industry")), int(np.floor(2.25164))), int(np.floor(8.22237))))
    b = rank(decay_linear(correlation(p.vwap * 0.318108 + p.open * (1 - 0.318108), ts_sum(_adv(p, 180), int(np.floor(37.2467))), int(np.floor(13.557))), int(np.floor(12.2883))))
    return (a - b) * -1

def alpha_064(p):
    blend = p.open * 0.178404 + p.low * (1 - 0.178404)
    left = rank(correlation(ts_sum(blend, int(np.floor(12.7054))), ts_sum(_adv(p, 120), int(np.floor(12.7054))), int(np.floor(16.6208))))
    right = rank(delta(((p.high + p.low) / 2 * 0.178404 + p.vwap * (1 - 0.178404)), int(np.floor(3.69741))))
    return (left < right).astype(float) * -1

def alpha_065(p):
    blend = p.open * 0.00817205 + p.vwap * (1 - 0.00817205)
    left = rank(correlation(blend, ts_sum(_adv(p, 60), int(np.floor(8.6911))), int(np.floor(6.40374))))
    right = rank(p.open - ts_min(p.open, int(np.floor(13.635))))
    return (left < right).astype(float) * -1

def alpha_066(p):
    a = rank(decay_linear(delta(p.vwap, int(np.floor(3.51013))), int(np.floor(7.23052))))
    inner = ((p.low * 0.96633 + p.low * (1 - 0.96633)) - p.vwap) / (p.open - (p.high + p.low) / 2 + 1e-12)
    b = ts_rank(decay_linear(inner, int(np.floor(11.4157))), int(np.floor(6.72611)))
    return (a + b) * -1

def alpha_067(p):
    a = rank(p.high - ts_min(p.high, int(np.floor(2.14593))))
    b = rank(correlation(indneutralize(p.vwap, _ind(p, "sector")), indneutralize(_adv(p, 20), _ind(p, "subindustry")), int(np.floor(6.02936))))
    return (a ** b) * -1

def alpha_068(p):
    left = ts_rank(correlation(rank(p.high), rank(_adv(p, 15)), int(np.floor(8.91644))), int(np.floor(13.9333)))
    right = rank(delta(p.close * 0.518371 + p.low * (1 - 0.518371), int(np.floor(1.06157))))
    return (left < right).astype(float) * -1

def alpha_069(p):
    a = rank(ts_max(delta(indneutralize(p.vwap, _ind(p, "industry")), int(np.floor(2.72412))), int(np.floor(4.79344))))
    b = ts_rank(correlation(p.close * 0.490655 + p.vwap * (1 - 0.490655), _adv(p, 20), int(np.floor(4.92416))), int(np.floor(9.0615)))
    return (a ** b) * -1

def alpha_070(p):
    a = rank(delta(p.vwap, int(np.floor(1.29456))))
    b = ts_rank(correlation(indneutralize(p.close, _ind(p, "industry")), _adv(p, 50), int(np.floor(17.8256))), int(np.floor(17.9171)))
    return (a ** b) * -1

def alpha_071(p):
    a = ts_rank(decay_linear(correlation(ts_rank(p.close, int(np.floor(3.43976))), ts_rank(_adv(p, 180), int(np.floor(12.0647))), int(np.floor(18.0175))), int(np.floor(4.20501))), int(np.floor(15.6948)))
    b = ts_rank(decay_linear((rank((p.low + p.open) - (p.vwap + p.vwap)) ** 2), int(np.floor(16.4662))), int(np.floor(4.4388)))
    return np.maximum(a, b)

def alpha_072(p):
    a = rank(decay_linear(correlation((p.high + p.low) / 2, _adv(p, 40), int(np.floor(8.93345))), int(np.floor(10.1519))))
    b = rank(decay_linear(correlation(ts_rank(p.vwap, int(np.floor(3.72469))), ts_rank(p.volume, int(np.floor(18.5188))), int(np.floor(6.86671))), int(np.floor(2.95011))))
    return a / b

def alpha_073(p):
    blend = p.open * 0.147155 + p.low * (1 - 0.147155)
    a = rank(decay_linear(delta(p.vwap, int(np.floor(4.72775))), int(np.floor(2.91864))))
    inner = (delta(blend, int(np.floor(2.03608))) / blend) * -1
    b = ts_rank(decay_linear(inner, int(np.floor(3.33829))), int(np.floor(16.7411)))
    return np.maximum(a, b) * -1

def alpha_074(p):
    left = rank(correlation(p.close, ts_sum(_adv(p, 30), int(np.floor(37.4843))), int(np.floor(15.1365))))
    right = rank(correlation(rank(p.high * 0.0261661 + p.vwap * (1 - 0.0261661)), rank(p.volume), int(np.floor(11.4791))))
    return (left < right).astype(float) * -1

def alpha_075(p):
    left = rank(correlation(p.vwap, p.volume, int(np.floor(4.24304))))
    right = rank(correlation(rank(p.low), rank(_adv(p, 50)), int(np.floor(12.4413))))
    return (left < right).astype(float)

def alpha_076(p):
    a = rank(decay_linear(delta(p.vwap, int(np.floor(1.24383))), int(np.floor(11.8259))))
    b = ts_rank(decay_linear(ts_rank(correlation(indneutralize(p.low, _ind(p, "sector")), _adv(p, 81), int(np.floor(8.14941))), int(np.floor(19.569))), int(np.floor(17.1543))), int(np.floor(19.383)))
    return np.maximum(a, b) * -1

def alpha_077(p):
    a = rank(decay_linear((((p.high + p.low) / 2 + p.high) - (p.vwap + p.high)), int(np.floor(20.0451))))
    b = rank(decay_linear(correlation((p.high + p.low) / 2, _adv(p, 40), int(np.floor(3.1614))), int(np.floor(5.64125))))
    return np.minimum(a, b)

def alpha_078(p):
    blend = p.low * 0.352233 + p.vwap * (1 - 0.352233)
    a = rank(correlation(ts_sum(blend, int(np.floor(19.7428))), ts_sum(_adv(p, 40), int(np.floor(19.7428))), int(np.floor(6.83313))))
    b = rank(correlation(rank(p.vwap), rank(p.volume), int(np.floor(5.77492))))
    return a ** b

def alpha_079(p):
    blend = p.close * 0.60733 + p.open * (1 - 0.60733)
    left = rank(delta(indneutralize(blend, _ind(p, "sector")), int(np.floor(1.23438))))
    right = rank(correlation(ts_rank(p.vwap, int(np.floor(3.60973))), ts_rank(_adv(p, 150), int(np.floor(9.18637))), int(np.floor(14.6644))))
    return (left < right).astype(float)

def alpha_080(p):
    blend = p.open * 0.868128 + p.high * (1 - 0.868128)
    a = rank(sign(delta(indneutralize(blend, _ind(p, "industry")), int(np.floor(4.04545)))))
    b = ts_rank(correlation(p.high, _adv(p, 10), int(np.floor(5.11456))), int(np.floor(5.53756)))
    return (a ** b) * -1

def alpha_081(p):
    inner = rank(correlation(p.vwap, ts_sum(_adv(p, 10), int(np.floor(49.6054))), int(np.floor(8.47743))) ** 4)
    left = rank(log(ts_product(rank(inner), int(np.floor(14.9655)))))
    right = rank(correlation(rank(p.vwap), rank(p.volume), int(np.floor(5.07914))))
    return (left < right).astype(float) * -1

def alpha_082(p):
    a = rank(decay_linear(delta(p.open, int(np.floor(1.46063))), int(np.floor(14.8717))))
    blend = p.open * 0.634196 + p.open * (1 - 0.634196)
    b = ts_rank(decay_linear(correlation(indneutralize(p.volume, _ind(p, "sector")), blend, int(np.floor(17.4842))), int(np.floor(6.92131))), int(np.floor(13.4283)))
    return np.minimum(a, b) * -1

def alpha_083(p):
    hl = (p.high - p.low) / (ts_sum(p.close, 5) / 5 + 1e-12)
    return (rank(delay(hl, 2)) * rank(rank(p.volume))) / (hl / (p.vwap - p.close + 1e-12))

def alpha_084(p):
    return signedpower(ts_rank(p.vwap - ts_max(p.vwap, int(np.floor(15.3217))), int(np.floor(20.7127))), delta(p.close, int(np.floor(4.96796))))

def alpha_085(p):
    blend = p.high * 0.876703 + p.close * (1 - 0.876703)
    a = rank(correlation(blend, _adv(p, 30), int(np.floor(9.61331))))
    b = rank(correlation(ts_rank((p.high + p.low) / 2, int(np.floor(3.70596))), ts_rank(p.volume, int(np.floor(10.1595))), int(np.floor(7.11408))))
    return a ** b

def alpha_086(p):
    left = ts_rank(correlation(p.close, ts_sum(_adv(p, 20), int(np.floor(14.7444))), int(np.floor(6.00049))), int(np.floor(20.4195)))
    right = rank((p.open + p.close) - (p.vwap + p.open))
    return (left < right).astype(float) * -1

def alpha_087(p):
    blend = p.close * 0.369701 + p.vwap * (1 - 0.369701)
    a = rank(decay_linear(delta(blend, int(np.floor(1.91233))), int(np.floor(2.65461))))
    b = ts_rank(decay_linear(oabs(correlation(indneutralize(_adv(p, 81), _ind(p, "industry")), p.close, int(np.floor(13.4132)))), int(np.floor(4.89768))), int(np.floor(14.4535)))
    return np.maximum(a, b) * -1

def alpha_088(p):
    a = rank(decay_linear((rank(p.open) + rank(p.low)) - (rank(p.high) + rank(p.close)), int(np.floor(8.06882))))
    b = ts_rank(decay_linear(correlation(ts_rank(p.close, int(np.floor(8.44728))), ts_rank(_adv(p, 60), int(np.floor(20.6966))), int(np.floor(8.01266))), int(np.floor(6.65053))), int(np.floor(2.61957)))
    return np.minimum(a, b)

def alpha_089(p):
    blend = p.low * 0.967285 + p.low * (1 - 0.967285)
    a = ts_rank(decay_linear(correlation(blend, _adv(p, 10), int(np.floor(6.94279))), int(np.floor(5.51607))), int(np.floor(3.79744)))
    b = ts_rank(decay_linear(delta(indneutralize(p.vwap, _ind(p, "industry")), int(np.floor(3.48158))), int(np.floor(10.1466))), int(np.floor(15.3012)))
    return a - b

def alpha_090(p):
    a = rank(p.close - ts_max(p.close, int(np.floor(4.66719))))
    b = ts_rank(correlation(indneutralize(_adv(p, 40), _ind(p, "subindustry")), p.low, int(np.floor(5.38375))), int(np.floor(3.21856)))
    return (a ** b) * -1

def alpha_091(p):
    a = ts_rank(decay_linear(decay_linear(correlation(indneutralize(p.close, _ind(p, "industry")), p.volume, int(np.floor(9.74928))), int(np.floor(16.398))), int(np.floor(3.83219))), int(np.floor(4.8667)))
    b = rank(decay_linear(correlation(p.vwap, _adv(p, 30), int(np.floor(4.01303))), int(np.floor(2.6809))))
    return (a - b) * -1

def alpha_092(p):
    a = ts_rank(decay_linear((((p.high + p.low) / 2 + p.close) < (p.low + p.open)).astype(float), int(np.floor(14.7221))), int(np.floor(18.8683)))
    b = ts_rank(decay_linear(correlation(rank(p.low), rank(_adv(p, 30)), int(np.floor(7.58555))), int(np.floor(6.94024))), int(np.floor(6.80584)))
    return np.minimum(a, b)

def alpha_093(p):
    blend = p.close * 0.524434 + p.vwap * (1 - 0.524434)
    a = ts_rank(decay_linear(correlation(indneutralize(p.vwap, _ind(p, "industry")), _adv(p, 81), int(np.floor(17.4193))), int(np.floor(19.848))), int(np.floor(7.54455)))
    b = rank(decay_linear(delta(blend, int(np.floor(2.77377))), int(np.floor(16.2664))))
    return a / b

def alpha_094(p):
    a = rank(p.vwap - ts_min(p.vwap, int(np.floor(11.5783))))
    b = ts_rank(correlation(ts_rank(p.vwap, int(np.floor(19.6462))), ts_rank(_adv(p, 60), int(np.floor(4.02992))), int(np.floor(18.0926))), int(np.floor(2.70756)))
    return (a ** b) * -1

def alpha_095(p):
    left = rank(p.open - ts_min(p.open, int(np.floor(12.4105))))
    inner = rank(correlation(ts_sum((p.high + p.low) / 2, int(np.floor(19.1351))), ts_sum(_adv(p, 40), int(np.floor(19.1351))), int(np.floor(12.8742))) ** 5)
    right = ts_rank(inner, int(np.floor(11.7584)))
    return (left < right).astype(float)

def alpha_096(p):
    a = ts_rank(decay_linear(correlation(rank(p.vwap), rank(p.volume), int(np.floor(3.83878))), int(np.floor(4.16783))), int(np.floor(8.38151)))
    inner = ts_argmax(correlation(ts_rank(p.close, int(np.floor(7.45404))), ts_rank(_adv(p, 60), int(np.floor(4.13242))), int(np.floor(3.65459))), int(np.floor(12.6556)))
    b = ts_rank(decay_linear(inner, int(np.floor(14.0365))), int(np.floor(13.4143)))
    return pd.DataFrame(np.fmax(a.values, b.values), index=a.index, columns=a.columns) * -1

def alpha_097(p):
    blend = p.low * 0.721001 + p.vwap * (1 - 0.721001)
    a = rank(decay_linear(delta(indneutralize(blend, _ind(p, "industry")), int(np.floor(3.3705))), int(np.floor(20.4523))))
    b = ts_rank(decay_linear(ts_rank(correlation(ts_rank(p.low, int(np.floor(7.87871))), ts_rank(_adv(p, 60), int(np.floor(17.255))), int(np.floor(4.97547))), int(np.floor(18.5925))), int(np.floor(15.7152))), int(np.floor(6.71659)))
    return (a - b) * -1

def alpha_098(p):
    a = rank(decay_linear(correlation(p.vwap, ts_sum(_adv(p, 5), int(np.floor(26.4719))), int(np.floor(4.58418))), int(np.floor(7.18088))))
    inner = ts_argmin(correlation(rank(p.open), rank(_adv(p, 15)), int(np.floor(20.8187))), int(np.floor(8.62571)))
    b = rank(decay_linear(ts_rank(inner, int(np.floor(6.95668))), int(np.floor(8.07206))))
    return a - b

def alpha_099(p):
    left = rank(correlation(ts_sum((p.high + p.low) / 2, int(np.floor(19.8975))), ts_sum(_adv(p, 60), int(np.floor(19.8975))), int(np.floor(8.8136))))
    right = rank(correlation(p.low, p.volume, int(np.floor(6.28259))))
    return (left < right).astype(float) * -1

def alpha_100(p):
    inner = rank(((((p.close - p.low) - (p.high - p.close)) / (p.high - p.low + 1e-12)) * p.volume))
    x = scale(indneutralize(indneutralize(inner, _ind(p, "subindustry")), _ind(p, "subindustry")))
    y = scale(indneutralize((correlation(p.close, rank(_adv(p, 20)), 5) - rank(ts_argmin(p.close, 30))), _ind(p, "subindustry")))
    return 0 - (1 * (((1.5 * x) - y) * (p.volume / _adv(p, 20))))

def alpha_101(p):
    return (p.close - p.open) / ((p.high - p.low) + 0.001)


ALPHAS: dict[str, callable] = {f"alpha{i:03d}": globals()[f"alpha_{i:03d}"] for i in range(1, 102)}
