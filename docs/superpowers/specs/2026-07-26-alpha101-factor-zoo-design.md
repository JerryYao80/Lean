# Alpha101 Factor Zoo Integration — Design

**Date:** 2026-07-26
**Branch:** `fix/price-scaling-10000x`
**Scope:** Implement WorldQuant "101 Formulaic Alphas" (Kakushadze 2015) as factors in the LEAN factor zoo, gated by `tushare_data_v2` data support, and wire them into the `factor_worker` supervisor for daily incremental updates.

---

## 1. Goal & Eligibility Rule

**Instruction:** *"读取docs/101.md，实现alpha1到alpha101这101个因子，只有tushare_data_v2中有数据支持，才将其加入到因子动物园，并加入到factor_worker 的supervisor任务做因子更新。"*

**Eligibility rule (operationalized):** An alpha is eligible iff every input it requires is either (a) stored as a parquet table in `tushare_data_v2`, or (b) mechanically derivable from stored raws. A name missing a date's data at compute time is a data-quality drop (that stock's alpha value is NaN, skipped) — not grounds to disqualify the whole alpha.

### 1.1 Data-Availability Verdict (from `tushare_data_v2` probe)

| Alpha-input | Available? | Source / derivation |
|---|---|---|
| open, high, low, close | YES | `daily.{open,high,low,close}` × `adj_factor.adj_factor` (adjusted prices) |
| volume | YES | `daily.vol` (units: 手 = 100 shares; unadjusted) |
| returns | DERIVABLE | `adj_close.pct_change()` |
| **vwap** | DERIVABLE | `daily.amount * 10 / daily.vol` (full history). Verified equivalent to `bak_daily.avg_price` (2020+). |
| cap | YES | `daily_basic.total_mv` (万元) |
| adv{5,10,15,20,30,40,50,60,81,120,150,180} | DERIVABLE | `daily.amount.rolling(N).mean()` (千元) — no precomputed field |
| IndClass.sector/.industry/.subindustry | YES | `index_member_all` (Shenwan L1/L2/L3): sector=L1, industry=L2, subindustry=L3 |

**Verdict: all 101 alphas are eligible.** The set of ineligible alphas is ∅. The only subscription-blocked APIs (`stk_mins` intraday, `stk_factor_pro`) are NOT needed for alpha101.

### 1.2 Unit Convention (critical on `fix/price-scaling-10000x`)

Tushare unit traps, documented and pinned by tests:
- `daily.amount` = 千元 (RMB ÷ 1000); `daily.vol` = 手 (shares ÷ 100).
- `bak_daily.amount` = 元 (raw RMB); `bak_daily.avg_price` = 元/share; `bak_daily.vol` = 手.
- **Derived vwap** = `daily.amount * 10 / daily.vol` (NOT `amount/vol`, NOT `amount/vol/100`). Regression-check: `|derived_vwap − bak_daily.avg_price| < 0.02` for 2020–2026 dates.
- `adv{N}` = `daily.amount.rolling(N).mean()` (千元). Only used inside `correlation`/`rank` denominators, so absolute units are irrelevant.
- `cap` = `daily_basic.total_mv` (万元). Only used in alpha#56 `returns * cap` under `rank`, so unit scale is irrelevant.

---

## 2. Architecture

### 2.1 Structure

```
data-source/tushare/
  alpha101/
    __init__.py
    operators.py        ← 15 vectorized primitives on date×ts_code wide DataFrames
    formulas.py         ← 101 declarative alpha functions alpha_001..alpha_101 + ALPHAS registry
    panel_loader.py     ← loads daily+adj+daily_basic+index_member_all → Alpha101Panel
    builder.py          ← build_day(): load panel once, eval all 101, write parquet+Influx
  factor_worker.py      ← one group FactorBuilder "alpha101" wired into BUILDERS
```

**Three tightly-coupled components** — not 101 separate builders (reloads panel 101×), not a string DSL parser (over-engineered). Each alpha is a short pure Python function (the paper's intent: "formulas that are also code").

### 2.2 Alpha101Panel (`panel_loader.py`)

`load_panel(ts_codes, asof_compact, data_root, lookback_days=260) -> Alpha101Panel`

Plain object with wide-DataFrame views (index=trade_date YYYYMMDD string, columns=ts_code):

| Field | Type | Source |
|---|---|---|
| `open, high, low, close` | DataFrame | `daily` × `adj_factor` (adjusted) |
| `close_raw` | DataFrame | unadjusted close (alpha#41 `sqrt(high*low)` needs raw — adjustment distorts the product) |
| `volume` | DataFrame | `daily.vol` (手, unadjusted) |
| `vwap` | DataFrame | derived `amount*10/vol` |
| `returns` | DataFrame | `adj_close.pct_change()` |
| `cap` | DataFrame | `daily_basic.total_mv` |
| `adv` | `dict[int, DataFrame]` | `daily.amount.rolling(N).mean()` for N ∈ {5,10,15,20,30,40,50,60,81,120,150,180} |
| `industry` | DataFrame | L1/L2/L3 code per date×ts_code, PIT via `index_member_all` in_date/out_date |

Tail window trimmed to `max(250, N)` days ending at `asof` (alpha#32/37 use 230-day correlation; alpha#19/39 use 250-day sum).

### 2.3 Operators (`operators.py`) — Contract

All operators act on wide DataFrames and return wide DataFrames. Non-integer `d` floored (`ts_{O}(x,d)` → `floor(d)` per the paper).

| Operator | Semantics |
|---|---|
| `rank(x)` | cross-sectional per-row percentile rank: `x.rank(axis=1, pct=True)` |
| `delay(x, d)` | `x.shift(d)` |
| `delta(x, d)` | `x - x.shift(d)` |
| `correlation(x, y, d)` | rolling Pearson: `x.rolling(d).corr(y)` |
| `covariance(x, y, d)` | `x.rolling(d).cov(y)` |
| `scale(x, a=1)` | rescale per row so `sum(abs(x))=a`: `x.div(x.abs().sum(axis=1), axis=0)*a` |
| `signedpower(x, a)` | `x ** a` elementwise |
| `decay_linear(x, d)` | weighted MA with weights d,d-1,…,1 rescaled to sum 1 |
| `ts_min/ts_max(x, d)` | `x.rolling(d).min()/max()` |
| `ts_argmax/ts_argmin(x, d)` | day-offset of max/min in trailing d days |
| `ts_rank(x, d)` | `x.rolling(d).rank(pct=True)` |
| `sum/product/stddev(x, d)` | rolling |
| `min/max(x, d)` | aliases of `ts_min/ts_max` |
| `indneutralize(x, industry_level_str)` | within-group demean per date: `x - x.groupby(industry_row, axis=1).transform('mean')` |
| `abs/log/sign` | elementwise; `log` on `x.where(x>0)`, NaN elsewhere |

Boolean/ternary `> < == || ?:` expressed via Python native operators inside formula functions (formulas are real code, not strings) — `np.where` for ternary.

### 2.4 Formulas (`formulas.py`)

101 pure functions, 1–8 lines each. Registry:

```python
ALPHAS: dict[str, Callable[[Alpha101Panel], pd.DataFrame]] = {
    "alpha001": alpha_001, ..., "alpha101": alpha_101,
}
```

Examples:
```python
def alpha_001(p):
    base = signedpower(np.where(p.returns < 0, stddev(p.returns, 20), p.close), 2.0)
    return rank(ts_argmax(base, 5)) - 0.5

def alpha_042(p):
    return rank(p.vwap - p.close) / rank(p.vwap + p.close)

def alpha_101(p):
    return (p.close - p.open) / ((p.high - p.low) + 1e-3)
```

For alphas requiring `IndNeutralize` (alpha#48, #58, #63, #79, #80, #100, …): function calls `indneutralize(x, "subindustry")`. **Strict membership handling** (per user choice): a stock with no L1/L2/L3 mapping on a date is excluded from its group's mean and its alpha value is NaN. `to_line` and parquet writes already filter NaN.

---

## 3. Builder & Worker Integration

### 3.1 Group FactorBuilder

One group `FactorBuilder(factor_id="alpha101")` loads the panel once and evaluates all 101 formulas in one pass. Added to `BUILDERS` in `factor_worker.py`:

```python
FactorBuilder(
    factor_id="alpha101",
    build_callable=_BLD_ALPHA101,
    latest_date_resolver=_RES_ALPHA101,
    depends_on=("daily", "adj_factor", "daily_basic", "index_member_all"),
    max_backfill_days=60,
),
```

`_BLD_ALPHA101` resolves the CSI800 universe (`000300.SH` ∪ `000905.SH`, deduped) via `BarraCNE5DataLoader.load_index_constituents`, then calls `alpha101.builder.build_day(...)`. `_RES_ALPHA101` scans `result/factor-zoo/alpha001/` for the max `yyyy-MM-dd` subdir — `alpha001` is the canonical reference; its max date is the group's freshness date (group-granularity freshness, confirmed in design §1).

### 3.2 Output Layout

For each alpha `alphaNNN`:
- **Parquet:** `result/factor-zoo/alphaNNN/<date>/<ts_code>.parquet`, column `alphaNNN` (single scalar). Identical to Phase-5 template.
- **Influx:** measurement `lean_factor_alphaNNN`, tags `ts_code,trade_date`, field `alphaNNN`. Timestamp = trade_date 15:00 Shanghai → UTC ns.

**Per-alpha try/except:** if `alpha_055` raises, it is logged, its output skipped, the other 100 still write. Failed alpha recorded in state file as `alphaNNN_failed: <error>` (visible in Grafana) but does NOT poison the group.

### 3.3 Catalog Integration (`build_catalog.py`)

Append 101 entries via a helper:
```python
def _alpha_entry(n, hint):
    aid = f"alpha{n:03d}"
    return {"id": aid, "name": f"WorldQuant Alpha#{n}", "category": "Alpha101",
            "compute_mode": "Precomputed",
            "storage": _parquet(f"result/factor-zoo/{aid}", aid, f"lean_factor_{aid}"),
            "tushare_deps": ["daily", "adj_factor", "daily_basic", "index_member_all"],
            "selection_hint": hint, "parameters": {"n": n}}

ALPHA101_HINTS = {1: "Ts_ArgMax 反转动量", ..., 101: "日内动量(close-open)/(high-low)"}
FACTOR_METADATA += [_alpha_entry(n, ALPHA101_HINTS[n]) for n in range(1, 102)]
```
Catalog grows 58 → 159 factors. `hypothesize.py` and `bayesian_optimizer.py` pick them up automatically (iterate `FACTOR_METADATA`).

### 3.4 C# Read-Side — Out of Scope

Phase-5 factors have no `RParquetAdapter` registered (Python-only consumption). Per memory `never-modify-existing-features`, and no C# strategy touches alpha101 in this PR. If a future C# strategy needs it, that's a separate PR registering `RParquetAdapter("factor-zoo/alpha001", "alpha001")` in `FactorStoreConfig.RegisterDefaults`.

---

## 4. Universe

CSI800 = `000300.SH` ∪ `000905.SH` constituents, deduped. Helper `load_csi800_universe(loader, asof_date)`. The existing CSI300 builders (ivol_20d etc.) are untouched.

---

## 5. Cadence

Daily, like other price-volume builders (`max_backfill_days=60`). Matches the paper's 0.6–6.4 day holding period and the existing Phase-5 daily builders.

---

## 6. Testing Plan — `Tests/Python/FactorZoo/test_alpha101*.py` (new)

Hermetic, `tmp_path`, synthetic parquet — following the existing `test_factor_worker.py` pattern.

| File | Coverage |
|---|---|
| `test_alpha101_operators.py` | Each operator on known inputs (rank on [10,20,30]→[1/3,2/3,1]; decay_linear on constant→that constant; indneutralize on 2 groups→group-mean-subtracted-to-zero). ~40 assertions. |
| `test_alpha101_vwap_units.py` | Synthetic `daily` parquet (amount=1200, vol=12 → vwap=100); regression vs `bak_daily.avg_price` on post-2020 date (skip if `bak_daily` absent from fixture). |
| `test_alpha101_formulas_smoke.py` | All 101 alphas evaluate on a 20-stock × 260-day synthetic panel, no exception, wide output, ≥1 non-NaN value. Parametrized per alpha. |
| `test_alpha101_builder_contract.py` | `build_day` writes parquet to `result/factor-zoo/alphaNNN/<date>/<ts_code>.parquet` with correct column; Influx write intercepted by monkeypatched `write_influx`, line-protocol format asserted. |
| `test_factor_worker_alpha101.py` | `alpha101` group FactorBuilder in the existing hermetic worker pattern: resolver reads `alpha001` dir; builder queues under dry-run; per-alpha failure does NOT poison the group. |
| `test_build_catalog.py` (update existing) | Catalog count becomes 159; `alpha042` entry present with correct storage path. |

---

## 7. Implementation Order (TDD)

1. `operators.py` + `test_alpha101_operators.py` (red first) → implement operator-by-operator to green.
2. `panel_loader.py` + `test_alpha101_vwap_units.py`.
3. `formulas.py` + `test_alpha101_formulas_smoke.py` (all 101 parametrized).
4. `builder.py` + `test_alpha101_builder_contract.py`.
5. `factor_worker.py` integration + `test_factor_worker_alpha101.py`.
6. `build_catalog.py` append + update `test_build_catalog.py` (count → 159).
7. Real-data dry-run: `python -m alpha101.builder --date 2026-07-23` on one date, verify parquet output + Influx write; spot-check alpha#42 sign matches `rank(vwap-close)`.
8. Supervisor reload: `supervisorctl reread && supervisorctl update factor_worker` (per memory — must restart after editing pipeline scripts).

---

## 8. Non-Goals

- No C# read-side adapter registration (Python-only consumption this PR).
- No backtest strategy consuming the factors (separate work).
- No modification of existing factor_zoo builders or catalog entries (memory: `never-modify-existing-features`).
- No intraday vwap (`stk_mins` is subscription-blocked); daily `amount/(vol)` proxy is the data we have.
- No new data downloads — every input is already in `tushare_data_v2`.

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Unit-scaling bug (the `fix/price-scaling-10000x` class of bug) | `test_alpha101_vwap_units.py` pins `amount*10/vol`; vwap regression-checked vs `bak_daily.avg_price`. |
| One buggy formula breaks the batch | Per-alpha `try/except`; failed alpha logged, group not poisoned. |
| `indneutralize` group has 1 member → NaN | Documented: 1-member group demean → 0, not NaN (only missing-membership → NaN). Tested. |
| 101× compute cost on CSI800 | Panel loaded once; rolling ops vectorized in pandas. ~800 stocks × 260 days × 101 formulas ≈ tractable (sub-minute/day expected). |
| Group-granularity freshness hides a per-alpha gap | Failed alphas recorded in state file as `alphaNNN_failed`; visible in Grafana freshness panel. |
