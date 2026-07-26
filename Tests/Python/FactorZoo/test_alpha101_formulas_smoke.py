# Tests/Python/FactorZoo/test_alpha101_formulas_smoke.py
"""All 101 alphas evaluate on a synthetic panel without exception, output >=1 non-NaN."""
import sys
from pathlib import Path
import numpy as np, pandas as pd, pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))
from alpha101.formulas import ALPHAS, INDCLASS_LEVELS  # noqa: E402
from alpha101.panel_loader import Alpha101Panel  # noqa: E402


@pytest.fixture
def synth_panel():
    rng = np.random.default_rng(42)
    codes = [f"S{i:03d}" for i in range(20)]
    # 260 unique trading-day-like dates. Long-lookback alphas (e.g. alpha#19/39
    # use sum(returns,250), alpha#48 corr(.,250), alpha#37/36 corr(.,200)) need
    # ≥250 rows to produce a non-NaN last row.
    dates = [f"2025{(i // 31) + 1:02d}{(i % 31) + 1:02d}" if i < 31 * 5 else
             f"2026{((i - 155) // 31) + 1:02d}{((i - 155) % 31) + 1:02d}"
             for i in range(260)]
    shape = (len(dates), len(codes))
    def W(base, scale=1.0):
        return pd.DataFrame(base * scale, index=dates, columns=codes, dtype=float)
    close = W(np.cumsum(rng.normal(0, 1, shape), axis=0) + 100)
    op = W(rng.normal(0, 1, shape)) + close - 1
    hi = pd.DataFrame(np.maximum(op.values, close.values) + np.abs(rng.normal(0, 0.5, shape)),
                      index=dates, columns=codes)
    lo = pd.DataFrame(np.minimum(op.values, close.values) - np.abs(rng.normal(0, 0.5, shape)),
                      index=dates, columns=codes)
    vol = W(rng.uniform(100, 10000, shape))
    amt = vol * close * 10
    vwap = (amt * 10) / vol
    rets = close.pct_change()
    cap = W(rng.uniform(1e5, 1e7, shape))
    adv = {n: amt.rolling(n).mean() for n in (5,10,15,20,30,40,50,60,81,120,150,180)}
    grp = {c: f"g{i%4}" for i, c in enumerate(codes)}
    ind = pd.DataFrame({"ts_code": codes, "l1": [grp[c][0] for c in codes],
                        "l2": [grp[c] for c in codes], "l3": [grp[c] for c in codes]})
    return Alpha101Panel(open=op, high=hi, low=lo, close=close, close_raw=close.copy(),
                          volume=vol, vwap=vwap, returns=rets, cap=cap, adv=adv,
                          industry=ind, dates=dates, asof=dates[-1])


@pytest.mark.parametrize("aid", sorted(ALPHAS))
def test_alpha_smoke(synth_panel, aid):
    out = ALPHAS[aid](synth_panel)
    assert isinstance(out, pd.DataFrame)
    assert out.shape[0] == len(synth_panel.dates)
    assert out.iloc[-1].notna().sum() >= 1, f"{aid} produced all-NaN last row"


def test_indclass_levels_table_completeness():
    assert len(INDCLASS_LEVELS) == 18
    for n, levels in INDCLASS_LEVELS.items():
        for lv in levels:
            assert lv in ("sector", "industry", "subindustry")
