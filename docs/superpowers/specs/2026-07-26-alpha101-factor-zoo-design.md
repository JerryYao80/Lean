# Alpha101 Factor Zoo Integration — Design

**Date:** 2026-07-26
**Branch:** `fix/price-scaling-10000x` (current). **Note:** this is a pure-additive feature PR (101 new factors + supervisor wiring, no edits to existing factor_zoo code). Per review feedback, implementation should move to a dedicated `feat/alpha101-factor-zoo` branch before merge so the feature diff isn't entangled with any in-flight bug fix on the current branch. The spec/plan commits can land on either; the implementation commits land on the feature branch.
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

Tail window trimmed to the per-alpha required lookback, NOT a single global constant. The required lookback is **sum-of-nested-windows**: for an expression like `correlation(delay(open-close,1), adv{180}, 5)`, the panel must span `180 (inner rolling adv) + 1 (delay) + 5 (outer rolling corr) = 186` trading days ending at `asof`. `panel_loader.load_panel` takes the **max across all 101 formulas' statically-computed lookbacks** so one shared panel serves all alphas. The per-formula lookback is computed by a static walker over the formula AST (operators declare their window cost: `delay/delta` = +d, `ts_*`/`sum`/`correlation`/`stddev`/`decay_linear` = +d, `rank`/`scale`/`signedpower`/`abs`/`log`/`sign` = +0, nested → sum). The max across alpha#1..101 is ~262 days (driven by alpha#32's 230-day correlation + delta, and alpha#19/39's 250-day sum) — `lookback_days=270` covers it with margin. `test_alpha101_lookback.py` asserts each formula's computed lookback ≥ the sum of its nested window literals, so an under-sized panel never silently produces warm-up NaNs that look like "data quality is bad."

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

For alphas requiring `IndNeutralize` (18 of 101 — see table below): each formula MUST call `indneutralize(x, <exact_level>)` using the **exact `IndClass` level named in the paper for that occurrence**, NOT a single default. A single alpha can use multiple levels in the same formula (e.g. alpha#67 uses both `sector` and `subindustry`), so a per-occurrence table is a required deliverable, not scattered string literals. **Strict membership handling** (per user choice): a stock with no L1/L2/L3 mapping on a date is excluded from its group's mean and its alpha value is NaN. `to_line` and parquet writes already filter NaN.

#### IndClass.level table (required deliverable — verified against docs/101.md Appendix A)

| Alpha | IndClass level(s) per occurrence | Formula motif |
|---|---|---|
| 48 | subindustry | `indneutralize((corr * delta)/close, subindustry)` |
| 58 | sector | `IndNeutralize(vwap, sector)` |
| 59 | industry | `IndNeutralize(blend(vwap), industry)` |
| 63 | industry | `IndNeutralize(close, industry)` |
| 67 | **sector + subindustry** | `IndNeutralize(vwap, sector)` ∧ `IndNeutralize(adv20, subindustry)` |
| 69 | industry | `IndNeutralize(vwap, industry)` |
| 70 | industry | `IndNeutralize(close, industry)` |
| 76 | sector | `IndNeutralize(low, sector)` |
| 79 | sector | `IndNeutralize(blend(close,open), sector)` |
| 80 | industry | `IndNeutralize(blend(open,high), industry)` |
| 82 | sector | `IndNeutralize(volume, sector)` |
| 87 | industry | `IndNeutralize(adv81, industry)` |
| 89 | industry | `IndNeutralize(vwap, industry)` |
| 90 | subindustry | `IndNeutralize(adv40, subindustry)` |
| 91 | industry | `IndNeutralize(close, industry)` |
| 93 | industry | `IndNeutralize(vwap, industry)` |
| 97 | industry | `IndNeutralize(blend(low,vwap), industry)` |
| 100 | subindustry (×2 nested calls) | `indneutralize(indneutralize(..., subindustry), subindustry)` |

This table is encoded as a module-level dict in `formulas.py`:
```python
INDCLASS_LEVELS: dict[int, list[str]] = {
    48: ["subindustry"], 58: ["sector"], 59: ["industry"], 63: ["industry"],
    67: ["sector", "subindustry"],  # two occurrences, two levels
    69: ["industry"], 70: ["industry"], 76: ["sector"], 79: ["sector"],
    80: ["industry"], 82: ["sector"], 87: ["industry"], 89: ["industry"],
    90: ["subindustry"], 91: ["industry"], 93: ["industry"], 97: ["industry"],
    100: ["subindustry", "subindustry"],  # two nested indneutralize calls
}
```
A unit test (`test_alpha101_indclass_levels.py`) asserts this dict matches the levels actually passed to `indneutralize` in every listed formula — guarding against silent regressions.

#### IndNeutralize weighting — documented choice

The paper's `IndNeutralize` admits two community implementations: simple arithmetic mean vs cap-weighted mean within each group. **This PR uses simple arithmetic mean** (`groupby(...).transform('mean')`). This is a known, documented choice — not the only valid one. The weighting is pinned by `test_alpha101_operators.py` so a later switch to cap-weighted would be a deliberate, tested change.

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

**Per-alpha try/except:** if `alpha_055` raises, it is logged, its output skipped, the other 100 still write. Failed alpha recorded in state file as `alphaNNN_failed: <error>` (visible in Grafana) but does NOT poison the group. **Failure escalation:** a consecutive-failure counter per alpha (`alphaNNN_fail_count`) increments on each failed date and resets on success; at a threshold (default: 5 consecutive trading days) the alpha is marked `alphaNNN_degraded` and an explicit warning line is written to the worker's stderr log + the `lean_factor_freshness` Influx measurement gets a `degraded` status tag. This prevents a structurally-broken industry feed from silently dropping a factor indefinitely. **Downstream contract:** `hypothesize.py` and `bayesian_optimizer.py` must tolerate a factor's parquet being absent for a date (they already iterate `FACTOR_METADATA` defensively — this PR adds a `conftest`-level fixture asserting a missing `alphaNNN/<date>/` dir does not raise in either consumer, documented as the "factor partial-absence tolerance" guarantee).

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
| `test_alpha101_formulas_smoke.py` | All 101 alphas evaluate on a 20-stock × 260-day synthetic panel, no exception, wide output, ≥1 non-NaN value. Parametrized per alpha. **Smoke only — does NOT prove numerical correctness** (see golden-value test below). |
| `test_alpha101_golden_values.py` | **Golden-value regression assertions for 15 representative alphas**, each covering a distinct operator family. Hand-constructed small deterministic inputs (≤5 stocks × ≤20 days) with **expected numeric outputs computed by hand / cross-checked against a community alpha101 reference implementation** (e.g. `krzang/alphas`, `yli188/alpha101_stock_notebooks` style public repos). Asserts exact values (within `1e-9`), NOT "non-NaN". Covers: ts_rank (alpha#4), decay_linear (alpha#57), covariance (alpha#13), indneutralize (alpha#48 subindustry + alpha#58 sector — also validates the per-alpha IndClass table), ternary (alpha#7), signedpower (alpha#84), ts_argmax (alpha#1), correlation (alpha#2), product (alpha#29), scale (alpha#28), nested decay+corr (alpha#91), vwap-close mean-reversion (alpha#42), the multi-level alpha#67, and alpha#101 (the simplest, as a sanity anchor). **This is the test that catches "runs but wrong" bugs** — the exact class the `fix/price-scaling-10000x` branch was created to fix. |
| `test_alpha101_indclass_levels.py` | Asserts `INDCLASS_LEVELS` dict matches the `indneutralize(..., level)` calls actually present in each listed formula (AST or call inspection), so a future edit can't silently change a level. |
| `test_alpha101_lookback.py` | Static walker computes each formula's required lookback as sum-of-nested-windows (delay/delta/ts_*/correlation/sum/stddev/decay_linear add their `d`; rank/scale/signedpower/abs/log/sign add 0; nesting sums). Asserts every formula's computed lookback ≥ sum of its window literals, and that `panel_loader.load_panel`'s default `lookback_days=270` ≥ the max across all 101. Guards against silent warm-up NaNs. |
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
- **No factor selection / de-correlation.** The WorldQuant 101 alphas are known to be mutually correlated (paper median pairwise corr 14.3%), and several are also correlated with the existing 58 zoo factors (e.g. alpha#4 ≈ short-term reversal). Selecting, deduping, or decorrelating the 159-factor catalog is explicitly out of scope — `hypothesize.py`/`bayesian_optimizer.py` consume the full catalog and let the optimizer's factor-include params handle selection. Naming this so a future reviewer doesn't read "101 factors added" as "101 should all be traded."
- **No cross-day panel consistency guarantee.** Each trade date is an independent cross-sectional recompute (standard for alpha101-style factors); a stock newly added to CSI800 uses its own historical price series for its trailing window, which is correct by construction. This is documented to prevent the misread that these are panel-consistent time series.

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Unit-scaling bug (the `fix/price-scaling-10000x` class of bug) | `test_alpha101_vwap_units.py` pins `amount*10/vol`; vwap regression-checked vs `bak_daily.avg_price`. |
| One buggy formula breaks the batch | Per-alpha `try/except`; failed alpha logged, group not poisoned. |
| `indneutralize` group has 1 member → NaN | Documented: 1-member group demean → 0, not NaN (only missing-membership → NaN). Tested. |
| 101× compute cost on CSI800 | Panel loaded once; rolling ops vectorized in pandas. **Real measured timing recorded in the step-7 dry-run** (not an estimate) — if wall-clock exceeds target, the design adds a memoization layer over common cross-formula subexpressions (`rank(volume)`, `adv{20}`, etc. recur across many alphas) via `functools.lru_cache` keyed on the subexpression + date range. The cache is opt-in and added only if measured cost justifies it — no speculative complexity. |
| Group-granularity freshness hides a per-alpha gap | Failed alphas recorded in state file as `alphaNNN_failed`; visible in Grafana freshness panel. |
