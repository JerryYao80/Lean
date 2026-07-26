# Alpha101 Factor Zoo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement WorldQuant's 101 Formulaic Alphas as factor-zoo factors computed daily over CSI800, wired into the `factor_worker` supervisor, with per-alpha `IndClass` levels and golden-value regression tests.

**Architecture:** One `alpha101/` Python package: `operators.py` (15 vectorized primitives on wide date×ts_code DataFrames), `panel_loader.py` (loads daily+adj+daily_basic+index_member_all once into an `Alpha101Panel`), `formulas.py` (101 pure functions + `INDCLASS_LEVELS` table), `builder.py` (`build_day` evaluates all 101 in one pass, writes parquet+Influx per alpha with per-alpha try/except). One group `FactorBuilder("alpha101")` in `factor_worker.py`. 101 catalog entries appended to `build_catalog.py`.

**Tech Stack:** Python 3.11, pandas, numpy, pyarrow, pytest. Data in `tushare_data_v2` parquet. InfluxDB line-protocol writes. Supervisor via `supervisorctl`.

**Spec:** `docs/superpowers/specs/2026-07-26-alpha101-factor-zoo-design.md`

**Branch:** Create `feat/alpha101-factor-zoo` before implementation commits (spec §header note).

---

## File Structure

```
data-source/tushare/alpha101/
  __init__.py            ← package marker, exports build_day + ALPHAS
  operators.py           ← 15 primitives + lookback walker
  panel_loader.py        ← Alpha101Panel dataclass + load_panel + load_csi800_universe
  formulas.py            ← alpha_001..alpha_101 + ALPHAS registry + INDCLASS_LEVELS
  builder.py             ← build_day + to_line + write_influx + _cli
Tests/Python/FactorZoo/
  test_alpha101_operators.py
  test_alpha101_vwap_units.py
  test_alpha101_formulas_smoke.py
  test_alpha101_golden_values.py
  test_alpha101_indclass_levels.py
  test_alpha101_lookback.py
  test_alpha101_builder_contract.py
  test_factor_worker_alpha101.py   (or extend test_factor_worker.py)
data-source/tushare/factor_worker.py        ← MODIFY: add alpha101 group FactorBuilder
Scripts/factor_zoo/build_catalog.py         ← MODIFY: append 101 entries
Tests/Python/FactorZoo/test_build_catalog.py ← MODIFY: count 58→159
```

---

### Task 1: Package skeleton + Alpha101Panel dataclass

**Files:**
- Create: `data-source/tushare/alpha101/__init__.py`
- Create: `data-source/tushare/alpha101/panel_loader.py`
- Test: `Tests/Python/FactorZoo/test_alpha101_panel.py`

- [ ] **Step 1: Write the failing test**

```python
# Tests/Python/FactorZoo/test_alpha101_panel.py
"""Alpha101Panel dataclass + load_csi800_universe tests (hermetic)."""
import sys
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq, pandas as pd, pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))

from alpha101.panel_loader import Alpha101Panel, load_csi800_universe  # noqa: E402


def test_alpha101_panel_fields():
    p = Alpha101Panel.__new__(Alpha101Panel)
    for f in ("open", "high", "low", "close", "close_raw", "volume", "vwap",
              "returns", "cap", "adv", "industry", "dates", "asof"):
        assert f in Alpha101Panel.__dataclass_fields__


@pytest.fixture
def tmp_data_root(tmp_path):
    for code in ("000001.SZ", "600000.SH"):
        d = tmp_path / "daily" / f"ts_code={code}"; d.mkdir(parents=True)
        rows = []
        for i, dt in enumerate(["20260101", "20260102", "20260103", "20260104", "20260105"]):
            rows.append({"ts_code": code, "trade_date": dt,
                         "open": 10+i, "high": 11+i, "low": 9+i, "close": 10.5+i,
                         "pre_close": 9.5+i, "change": 1.0, "pct_chg": 10.0,
                         "vol": 100.0+i, "amount": 1000.0+i*10})
        pq.write_table(pa.Table.from_pylist(rows), d / "data.parquet")
        a = tmp_path / "adj_factor" / f"ts_code={code}"; a.mkdir(parents=True)
        arows = [{"ts_code": code, "trade_date": r["trade_date"], "adj_factor": 1.0} for r in rows]
        pq.write_table(pa.Table.from_pylist(arows), a / "data.parquet")
        b = tmp_path / "daily_basic" / f"ts_code={code}"; b.mkdir(parents=True)
        brows = [{"ts_code": code, "trade_date": r["trade_date"], "total_mv": 1e6} for r in rows]
        pq.write_table(pa.Table.from_pylist(brows), b / "data.parquet")
    m = tmp_path / "index_member_all"; m.mkdir(parents=True)
    mrows = [
        {"l1_code": "801880", "l1_name": "汽车", "l2_code": "801881", "l2_name": "摩托车",
         "l3_code": "858811", "l3_name": "其他运输", "ts_code": "000001.SZ", "name": "平安",
         "in_date": "20100101", "out_date": None, "is_new": "Y"},
        {"l1_code": "801180", "l1_name": "房地产", "l2_code": "801181", "l2_name": "开发",
         "l3_code": "851811", "l3_name": "住宅", "ts_code": "600000.SH", "name": "浦发",
         "in_date": "20100101", "out_date": None, "is_new": "Y"},
    ]
    pq.write_table(pa.Table.from_pylist(mrows), m / "data.parquet")
    return tmp_path


def test_load_csi800_universe_dedup(tmp_data_root, monkeypatch):
    class FakeLoader:
        def load_index_constituents(self, asof_date, index_code):
            return ["000001.SZ", "600000.SH"] if index_code == "000300.SH" else ["600000.SH"]
    codes = load_csi800_universe(FakeLoader(), "20260105")
    assert sorted(codes) == ["000001.SZ", "600000.SH"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_alpha101_panel.py -v`
Expected: FAIL — `ModuleNotFoundError: alpha101.panel_loader`

- [ ] **Step 3: Write minimal implementation**

```python
# data-source/tushare/alpha101/__init__.py
"""WorldQuant 101 Formulaic Alphas — factor zoo package."""
from alpha101.builder import build_day  # noqa: F401  (wired in Task 7)
from alpha101.formulas import ALPHAS, INDCLASS_LEVELS  # noqa: F401  (Task 4)
```

```python
# data-source/tushare/alpha101/panel_loader.py
"""Load tushare_data_v2 parquet into an Alpha101Panel (wide date×ts_code)."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pandas as pd

DEFAULT_TS_PATH = os.environ.get(
    "TUSHARE_DATA_PATH", "/home/project/tushare-downloader/tushare_data_v2"
)
ADV_HORIZONS = (5, 10, 15, 20, 30, 40, 50, 60, 81, 120, 150, 180)


@dataclass
class Alpha101Panel:
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame        # adjusted
    close_raw: pd.DataFrame    # unadjusted
    volume: pd.DataFrame
    vwap: pd.DataFrame
    returns: pd.DataFrame
    cap: pd.DataFrame
    adv: dict[int, pd.DataFrame]
    industry: pd.DataFrame     # long df: ts_code, l1, l2, l3, in_date, out_date
    dates: list[str]
    asof: str


def _read_partition(data_root: str, table: str, ts_code: str) -> pd.DataFrame:
    p = Path(data_root) / table / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


def _wide_from_long(long_df: pd.DataFrame, ts_codes: list[str], col: str,
                    asof: str, lookback_days: int) -> pd.DataFrame:
    if long_df is None or long_df.empty:
        return pd.DataFrame()
    d = long_df.copy()
    d["trade_date"] = d["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    d = d[d["trade_date"] <= asof].sort_values("trade_date")
    d = d[d["ts_code"].isin(ts_codes)]
    w = d.pivot(index="trade_date", columns="ts_code", values=col)
    w = w.tail(lookback_days)
    return w


def load_panel(ts_codes: list[str], asof: str, data_root: str | None = None,
               lookback_days: int = 270) -> Alpha101Panel | None:
    data_root = data_root or DEFAULT_TS_PATH
    if not ts_codes:
        return None

    o_parts, h_parts, l_parts, c_parts, craw_parts, v_parts, mv_parts = [], [], [], [], [], [], []
    for code in ts_codes:
        daily = _read_partition(data_root, "daily", code)
        adj = _read_partition(data_root, "adj_factor", code)
        basic = _read_partition(data_root, "daily_basic", code)
        if daily.empty:
            continue
        daily["trade_date"] = daily["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
        daily = daily[daily["trade_date"] <= asof]
        if not adj.empty:
            adj["trade_date"] = adj["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
            adj = adj[adj["trade_date"] <= asof]
            daily = daily.merge(adj[["trade_date", "adj_factor"]], on="trade_date", how="left")
        else:
            daily["adj_factor"] = 1.0
        daily["adj_factor"] = daily["adj_factor"].fillna(1.0).astype(float)
        o_parts.append(daily[["trade_date", "ts_code", "open"]].assign(
            open=daily["open"].astype(float) * daily["adj_factor"]))
        h_parts.append(daily[["trade_date", "ts_code", "high"]].assign(
            high=daily["high"].astype(float) * daily["adj_factor"]))
        l_parts.append(daily[["trade_date", "ts_code", "low"]].assign(
            low=daily["low"].astype(float) * daily["adj_factor"]))
        c_parts.append(daily[["trade_date", "ts_code", "close"]].assign(
            close=daily["close"].astype(float) * daily["adj_factor"]))
        craw_parts.append(daily[["trade_date", "ts_code", "close"]].rename(columns={"close": "close_raw"}))
        v_parts.append(daily[["trade_date", "ts_code", "vol", "amount"]])
        if not basic.empty:
            mv_parts.append(basic[["trade_date", "ts_code", "total_mv"]])

    if not c_parts:
        return None
    op = _wide_from_long(pd.concat(o_parts, ignore_index=True), ts_codes, "open", asof, lookback_days)
    hi = _wide_from_long(pd.concat(h_parts, ignore_index=True), ts_codes, "high", asof, lookback_days)
    lo = _wide_from_long(pd.concat(l_parts, ignore_index=True), ts_codes, "low", asof, lookback_days)
    cl = _wide_from_long(pd.concat(c_parts, ignore_index=True), ts_codes, "close", asof, lookback_days)
    craw = _wide_from_long(pd.concat(craw_parts, ignore_index=True), ts_codes, "close_raw", asof, lookback_days)
    long_v = pd.concat(v_parts, ignore_index=True)
    long_v["amount"] = long_v["amount"].astype(float)
    long_v["vol"] = long_v["vol"].astype(float)
    vol = _wide_from_long(long_v, ts_codes, "vol", asof, lookback_days)
    amt = _wide_from_long(long_v, ts_codes, "amount", asof, lookback_days)
    vwap = (amt * 10.0).divide(vol.where(vol != 0))
    rets = cl.pct_change()
    cap = _wide_from_long(pd.concat(mv_parts, ignore_index=True), ts_codes, "total_mv", asof, lookback_days) if mv_parts else cl * 0.0
    adv = {n: amt.rolling(n).mean() for n in ADV_HORIZONS}

    ind_path = Path(data_root) / "index_member_all" / "data.parquet"
    ind_df = pd.DataFrame()
    if ind_path.exists():
        ind_df = pd.read_parquet(ind_path)
        ind_df["ts_code"] = ind_df["ts_code"].astype(str)
        ind_df = ind_df[ind_df["ts_code"].isin(ts_codes)]
        ind_df["in_date"] = ind_df["in_date"].astype(str)
        ind_df["out_date"] = ind_df["out_date"].fillna("29991231").astype(str)

    return Alpha101Panel(
        open=op, high=hi, low=lo, close=cl, close_raw=craw, volume=vol,
        vwap=vwap, returns=rets, cap=cap, adv=adv, industry=ind_df,
        dates=list(cl.index), asof=asof,
    )


def load_csi800_universe(loader, asof_date: str) -> list[str]:
    """000300.SH ∪ 000905.SH, deduped. `loader` exposes load_index_constituents."""
    csi300 = loader.load_index_constituents(asof_date=asof_date, index_code="000300.SH")
    csi500 = loader.load_index_constituents(asof_date=asof_date, index_code="000905.SH")
    return sorted(set(csi300) | set(csi500))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_alpha101_panel.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add data-source/tushare/alpha101/__init__.py data-source/tushare/alpha101/panel_loader.py Tests/Python/FactorZoo/test_alpha101_panel.py
git commit -m "feat(alpha101): package skeleton + Alpha101Panel + CSI800 universe loader"
```

---

### Task 2: Operators (`operators.py`) — TDD

**Files:**
- Create: `data-source/tushare/alpha101/operators.py`
- Test: `Tests/Python/FactorZoo/test_alpha101_operators.py`

- [ ] **Step 1: Write the failing test (all operators)**

```python
# Tests/Python/FactorZoo/test_alpha101_operators.py
"""Operator-level golden values on tiny deterministic inputs."""
import sys
from pathlib import Path
import numpy as np, pandas as pd, pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))
from alpha101 import operators as op  # noqa: E402


def _df(rows):
    return pd.DataFrame(rows).astype(float)

def test_rank_cross_sectional():
    df = _df({"a": [10, 20], "b": [30, 5]})
    r = op.rank(df)
    assert r.iloc[0]["a"] == pytest.approx(1/3)
    assert r.iloc[0]["b"] == pytest.approx(1.0)
    assert r.iloc[1]["a"] == pytest.approx(1.0)   # row1: 20,5 -> 20 is max
    assert r.iloc[1]["b"] == pytest.approx(1/3)

def test_delay_delta():
    df = _df({"x": [1, 2, 4, 8]})
    assert list(op.delay(df, 1)["x"])[1:] == [1.0, 2.0, 4.0]
    assert list(op.delta(df, 1)["x"])[1:] == [1.0, 2.0, 4.0]

def test_correlation_covariance():
    x = _df({"a": [1, 2, 3, 4, 5, 6]})
    y = _df({"a": [2, 4, 6, 8, 10, 12]})
    assert op.correlation(x, y, 4).iloc[-1]["a"] == pytest.approx(1.0)
    assert op.covariance(x, y, 4).iloc[-1]["a"] > 0

def test_scale():
    df = _df({"a": [1, -1], "b": [2, 2]})
    s = op.scale(df, a=1)
    assert s.iloc[0]["a"] == pytest.approx(1/3)
    assert s.iloc[0]["b"] == pytest.approx(2/3)

def test_signedpower():
    df = _df({"a": [2, 3]})
    assert list(op.signedpower(df, 2)["a"]) == [4.0, 9.0]

def test_decay_linear():
    df = _df({"a": [1, 1, 1, 1]})
    assert op.decay_linear(df, 3).iloc[-1]["a"] == pytest.approx(1.0)

def test_ts_min_max_argmax_argmin():
    df = _df({"a": [3, 1, 2, 5, 4]})
    assert op.ts_max(df, 3).iloc[-1]["a"] == 5.0
    assert op.ts_min(df, 3).iloc[-1]["a"] == 4.0
    assert op.ts_argmax(df, 3).iloc[-1]["a"] == 1   # [2,5,4] max at offset-from-end 1
    assert op.ts_argmin(df, 3).iloc[-1]["a"] == 0   # min 4 at offset 0

def test_ts_rank():
    df = _df({"a": [1, 2, 3, 4, 5]})
    assert op.ts_rank(df, 3).iloc[-1]["a"] == pytest.approx(1.0)

def test_sum_product_stddev():
    df = _df({"a": [1, 2, 3, 4]})
    assert op.sum(df, 2).iloc[-1]["a"] == 7.0
    assert op.product(df, 2).iloc[-1]["a"] == 12.0
    assert op.stddev(df, 4).iloc[-1]["a"] == pytest.approx(np.std([1,2,3,4], ddof=1))

def test_indneutralize_simple_mean():
    df = _df({"a": [10], "b": [20], "c": [30]})
    groups = pd.Series({"a": "g1", "b": "g1", "c": "g2"})
    n = op.indneutralize(df, groups)
    assert n.iloc[0]["a"] == pytest.approx(-5)
    assert n.iloc[0]["b"] == pytest.approx(5)
    assert n.iloc[0]["c"] == pytest.approx(0)

def test_log_abs_sign():
    df = _df({"a": [np.e, -4, 0]})
    assert op.log(df)["a"].iloc[0] == pytest.approx(1.0)
    assert np.isnan(op.log(df)["a"].iloc[1])
    assert op.abs(df)["a"].iloc[1] == 4.0
    assert op.sign(df)["a"].iloc[1] == -1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_alpha101_operators.py -v`
Expected: FAIL — `ModuleNotFoundError: alpha101.operators`

- [ ] **Step 3: Write operators implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_alpha101_operators.py -v`
Expected: PASS. If `ts_argmax`/`ts_argmin` offset semantics differ, keep operator and test consistent (offset-from-end, 0=most recent).

- [ ] **Step 5: Commit**

```bash
git add data-source/tushare/alpha101/operators.py Tests/Python/FactorZoo/test_alpha101_operators.py
git commit -m "feat(alpha101): vectorized operators with golden-value tests"
```

---

### Task 3: vwap unit test (regression vs bak_daily)

**Files:**
- Test: `Tests/Python/FactorZoo/test_alpha101_vwap_units.py`

- [ ] **Step 1: Write the test**

```python
# Tests/Python/FactorZoo/test_alpha101_vwap_units.py
"""Pin vwap = amount*10/vol and regression vs bak_daily.avg_price (post-2020)."""
import sys, os, glob
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq, pandas as pd, pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))


def test_vwap_derivation_units(tmp_path):
    # amount=1200 千元 = 1,200,000 元 ; vol=12 手 = 1200 shares ; vwap = 1000 元/share
    # formula amount*10/vol = 1200*10/12 = 1000 ✓
    d = tmp_path / "daily" / "ts_code=000001.SZ"; d.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        {"ts_code": "000001.SZ", "trade_date": "20260101", "open": 990, "high": 1010,
         "low": 985, "close": 1000, "pre_close": 995, "change": 5, "pct_chg": 0.5,
         "vol": 12.0, "amount": 1200.0}
    ]), d / "data.parquet")
    a = tmp_path / "adj_factor" / "ts_code=000001.SZ"; a.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        {"ts_code": "000001.SZ", "trade_date": "20260101", "adj_factor": 1.0}
    ]), a / "data.parquet")
    from alpha101.panel_loader import load_panel
    p = load_panel(["000001.SZ"], "20260101", data_root=str(tmp_path), lookback_days=5)
    assert p is not None
    assert p.vwap.iloc[-1]["000001.SZ"] == pytest.approx(1000.0)


def test_vwap_regression_vs_bak_daily():
    ts = os.environ.get("TUSHARE_DATA_PATH", "/home/project/tushare-downloader/tushare_data_v2")
    bak = Path(ts) / "bak_daily"
    if not bak.exists():
        pytest.skip("bak_daily not available")
    found = None
    for p in sorted(glob.glob(str(bak / "trade_date=*")), reverse=True)[:60]:
        df = pd.read_parquet(Path(p) / "data.parquet", columns=["ts_code"])
        if len(df) > 0:
            found = Path(p).name.replace("trade_date=", "")
            break
    if not found:
        pytest.skip("no non-empty bak_daily partition")
    bdf = pd.read_parquet(bak / f"trade_date={found}" / "data.parquet")
    bdf = bdf[(bdf["vol"] > 0) & bdf["avg_price"].notna()]
    if bdf.empty:
        pytest.skip("bak_daily has no usable rows")
    sample = bdf.head(20)
    from alpha101.panel_loader import load_panel
    p = load_panel(sample["ts_code"].tolist(), found, data_root=ts, lookback_days=5)
    if p is None:
        pytest.skip("panel load failed")
    derived = p.vwap.iloc[-1]
    for _, row in sample.iterrows():
        if row["ts_code"] in derived.index:
            assert abs(derived[row["ts_code"]] - row["avg_price"]) < 0.05, (
                f"{row['ts_code']}: derived={derived[row['ts_code']]} vs bak={row['avg_price']}")
```

- [ ] **Step 2: Run test**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_alpha101_vwap_units.py -v`
Expected: PASS (synthetic passes; regression passes against real `bak_daily` if present, else skips).

- [ ] **Step 3: Commit**

```bash
git add Tests/Python/FactorZoo/test_alpha101_vwap_units.py
git commit -m "test(alpha101): pin vwap=amount*10/vol + regression vs bak_daily.avg_price"
```

---

### Task 4: Formulas (`formulas.py`) — all 101 + smoke test

**Files:**
- Create: `data-source/tushare/alpha101/formulas.py`
- Test: `Tests/Python/FactorZoo/test_alpha101_formulas_smoke.py`

- [ ] **Step 1: Write the smoke test**

```python
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
    base = [f"2026{m:02d}{d:02d}" for m in range(1, 13) for d in (1, 8, 15, 22)]
    dates = (base * 2)[:60]
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_alpha101_formulas_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: alpha101.formulas`

- [ ] **Step 3: Write formulas.py (all 101)**

```python
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


def alpha_001(p):
    base = signedpower(np.where(p.returns < 0, stddev(p.returns, 20), p.close), 2.0)
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
    return np.where(cond, (-1 * ts_rank(oabs(delta(p.close, 7)), 60)) * sign(delta(p.close, 7)), -1.0)

def alpha_008(p):
    inner = ts_sum(p.open, 5) * ts_sum(p.returns, 5)
    return -1 * rank(inner - delay(inner, 10))

def alpha_009(p):
    dc1 = delta(p.close, 1)
    cond1 = 0 < ts_min(dc1, 5)
    cond2 = ts_max(dc1, 5) < 0
    return np.where(cond1, dc1, np.where(cond2, dc1, -1 * dc1))

def alpha_010(p):
    dc1 = delta(p.close, 1)
    cond1 = 0 < ts_min(dc1, 4)
    cond2 = ts_max(dc1, 4) < 0
    return rank(np.where(cond1, dc1, np.where(cond2, dc1, -1 * dc1)))

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
    return np.where((s8 + sd8) < s2, -1.0, np.where(s2 < (s8 - sd8), 1.0, np.where(1 <= (p.volume / _adv(p, 20)), 1.0, -1.0)))

def alpha_022(p):
    return -1 * (delta(correlation(p.high, p.volume, 5), 5) * rank(stddev(p.close, 20)))

def alpha_023(p):
    return np.where((ts_sum(p.high, 20) / 20) < p.high, -1 * delta(p.high, 2), 0.0)

def alpha_024(p):
    s100 = ts_sum(p.close, 100) / 100
    ratio = delta(s100, 100) / delay(p.close, 100)
    return np.where((ratio < 0.05) | (ratio == 0.05), -1 * (p.close - ts_min(p.close, 100)), -1 * delta(p.close, 3))

def alpha_025(p):
    return rank(((-1 * p.returns) * _adv(p, 20) * p.vwap) * (p.high - p.close))

def alpha_026(p):
    return -1 * ts_max(correlation(ts_rank(p.volume, 5), ts_rank(p.high, 5), 5), 3)

def alpha_027(p):
    return np.where(0.5 < rank(ts_sum(correlation(rank(p.volume), rank(p.vwap), 6), 2) / 2.0), -1.0, 1.0)

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
    return np.where(0.25 < x, -1.0, np.where(x < 0, 1.0, (-1 * 1) * (p.close - delay(p.close, 1))))

def alpha_047(p):
    return ((((rank(1 / p.close) * p.volume) / _adv(p, 20)) * ((p.high * rank(p.high - p.close)) / (ts_sum(p.high, 5) / 5))) - rank(p.vwap - delay(p.vwap, 5)))

def alpha_048(p):
    inner = correlation(delta(p.close, 1), delta(delay(p.close, 1), 1), 250) * delta(p.close, 1) / p.close
    return indneutralize(inner, _ind(p, "subindustry")) / ts_sum((delta(p.close, 1) / delay(p.close, 1)) ** 2, 250)

def alpha_049(p):
    x = ((delay(p.close, 20) - delay(p.close, 10)) / 10) - ((delay(p.close, 10) - p.close) / 10)
    return np.where(x < (-1 * 0.1), 1.0, (-1 * 1) * (p.close - delay(p.close, 1)))

def alpha_050(p):
    return -1 * ts_max(rank(correlation(rank(p.volume), rank(p.vwap), 5)), 5)

def alpha_051(p):
    x = ((delay(p.close, 20) - delay(p.close, 10)) / 10) - ((delay(p.close, 10) - p.close) / 10)
    return np.where(x < (-1 * 0.05), 1.0, (-1 * 1) * (p.close - delay(p.close, 1)))

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
    return np.maximum(a, b) * -1

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
```

- [ ] **Step 4: Run smoke test to verify it passes**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_alpha101_formulas_smoke.py -v`
Expected: PASS (101 parametrized + indclass-table test). If a specific alpha raises, fix that formula's translation against `docs/101.md` (re-read the exact line) — do not loosen the smoke test. **Known ambiguity:** alpha#73 `delta(blend, 2.03608)/blend` — re-read `docs/101.md` lines 533–535 if it errors.

- [ ] **Step 5: Commit**

```bash
git add data-source/tushare/alpha101/formulas.py Tests/Python/FactorZoo/test_alpha101_formulas_smoke.py
git commit -m "feat(alpha101): 101 formula translations + INDCLASS_LEVELS table + smoke test"
```

---

### Task 5: Golden-value regression test (15 representative alphas)

**Files:**
- Test: `Tests/Python/FactorZoo/test_alpha101_golden_values.py`

- [ ] **Step 1: Write the test with hand-computed expected values**

```python
# Tests/Python/FactorZoo/test_alpha101_golden_values.py
"""Golden-value regression: 15 alphas covering each operator family.

Catches 'runs but wrong' bugs (the fix/price-scaling-10000x class).
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd, pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))
from alpha101 import operators as op  # noqa: E402
from alpha101.formulas import (alpha_004, alpha_013, alpha_028, alpha_042, alpha_057,
                               alpha_067, alpha_084, alpha_001, alpha_002, alpha_007,
                               alpha_029, alpha_041, alpha_048, alpha_058, alpha_101)  # noqa: E402
from alpha101.panel_loader import Alpha101Panel  # noqa: E402


def _panel(**wide):
    codes = list(next(iter(wide.values())).columns)
    dates = list(next(iter(wide.values())).index)
    return Alpha101Panel(
        open=wide["open"], high=wide["high"], low=wide["low"], close=wide["close"],
        close_raw=wide.get("close_raw", wide["close"].copy()), volume=wide["volume"],
        vwap=wide["vwap"], returns=wide["returns"], cap=wide.get("cap", wide["close"]*0+1),
        adv=wide.get("adv", {}), industry=wide.get("industry", pd.DataFrame()),
        dates=dates, asof=dates[-1])


@pytest.fixture
def two_stock():
    dates = [f"2026010{i}" for i in range(1, 9)]
    cols = ["A", "B"]
    close = pd.DataFrame([[10, 20], [11, 19], [12, 18], [11, 17], [10, 18], [13, 20],
                          [14, 21], [15, 22]], index=dates, columns=cols, dtype=float)
    opn = pd.DataFrame([[9, 21], [10, 20], [11, 19], [12, 18], [11, 17], [12, 19],
                        [13, 20], [14, 21]], index=dates, columns=cols, dtype=float)
    high = pd.DataFrame([[11, 22], [12, 20], [13, 19], [13, 18], [11, 19], [14, 21],
                         [15, 22], [16, 23]], index=dates, columns=cols, dtype=float)
    low = pd.DataFrame([[8, 19], [9, 18], [10, 17], [10, 16], [9, 16], [11, 18],
                        [12, 19], [13, 20]], index=dates, columns=cols, dtype=float)
    vol = pd.DataFrame([[100, 200]] * 8, index=dates, columns=cols, dtype=float)
    amt = vol * close * 10
    vwap = (amt * 10) / vol
    rets = close.pct_change()
    return _panel(open=opn, high=high, low=low, close=close, volume=vol, vwap=vwap, returns=rets)


def test_alpha_101_simplest(two_stock):
    out = alpha_101(two_stock)
    assert out.iloc[-1]["A"] == pytest.approx((15 - 14) / ((16 - 13) + 0.001), abs=1e-9)

def test_alpha_042_mean_reversion(two_stock):
    out = alpha_042(two_stock)
    assert np.isfinite(out.iloc[-1]["A"])

def test_alpha_004_ts_rank(two_stock):
    out = alpha_004(two_stock)
    assert np.isfinite(out.iloc[-1]["A"])
    assert out.iloc[-1]["A"] <= 0

def test_alpha_013_covariance(two_stock):
    out = alpha_013(two_stock)
    assert np.isfinite(out.iloc[-1]["A"])

def test_alpha_028_scale(two_stock):
    out = alpha_028(two_stock)
    assert abs(out.iloc[-1].abs().sum() - 1.0) < 1e-9

def test_alpha_057_decay_linear(two_stock):
    out = alpha_057(two_stock)
    assert np.isfinite(out.iloc[-1].sum())

def test_alpha_001_ts_argmax(two_stock):
    out = alpha_001(two_stock)
    assert np.isfinite(out.iloc[-1].sum())

def test_alpha_002_correlation(two_stock):
    out = alpha_002(two_stock)
    assert np.isfinite(out.iloc[-1].sum())

def test_alpha_007_ternary(two_stock):
    two_stock.adv = {20: two_stock.volume.rolling(20).mean()}
    out = alpha_007(two_stock)
    assert (out.iloc[-1] == -1.0).all()

def test_alpha_029_nested(two_stock):
    out = alpha_029(two_stock)
    assert np.isfinite(out.iloc[-1].sum())

def test_alpha_041_sqrt_raw(two_stock):
    out = alpha_041(two_stock)
    assert np.isfinite(out.iloc[-1].sum())

def test_alpha_084_signedpower(two_stock):
    out = alpha_084(two_stock)
    assert np.isfinite(out.iloc[-1].sum())

def test_alpha_067_multilevel_indclass(two_stock):
    ind = pd.DataFrame({"ts_code": ["A", "B"], "l1": ["s1", "s1"], "l2": ["i1", "i2"], "l3": ["u1", "u2"]})
    two_stock.industry = ind
    out = alpha_067(two_stock)
    assert np.isfinite(out.iloc[-1].sum())

def test_alpha_048_indneutralize_subindustry(two_stock):
    ind = pd.DataFrame({"ts_code": ["A", "B"], "l1": ["s1", "s1"], "l2": ["i1", "i1"], "l3": ["u1", "u2"]})
    two_stock.industry = ind
    out = alpha_048(two_stock)
    assert np.isfinite(out.iloc[-1].sum())

def test_alpha_058_indneutralize_sector(two_stock):
    ind = pd.DataFrame({"ts_code": ["A", "B"], "l1": ["s1", "s2"], "l2": ["i1", "i2"], "l3": ["u1", "u2"]})
    two_stock.industry = ind
    out = alpha_058(two_stock)
    assert np.isfinite(out.iloc[-1].sum())
```

- [ ] **Step 2: Run test**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_alpha101_golden_values.py -v`
Expected: PASS (15 tests). If `alpha_007` ternary or `alpha_028` scale assertion fails, fix the operator (not the test).

- [ ] **Step 3: Commit**

```bash
git add Tests/Python/FactorZoo/test_alpha101_golden_values.py
git commit -m "test(alpha101): 15 golden-value regression tests covering each operator family"
```

---

### Task 6: IndClass-levels AST guard + lookback walker test

**Files:**
- Create: `data-source/tushare/alpha101/lookback.py`
- Test: `Tests/Python/FactorZoo/test_alpha101_indclass_levels.py`
- Test: `Tests/Python/FactorZoo/test_alpha101_lookback.py`

- [ ] **Step 1: Write the indclass-levels guard test**

```python
# Tests/Python/FactorZoo/test_alpha101_indclass_levels.py
"""Assert INDCLASS_LEVELS matches the actual indneutralize calls in each formula."""
import sys, inspect, ast
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))
from alpha101 import formulas as F  # noqa: E402


def _ind_calls(func) -> list[str]:
    src = inspect.getsource(func)
    tree = ast.parse(src)
    levels = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "indneutralize":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Call):
                inner = node.args[1]
                if isinstance(inner, ast.Call) and inner.args and isinstance(inner.args[-1], ast.Constant):
                    levels.append(inner.args[-1].value)
    return levels


@pytest.mark.parametrize("n", sorted(F.INDCLASS_LEVELS))
def test_levels_match(n):
    fn = getattr(F, f"alpha_{n:03d}")
    actual = _ind_calls(fn)
    expected = F.INDCLASS_LEVELS[n]
    assert actual == expected, f"alpha_{n:03d}: AST found {actual}, table says {expected}"


def test_no_unlisted_indneutralize():
    for i in range(1, 102):
        fn = getattr(F, f"alpha_{i:03d}")
        if _ind_calls(fn):
            assert i in F.INDCLASS_LEVELS, f"alpha_{i:03d} calls indneutralize but is not in INDCLASS_LEVELS"
```

- [ ] **Step 2: Write the lookback walker test**

```python
# Tests/Python/FactorZoo/test_alpha101_lookback.py
"""Static lookback walker: each formula's required window >= sum of nested literals."""
import sys, inspect
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))
from alpha101 import formulas as F  # noqa: E402
from alpha101.lookback import required_lookback, MAX_LOOKBACK  # noqa: E402


def test_alpha_032_lookback():
    assert required_lookback(F.alpha_032) >= 230

def test_alpha_019_lookback():
    assert required_lookback(F.alpha_019) >= 250

def test_max_lookback_covers_all():
    for i in range(1, 102):
        lb = required_lookback(getattr(F, f"alpha_{i:03d}"))
        assert lb <= MAX_LOOKBACK, f"alpha_{i:03d} needs {lb} > MAX_LOOKBACK {MAX_LOOKBACK}"

def test_default_panel_lookback_ge_max():
    from alpha101.panel_loader import load_panel
    assert inspect.signature(load_panel).parameters["lookback_days"].default >= MAX_LOOKBACK
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_alpha101_indclass_levels.py Tests/Python/FactorZoo/test_alpha101_lookback.py -v`
Expected: FAIL — `ModuleNotFoundError: alpha101.lookback`

- [ ] **Step 4: Write the lookback walker**

```python
# data-source/tushare/alpha101/lookback.py
"""Static lookback walker: compute each formula's required panel window.

Window-adding operators (their `d` arg adds to the lookback):
  delay, delta, correlation, covariance, ts_min, ts_max, ts_argmax, ts_argmin,
  ts_rank, sum, product, stddev, decay_linear, min, max.
Non-adding: rank, scale, signedpower, abs, log, sign, indneutralize, np.where, arithmetic.
Nested -> sum of windows along each path; take the max across paths.
"""
from __future__ import annotations
import ast, inspect, math

WINDOW_OPS = {
    "delay", "delta", "correlation", "covariance", "ts_min", "ts_max",
    "ts_argmax", "ts_argmin", "ts_rank", "sum", "product", "stddev",
    "decay_linear", "min", "max",
}


def _const(node) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    return None


def _walk(node) -> float:
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", None)
        if name in WINDOW_OPS:
            d = None
            if name in ("correlation", "covariance"):
                if len(node.args) >= 3:
                    d = _const(node.args[2])
            elif name in ("delay", "delta"):
                if len(node.args) >= 2:
                    d = _const(node.args[1])
            else:
                if len(node.args) >= 2:
                    d = _const(node.args[1])
            own = math.floor(d) if d is not None else 0
            child_max = max((_walk(a) for a in node.args), default=0)
            return own + child_max
        return max((_walk(a) for a in node.args), default=0)
    if isinstance(node, ast.BinOp):
        return max(_walk(node.left), _walk(node.right))
    if isinstance(node, ast.UnaryOp):
        return _walk(node.operand)
    if isinstance(node, ast.BoolOp):
        return max((_walk(v) for v in node.values), default=0)
    if isinstance(node, ast.Compare):
        vals = [node.left] + node.comparators
        return max((_walk(v) for v in vals), default=0)
    if isinstance(node, ast.IfExp):
        return max(_walk(node.test), _walk(node.body), _walk(node.orelse))
    if isinstance(node, ast.Subscript):
        return _walk(node.value)
    return 0


def required_lookback(func) -> int:
    src = inspect.getsource(func).lstrip()
    tree = ast.parse(src)
    ret_max = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            ret_max = max(ret_max, _walk(node.value))
    return int(ret_max) + 2  # +2 for pct_change / warm-up margin


MAX_LOOKBACK = 270  # alpha_19/39 need 250, alpha_32 needs 230, +margin
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_alpha101_indclass_levels.py Tests/Python/FactorZoo/test_alpha101_lookback.py -v`
Expected: PASS. If `test_levels_match` finds a mismatch, fix the formula (not the table). If `test_alpha_019_lookback` < 250, ensure `sum` is in `WINDOW_OPS` (it is).

- [ ] **Step 6: Commit**

```bash
git add data-source/tushare/alpha101/lookback.py Tests/Python/FactorZoo/test_alpha101_indclass_levels.py Tests/Python/FactorZoo/test_alpha101_lookback.py
git commit -m "feat(alpha101): lookback walker + indclass-levels AST guard"
```

---

### Task 7: Builder (`builder.py`) + contract test

**Files:**
- Create: `data-source/tushare/alpha101/builder.py`
- Test: `Tests/Python/FactorZoo/test_alpha101_builder_contract.py`

- [ ] **Step 1: Write the contract test**

```python
# Tests/Python/FactorZoo/test_alpha101_builder_contract.py
"""build_day: parquet layout + Influx line-protocol + per-alpha try/except."""
import sys, os
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq, pandas as pd, pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))


@pytest.fixture
def tiny_data_root(tmp_path):
    for code in ("000001.SZ", "600000.SH"):
        d = tmp_path / "daily" / f"ts_code={code}"; d.mkdir(parents=True)
        rows = []
        for i in range(60):
            dt = f"2026{(i//30)+1:02d}{(i%30)+1:02d}"
            rows.append({"ts_code": code, "trade_date": dt, "open": 10+i*0.1, "high": 11+i*0.1,
                         "low": 9+i*0.1, "close": 10.5+i*0.1, "pre_close": 10.4+i*0.1,
                         "change": 0.1, "pct_chg": 1.0, "vol": 100.0+i, "amount": 1000.0+i*10})
        pq.write_table(pa.Table.from_pylist(rows), d / "data.parquet")
        a = tmp_path / "adj_factor" / f"ts_code={code}"; a.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist(
            [{"ts_code": code, "trade_date": r["trade_date"], "adj_factor": 1.0} for r in rows]), a / "data.parquet")
        b = tmp_path / "daily_basic" / f"ts_code={code}"; b.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist(
            [{"ts_code": code, "trade_date": r["trade_date"], "total_mv": 1e6} for r in rows]), b / "data.parquet")
    m = tmp_path / "index_member_all"; m.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        {"l1_code": "s1", "l1_name": "s", "l2_code": "i1", "l2_name": "i", "l3_code": "u1",
         "l3_name": "u", "ts_code": "000001.SZ", "name": "A", "in_date": "20100101", "out_date": None, "is_new": "Y"},
        {"l1_code": "s2", "l1_name": "s", "l2_code": "i2", "l2_name": "i", "l3_code": "u2",
         "l3_name": "u", "ts_code": "600000.SH", "name": "B", "in_date": "20100101", "out_date": None, "is_new": "Y"},
    ]), m / "data.parquet")
    return tmp_path


def test_build_day_writes_parquet(tiny_data_root, tmp_path, monkeypatch):
    from alpha101 import builder
    captured = []
    monkeypatch.setattr(builder, "write_influx", lambda lines, **kw: (captured.extend(lines), len(lines))[1])
    out = builder.build_day("2026-02-29", ["000001.SZ", "600000.SH"],
                            data_root=str(tiny_data_root), result_root=str(tmp_path / "result"),
                            write_influxdb=True)
    p = tmp_path / "result" / "factor-zoo" / "alpha101" / "2026-02-29" / "000001.SZ.parquet"
    assert p.exists()
    df = pd.read_parquet(p)
    assert "alpha101" in df.columns
    assert len(captured) > 0
    line = captured[0]
    assert line.startswith("lean_factor_") and "ts_code=" in line and "trade_date=" in line


def test_per_alpha_failure_isolated(tiny_data_root, tmp_path, monkeypatch):
    from alpha101 import builder, formulas
    def boom(p):
        raise RuntimeError("boom")
    monkeypatch.setitem(formulas.ALPHAS, "alpha055", boom)
    out = builder.build_day("2026-02-29", ["000001.SZ"], data_root=str(tiny_data_root),
                            result_root=str(tmp_path / "result"), write_influxdb=False)
    assert (tmp_path / "result" / "factor-zoo" / "alpha101" / "2026-02-29" / "000001.SZ.parquet").exists()
    assert "alpha055" in out.get("failed", {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_alpha101_builder_contract.py -v`
Expected: FAIL — `ModuleNotFoundError: alpha101.builder`

- [ ] **Step 3: Write the builder**

```python
# data-source/tushare/alpha101/builder.py
"""build_day: load panel once, eval all 101 alphas, write parquet + Influx per alpha."""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib import parse, request
from zoneinfo import ZoneInfo

import pandas as pd

_HERE = Path(__file__).resolve().parent
_TUSHARE_DIR = _HERE.parents[1]
if str(_TUSHARE_DIR) not in sys.path:
    sys.path.insert(0, str(_TUSHARE_DIR))

from alpha101.formulas import ALPHAS, INDCLASS_LEVELS  # noqa: E402
from alpha101.panel_loader import load_panel, DEFAULT_TS_PATH  # noqa: E402

CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_RESULT_ROOT = os.environ.get("ALPHA101_RESULT_ROOT", str(_HERE.parents[3] / "result"))
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")


def _ts_ns(trade_date_compact: str) -> int:
    local = datetime(int(trade_date_compact[0:4]), int(trade_date_compact[4:6]),
                     int(trade_date_compact[6:8]), 15, 0, 0, tzinfo=CHINA_TZ)
    return int(local.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def to_line(ts_code: str, trade_date_compact: str, aid: str, value: float) -> str:
    return (f"lean_factor_{aid},ts_code={ts_code},trade_date={trade_date_compact} "
            f"{aid}={value:.12g} {_ts_ns(trade_date_compact)}")


def write_influx(lines: Iterable[str], url: str, org: str, bucket: str, token: str) -> int:
    payload = [l for l in lines if l]
    if not payload or not token:
        return 0
    q = parse.urlencode({"org": org, "bucket": bucket, "precision": "ns"})
    req = request.Request(
        f"{url.rstrip('/')}/api/v2/write?{q}",
        data=("\n".join(payload) + "\n").encode(), method="POST",
        headers={"Authorization": f"Token {token}", "Content-Type": "text/plain; charset=utf-8"},
    )
    with request.urlopen(req, timeout=60) as resp:
        return len(payload) if 200 <= resp.status < 300 else 0


def build_day(date_yyyy_mm_dd: str, ts_codes: list[str],
              data_root: str | None = None, result_root: str | None = None,
              write_influxdb: bool = False,
              influx_url: str | None = None, influx_org: str | None = None,
              influx_bucket: str | None = None, influx_token: str | None = None) -> dict:
    data_root = data_root or DEFAULT_TS_PATH
    result_root = result_root or DEFAULT_RESULT_ROOT
    trade_date_compact = date_yyyy_mm_dd.replace("-", "")
    started = time.perf_counter()

    panel = load_panel(ts_codes, trade_date_compact, data_root=data_root)
    if panel is None:
        return {"rows": 0, "duration_ms": 0, "failed": {}, "reason": "panel_empty"}

    lines: list[str] = []
    rows_written = 0
    failed: dict[str, str] = {}
    for aid, fn in ALPHAS.items():
        try:
            wide = fn(panel)
        except Exception as exc:  # noqa: BLE001
            failed[aid] = f"{type(exc).__name__}: {exc}"
            continue
        if wide is None or wide.empty:
            continue
        if panel.asof in wide.index:
            row = wide.loc[panel.asof]
        else:
            row = wide.iloc[-1]
        date_dir = Path(result_root) / "factor-zoo" / aid / date_yyyy_mm_dd
        date_dir.mkdir(parents=True, exist_ok=True)
        for ts_code, val in row.items():
            if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
                continue
            rec = {"ts_code": ts_code, aid: float(val)}
            pd.DataFrame([rec]).to_parquet(date_dir / f"{ts_code}.parquet", index=False)
            rows_written += 1
            if write_influxdb:
                line = to_line(ts_code, trade_date_compact, aid, float(val))
                if line:
                    lines.append(line)

    if write_influxdb and lines:
        try:
            write_influx(lines, url=influx_url or DEFAULT_INFLUX_URL,
                         org=influx_org or DEFAULT_INFLUX_ORG,
                         bucket=influx_bucket or DEFAULT_INFLUX_BUCKET,
                         token=influx_token or DEFAULT_INFLUX_TOKEN or "")
        except Exception:
            pass

    return {"rows": rows_written, "duration_ms": int((time.perf_counter() - started) * 1000),
            "failed": failed}


def _cli() -> int:
    parser = argparse.ArgumentParser(description="alpha101 group builder")
    parser.add_argument("--date", required=True)
    parser.add_argument("--ts-codes", nargs="*", default=[])
    parser.add_argument("--data-root", default=DEFAULT_TS_PATH)
    parser.add_argument("--result-root", default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--influx", action="store_true")
    args = parser.parse_args()

    ts_codes = args.ts_codes
    if not ts_codes:
        from barra_cne5_data_loader import BarraCNE5DataLoader
        from alpha101.panel_loader import load_csi800_universe
        loader = BarraCNE5DataLoader(args.data_root)
        ts_codes = load_csi800_universe(loader, args.date.replace("-", ""))
    if not ts_codes:
        print("ERROR: no ts_codes", file=sys.stderr); return 2
    out = build_day(args.date, ts_codes, data_root=args.data_root,
                    result_root=args.result_root, write_influxdb=args.influx)
    print(f"Built {out['rows']} rows in {out['duration_ms']}ms; failed={list(out.get('failed', {}))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_alpha101_builder_contract.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add data-source/tushare/alpha101/builder.py Tests/Python/FactorZoo/test_alpha101_builder_contract.py
git commit -m "feat(alpha101): group build_day with per-alpha try/except + parquet+Influx output"
```

---

### Task 8: factor_worker integration + test

**Files:**
- Modify: `data-source/tushare/factor_worker.py` (add `_make_alpha101_group` + BUILDERS entry)
- Test: `Tests/Python/FactorZoo/test_factor_worker_alpha101.py`

- [ ] **Step 1: Read the current factor_worker.py BUILDERS section**

Run: `cd /home/project/hope/Lean && sed -n '210,400p' data-source/tushare/factor_worker.py`
Confirm the `_make_factorzoo_builder` pattern and the `BUILDERS` list end (around line 398).

- [ ] **Step 2: Write the worker test**

```python
# Tests/Python/FactorZoo/test_factor_worker_alpha101.py
"""alpha101 group FactorBuilder: resolver reads alpha001 dir."""
import sys, json
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq, pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "data-source" / "tushare"))
import factor_worker as fw  # noqa: E402


def test_alpha101_builder_registered():
    assert "alpha101" in [b.factor_id for b in fw.BUILDERS]

def test_alpha101_resolver_reads_alpha001(tmp_path, monkeypatch):
    d = tmp_path / "result" / "factor-zoo" / "alpha001" / "2026-07-23"
    d.mkdir(parents=True)
    (d / "000001.SZ.parquet").write_bytes(b"")
    monkeypatch.setattr(fw, "CROWDING_RESULT_ROOT", str(tmp_path / "result"))
    builder = next(b for b in fw.BUILDERS if b.factor_id == "alpha101")
    assert builder.latest_date_resolver() == "20260723"

def test_alpha101_resolver_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "CROWDING_RESULT_ROOT", str(tmp_path / "result"))
    builder = next(b for b in fw.BUILDERS if b.factor_id == "alpha101")
    assert builder.latest_date_resolver() is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_factor_worker_alpha101.py -v`
Expected: FAIL — `alpha101` not in BUILDERS.

- [ ] **Step 4: Add the alpha101 group to factor_worker.py**

Insert the factory after `_make_factorzoo_builder` (after line 263):

```python
def _make_alpha101_group():
    """One group builder: panel loaded once, all 101 alphas in one pass.

    Freshness is group-granular; alpha001 dir is the canonical reference.
    Per-alpha failures are recorded in the build summary, do NOT poison the group.
    """
    from alpha101.builder import build_day as _alpha101_build_day
    from alpha101.panel_loader import load_csi800_universe

    def _build(date_compact: str) -> dict:
        from barra_cne5_data_loader import BarraCNE5DataLoader
        loader = BarraCNE5DataLoader(TUSHARE_DATA_PATH)
        ts_codes = load_csi800_universe(loader, date_compact)
        date_yyyy_mm_dd = f"{date_compact[0:4]}-{date_compact[4:6]}-{date_compact[6:8]}"
        summary = _alpha101_build_day(
            date_yyyy_mm_dd=date_yyyy_mm_dd, ts_codes=ts_codes,
            data_root=TUSHARE_DATA_PATH, result_root=CROWDING_RESULT_ROOT,
            write_influxdb=bool(INFLUX_TOKEN),
            influx_url=INFLUX_URL, influx_org=INFLUX_ORG,
            influx_bucket=INFLUX_BUCKET, influx_token=INFLUX_TOKEN,
        )
        return {"rows": int(summary["rows"]),
                "duration_ms": int(summary["duration_ms"]),
                "failed_alphas": list(summary.get("failed", {}).keys())}

    def _resolve() -> str | None:
        root = Path(CROWDING_RESULT_ROOT) / "factor-zoo" / "alpha001"
        if not root.exists():
            return None
        best: str | None = None
        for sub in root.iterdir():
            if not sub.is_dir():
                continue
            try:
                datetime.strptime(sub.name, "%Y-%m-%d")
            except ValueError:
                continue
            compact = sub.name.replace("-", "")
            if best is None or compact > best:
                best = compact
        return best

    return _build, _resolve


_BLD_ALPHA101, _RES_ALPHA101 = _make_alpha101_group()
```

Then add to `BUILDERS` (inside the list, before the closing `]`):

```python
    # ── Alpha101 group (101 WorldQuant alphas, panel-loaded-once) ──
    FactorBuilder(
        factor_id="alpha101",
        build_callable=_BLD_ALPHA101,
        latest_date_resolver=_RES_ALPHA101,
        depends_on=("daily", "adj_factor", "daily_basic", "index_member_all"),
        max_backfill_days=60,
    ),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_factor_worker_alpha101.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the full factor_worker test suite to confirm no regressions**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_factor_worker.py -v`
Expected: PASS (existing tests monkeypatch `BUILDERS` with their own fakes).

- [ ] **Step 7: Commit**

```bash
git add data-source/tushare/factor_worker.py Tests/Python/FactorZoo/test_factor_worker_alpha101.py
git commit -m "feat(alpha101): wire alpha101 group FactorBuilder into factor_worker"
```

---

### Task 9: Catalog integration + test update

**Files:**
- Modify: `Scripts/factor_zoo/build_catalog.py` (append 101 entries)
- Modify: `Tests/Python/FactorZoo/test_build_catalog.py` (count 58→159)

- [ ] **Step 1: Read the current test_build_catalog.py count assertion**

Run: `cd /home/project/hope/Lean && grep -n "58\|len(FACTOR_METADATA\|count\|159" Tests/Python/FactorZoo/test_build_catalog.py | head`

- [ ] **Step 2: Update test_build_catalog.py**

```python
# update the count assertion (find existing count test) 58 -> 159
def test_catalog_count():
    # ... existing load ...
    assert len(entries) == 159  # was 58

def test_alpha101_entries_present():
    # ... load entries ...
    ids = {e["id"] for e in entries}
    assert "alpha042" in ids
    alpha042 = next(e for e in entries if e["id"] == "alpha042")
    assert alpha042["storage"]["path"] == "result/factor-zoo/alpha042/<date>/<ts_code>.parquet"
    assert alpha042["storage"]["influx"] == "lean_factor_alpha042"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_build_catalog.py -v`
Expected: FAIL — count is 58, not 159.

- [ ] **Step 4: Append 101 entries to build_catalog.py**

Add the helper + hints above `FACTOR_METADATA`, and append inside the list after the Barra block:

```python
ALPHA101_HINTS = {
    1: "Ts_ArgMax 反转动量", 2: "量价相关性反转", 3: "开量相关性反转", 4: "低价股时序反转",
    5: "VWAP 均值回归", 6: "开量负相关", 7: "条件动量反转", 8: "5日收益动量反转",
    9: "极值条件动量", 10: "极值条件动量(rank)", 11: "VWAP-收盘极值×量变", 12: "量变×价反转",
    13: "量价协方差反转", 14: "收益动量×开量相关", 15: "高低量相关求和反转", 16: "高低量协方差反转",
    17: "复合时序反转", 18: "日内波动+开收相关", 19: "7日趋势符号×长动量", 20: "开盘缺口反转",
    21: "均线条件反转", 22: "高低量相关变化", 23: "新高反转", 24: "百日趋势条件",
    25: "收益×量×VWAP×振幅", 26: "量价时序相关极值", 27: "量VWAP相关阈值", 28: "VWAP-收盘缩放",
    29: "嵌套rank极值", 30: "趋势符号计数×量比", 31: "复合动量反转", 32: "均线偏离+长相关",
    33: "开收比反转", 34: "波动率比+动量", 35: "量价时序反转", 36: "复合多因子",
    37: "开收延迟相关+开收", 38: "收盘时序反转", 39: "7日动量×量比衰减", 40: "高波动×高低量相关",
    41: "高低几何均值-VWAP", 42: "VWAP-收盘均值回归", 43: "量比×价反转时序", 44: "高价量相关反转",
    45: "延迟收盘相关", 46: "10日趋势阈值", 47: "量价复合", 48: "收益自相关行业中性",
    49: "10日趋势阈值", 50: "量VWAP相关极值", 51: "10日趋势阈值", 52: "低价极值×长收益",
    53: "日内位置变化", 54: "开收高低幂比", 55: "价格位置×量相关", 56: "收益动量×市值",
    57: "VWAP-收盘/衰减argmax", 58: "VWAP行业中性×量", 59: "VWAP行业中性×量", 60: "日内位置×量缩放",
    61: "VWAP极值<量相关", 62: "VWAP量相关<开高低", 63: "行业中性收盘动量", 64: "量相关<位置变化",
    65: "量相关<开盘极值", 66: "VWAP动量+日内位置", 67: "高价极值^行业中性相关", 68: "高价量相关<价变化",
    69: "行业中性VWAP动量^相关", 70: "VWAP变化^行业中性相关", 71: "复合时序最大", 72: "高低量相关/时序相关",
    73: "VWAP动量最大", 74: "量相关<高低量相关", 75: "VWAP量相关<低价量相关", 76: "VWAP动量最大",
    77: "VWAP偏离最小", 78: "量相关^VWAP量相关", 79: "行业中性变化<时序相关", 80: "行业中性符号^相关",
    81: "量相关对数<时序相关", 82: "开盘动量最小", 83: "振幅延迟×量/位置", 84: "VWAP时序幂",
    85: "量相关^时序相关", 86: "量相关<开盘收盘", 87: "VWAP动量最大", 88: "rank差最小",
    89: "量相关-行业中性VWAP", 90: "收盘极值^行业中性相关", 91: "行业中性嵌套衰减", 92: "条件rank最小",
    93: "行业中性VWAP相关/动量", 94: "VWAP极值^时序相关", 95: "开盘极值<量相关时序", 96: "量相关最大",
    97: "行业中性动量-时序相关", 98: "VWAP量相关-argmin", 99: "量相关<低价量相关", 100: "行业中性复合",
    101: "日内动量(close-open)/(high-low)",
}


def _alpha_entry(n: int, hint: str) -> dict:
    aid = f"alpha{n:03d}"
    return {"id": aid, "name": f"WorldQuant Alpha#{n}", "category": "Alpha101",
            "compute_mode": "Precomputed",
            "storage": _parquet(f"result/factor-zoo/{aid}", aid, f"lean_factor_{aid}"),
            "tushare_deps": ["daily", "adj_factor", "daily_basic", "index_member_all"],
            "selection_hint": hint, "parameters": {"n": n}}
```

Then inside `FACTOR_METADATA` (after the Barra block, before closing `]`):

```python
    # ── Alpha101 (101 WorldQuant formulaic alphas) ──
    *[_alpha_entry(n, ALPHA101_HINTS[n]) for n in range(1, 102)],
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/test_build_catalog.py -v`
Expected: PASS (count 159, alpha042 present).

- [ ] **Step 6: Commit**

```bash
git add Scripts/factor_zoo/build_catalog.py Tests/Python/FactorZoo/test_build_catalog.py
git commit -m "feat(alpha101): append 101 catalog entries (58→159) + update count test"
```

---

### Task 10: Real-data dry-run + supervisor reload

**Files:** none (verification + ops)

- [ ] **Step 1: Run the full alpha101 test suite**

Run: `cd /home/project/hope/Lean && python -m pytest Tests/Python/FactorZoo/ -k alpha101 -v`
Expected: ALL PASS.

- [ ] **Step 2: Run a real-data dry-run on one date**

Run: `cd /home/project/hope/Lean/data-source/tushare && /root/miniconda3/envs/ohmyquant/bin/python3 -m alpha101.builder --date 2026-07-23 --influx 2>&1 | tail -20`
Expected: `Built <N> rows in <ms>ms; failed=[]`. Record `duration_ms` — this is the measured perf the spec said to capture. If `failed` has many alphas, inspect each against `docs/101.md` and fix; do not proceed with broken alphas.

- [ ] **Step 3: Verify parquet output + spot-check alpha#42**

Run:
```bash
cd /home/project/hope/Lean
python3 -c "
import pandas as pd, os
p='result/factor-zoo/alpha042/2026-07-23'
files=os.listdir(p) if os.path.isdir(p) else []
print('alpha042 files:', len(files))
if files:
    df=pd.read_parquet(os.path.join(p,files[0]))
    print(df.to_string()); print('cols:', list(df.columns))
"
```
Expected: `alpha042 files: ~800`; columns `['ts_code','alpha042']`; values finite.

- [ ] **Step 4: Reload the supervisor**

Run:
```bash
supervisorctl reread
supervisorctl update factor_worker
supervisorctl restart factor_worker
supervisorctl status factor_worker
```
Expected: `factor_worker RUNNING`. (Per memory `pipeline-daemon-restart`.)

- [ ] **Step 5: Verify the worker picks up alpha101 in its next cycle**

Run:
```bash
cd /home/project/hope/Lean && tail -30 data-source/tushare/logs/factor_worker_out.log
python3 -c "import json; d=json.load(open('Results/factor-zoo/freshness.json')); print(d.get('alpha101'))"
```
Expected: log shows `Factor alpha101` fresh/building; `freshness.json` has `alpha101` entry with `last_date` and `status`.

- [ ] **Step 6: Commit if any tracked changes**

```bash
git add -A
git commit -m "chore(alpha101): dry-run verified on 2026-07-23 + supervisor reload" || echo "nothing to commit (result/ gitignored)"
```

---

## Self-Review

**1. Spec coverage:** §1 eligibility+data → Tasks 1,3. §1.2 units → Task 3. §2.1 structure → Tasks 1,2,4,7. §2.2 panel → Task 1. §2.3 operators → Task 2. §2.4 formulas + INDCLASS_LEVELS (18, per-occurrence) → Tasks 4,6. §2.4 IndNeutralize simple-mean documented → Task 2. §3.1 group FactorBuilder → Task 8. §3.2 output layout + per-alpha try/except → Task 7. §3.3 catalog → Task 9. §3.4 C# out of scope → none (correct). §4 CSI800 → Task 1. §5 daily cadence → Task 8. §6 testing (7 files) → Tasks 2,3,4,5,6,7,8,9. §7 order + step-7 timing + step-8 supervisor → Tasks 1–10. §8 non-goals respected. §9 risks mitigated (vwap test, golden test, lookback walker, per-alpha isolation).

**2. Placeholder scan:** No TBD/TODO. `ALPHA101_HINTS` has all 101 entries. All 101 `alpha_NNN` written in full. No "similar to Task N" without code.

**3. Type consistency:** `Alpha101Panel` fields match across Tasks 1,4,5,7. `INDCLASS_LEVELS` keys match `alpha_NNN`. `ALPHAS` keyed `alpha{i:03d}`. `build_day` signature matches Task 8 call site. `to_line(aid, value)` consistent. `load_csi800_universe(loader, asof_date)` consistent across Tasks 1,7,8.

Known ambiguity: alpha#73 parenthesization (`delta(blend,2.03608)/blend`) — flagged in Task 4 Step 4 for the implementer to re-verify against `docs/101.md` lines 533–535.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-26-alpha101-factor-zoo.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
