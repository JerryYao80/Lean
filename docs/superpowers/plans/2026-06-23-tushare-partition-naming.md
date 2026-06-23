# tushare_data Partition Naming Normalization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Normalize tushare_data partition naming (one strategy per table, `date=`→`trade_date=`), migrate the existing ~18G into a clean `tushare_data_v2/` directory without re-downloading, gap-fill stale tables to 20260623, and cut the supervisor-managed `tushare_worker` over to the new directory.

**Architecture:** Local-only parquet migration into a parallel `tushare_data_v2/` directory (zero tushare API calls). The writer's `_get_file_path()` renames the DATE-strategy partition key `date=`→`trade_date=` and validates 8-digit dates. Old `tushare_data/` is kept read-only as rollback. Cutover uses `supervisorctl stop/start tushare_worker` (never `kill`). Gap-fill reuses the existing `incremental_update.py --api ... --target-date` CLI.

**Tech Stack:** Python 3 (`/root/miniconda3/envs/ohmyquant/bin/python3`), pandas + pyarrow parquet, pytest, supervisor, bash.

**Spec:** `docs/superpowers/specs/2026-06-23-tushare-partition-naming-design.md`

**Repo layout for this plan:**
- `/home/project/tushare-downloader/` — the deployed "trimmed" scripts; `tushare_worker` runs here; **all code + data live here**
- `/home/project/hope/Lean/data-source/tushare/` — the "full" mirror copy; byte-identical core; **must receive the same edits**
- `/home/project/hope/Lean/` — the git repo where this plan is committed; the spec lives under its `docs/`

**Two non-negotiable rules from the spec (hard constraints):**
1. **No re-download.** The migration script does zero tushare API calls — it only reads existing parquet and writes to the new directory.
2. **Downloads run only in supervisor `tushare_worker`.** Never start a long-running download from the Claude Code CLI. Gap-fill is a one-shot CLI invocation of `incremental_update.py`, not a daemon.

---

## File Structure

**Created:**
- `/home/project/tushare-downloader/migrate_to_v2.py` — one-shot local migration script (read old parquet → write `tushare_data_v2/` with normalized keys; per-table row/date validation; rollback on mismatch)
- `/home/project/tushare-downloader/tests/__init__.py` — empty
- `/home/project/tushare-downloader/tests/test_file_path.py` — unit tests for `_get_file_path` partition key + 8-digit validation
- `/home/project/tushare-downloader/tests/test_migrate_v2.py` — unit tests for the migration mapping logic
- `/home/project/tushare-downloader/tests/test_get_stock_list.py` — regression test for the existing prefix filter
- `/home/project/tushare-downloader/tests/conftest.py` — tiny temp-data fixtures
- `/home/project/tushare-downloader/pytest.ini`

**Modified (both copies must stay in sync):**
- `downloader.py:107-136 _get_file_path()` — rename DATE key `date=`→`trade_date=`; validate `date` is 8-digit, raise on non-date
- `config.py:20 DATA_DIR` — switch value at cutover time (last step, by operator)
- `analysis/06_stock_potential_analysis.py:39` and `analysis/07_hs300_potential_duckdb.py:48` — read path `daily/date={ts_code}` → `daily/ts_code={ts_code}` (the residue they read is deleted by migration)

**NOT modified (verified already correct):**
- `convert_to_lean.py:329 get_stock_list()` — ALREADY filters `entry.startswith("ts_code=")`. No change. The spec's "fix prefix filter" item is already done; a regression test is added instead (Task 8).
- `incremental_update.py` — call sites use `_get_file_path(date=trade_date)` (param name), unaffected by the key rename. No `date=*` globs exist (progress is in the state file).

---

## Task 1: Set up the test harness

**Files:**
- Create: `/home/project/tushare-downloader/tests/__init__.py`
- Create: `/home/project/tushare-downloader/tests/conftest.py`
- Create: `/home/project/tushare-downloader/pytest.ini`

- [ ] **Step 1: Create the tests package and conftest**

`tests/__init__.py`: (empty file)

`tests/conftest.py`:
```python
import sys
from pathlib import Path

# Make the parent dir (tushare-downloader) importable so `import downloader` works.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path):
    """A fresh DATA_DIR for building tiny partition trees in tests."""
    return tmp_path
```

`/home/project/tushare-downloader/pytest.ini`:
```ini
[pytest]
testpaths = tests
python_files = test_*.py
```

- [ ] **Step 2: Verify pytest runs (0 tests collected is fine)**

Run: `cd /home/project/tushare-downloader && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest -q`
Expected: `no tests ran` (exit 5) or `0 passed` — no import/collection errors.

- [ ] **Step 3: Commit (if tushare-downloader is a git repo)**

```bash
cd /home/project/tushare-downloader
git rev-parse --is-inside-work-tree 2>/dev/null && \
  git add tests/ pytest.ini && \
  git commit -m "test: add pytest harness for tushare-downloader" || \
  echo "tushare-downloader is not a git repo; changes tracked via the Lean plan"
```

---

## Task 2: TDD — `_get_file_path` DATE key rename + 8-digit validation

**Files:**
- Create: `/home/project/tushare-downloader/tests/test_file_path.py`
- Modify: `/home/project/tushare-downloader/downloader.py:107-136`

**Context:** `_get_file_path` is a method on `TushareDownloader` that only needs `self.data_dir`. The test bypasses `__init__` (avoids network/config) and sets `data_dir` directly.

- [ ] **Step 1: Write the failing test**

`tests/test_file_path.py`:
```python
import pytest
from downloader import TushareDownloader


def _make_downloader(tmp_data_dir):
    d = TushareDownloader.__new__(TushareDownloader)
    d.data_dir = tmp_data_dir
    return d


def test_stock_strategy_uses_ts_code_key(tmp_data_dir):
    d = _make_downloader(tmp_data_dir)
    p = d._get_file_path("daily", ts_code="600519.SH")
    assert p == tmp_data_dir / "daily" / "ts_code=600519.SH" / "data.parquet"


def test_date_strategy_uses_trade_date_key(tmp_data_dir):
    """DATE-strategy tables must write trade_date= (NOT date=)."""
    d = _make_downloader(tmp_data_dir)
    p = d._get_file_path("index_weight", date="20260623")
    assert p == tmp_data_dir / "index_weight" / "trade_date=20260623" / "data.parquet"


def test_year_strategy_unchanged(tmp_data_dir):
    d = _make_downloader(tmp_data_dir)
    p = d._get_file_path("hk_daily", year=2025)
    assert p == tmp_data_dir / "hk_daily" / "year=2025" / "data.parquet"


def test_quarter_strategy_unchanged(tmp_data_dir):
    d = _make_downloader(tmp_data_dir)
    p = d._get_file_path("fund_portfolio", quarter="2025Q1")
    assert p == tmp_data_dir / "fund_portfolio" / "quarter=2025Q1" / "data.parquet"


def test_none_strategy_root_file(tmp_data_dir):
    d = _make_downloader(tmp_data_dir)
    p = d._get_file_path("trade_cal")
    assert p == tmp_data_dir / "trade_cal" / "data.parquet"


def test_date_rejects_non_8_digit_symbol(tmp_data_dir):
    """A symbol passed as `date` (the old residue bug) must raise, not write date=<symbol>."""
    d = _make_downloader(tmp_data_dir)
    with pytest.raises(ValueError):
        d._get_file_path("daily", date="920992.BJ")


def test_date_rejects_short_date(tmp_data_dir):
    d = _make_downloader(tmp_data_dir)
    with pytest.raises(ValueError):
        d._get_file_path("index_weight", date="2026062")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/tushare-downloader && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest tests/test_file_path.py -v`
Expected: FAIL — `test_date_strategy_uses_trade_date_key` (currently produces `date=20260623`) and the two rejection tests (currently no validation).

- [ ] **Step 3: Implement the change in `_get_file_path`**

In `/home/project/tushare-downloader/downloader.py`, replace the body of `_get_file_path` (lines ~107-136) with:

```python
    def _get_file_path(
        self,
        api_name: str,
        year: Optional[int] = None,
        quarter: Optional[str] = None,
        date: Optional[str] = None,
        ts_code: Optional[str] = None
    ) -> Path:
        """
        获取数据文件路径

        按照分区目录结构存储:
        - 无分块: {data_dir}/{api_name}/data.parquet
        - 按年: {data_dir}/{api_name}/year={year}/data.parquet
        - 按季度: {data_dir}/{api_name}/quarter={quarter}/data.parquet
        - 按交易日: {data_dir}/{api_name}/trade_date={date}/data.parquet
        - 按代码: {data_dir}/{api_name}/ts_code={ts_code}/data.parquet

        Note: DATE-strategy partitions use the `trade_date=` key (not `date=`) to
        disambiguate from historical residue where `date=<symbol>` directories were
        created by an old call pattern. `date` must be an 8-digit YYYYMMDD string.
        """
        base_path = self.data_dir / api_name

        if ts_code is not None:
            return base_path / f"ts_code={ts_code}" / "data.parquet"
        elif year is not None:
            return base_path / f"year={year}" / "data.parquet"
        elif quarter is not None:
            return base_path / f"quarter={quarter}" / "data.parquet"
        elif date is not None:
            if not (isinstance(date, str) and len(date) == 8 and date.isdigit()):
                raise ValueError(
                    f"date must be an 8-digit YYYYMMDD string, got: {date!r}"
                )
            return base_path / f"trade_date={date}" / "data.parquet"
        else:
            return base_path / "data.parquet"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/project/tushare-downloader && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest tests/test_file_path.py -v`
Expected: 7 passed.

- [ ] **Step 5: Mirror the change to the Lean copy**

Apply the identical edit to `/home/project/hope/Lean/data-source/tushare/downloader.py` `_get_file_path`.

Verify the two functions match:
Run: `diff <(sed -n '/def _get_file_path/,/return base_path .. "data.parquet"/p' /home/project/tushare-downloader/downloader.py) <(sed -n '/def _get_file_path/,/return base_path .. "data.parquet"/p' /home/project/hope/Lean/data-source/tushare/downloader.py)`
Expected: no output (identical).

- [ ] **Step 6: Commit**

```bash
cd /home/project/tushare-downloader
git add downloader.py tests/test_file_path.py && \
  git commit -m "feat: _get_file_path uses trade_date= key + 8-digit validation" || \
  echo "not a git repo; tracked via Lean plan"
cd /home/project/hope/Lean
git add data-source/tushare/downloader.py
git commit -m "feat: mirror _get_file_path trade_date= change from tushare-downloader

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: TDD — migration mapping helpers (`classify_partition` + `target_rel_path`)

The migration script is big; test its pure helpers first, then I/O.

**Files:**
- Create: `/home/project/tushare-downloader/tests/test_migrate_v2.py`
- Create: `/home/project/tushare-downloader/migrate_to_v2.py` (helpers only this task)

- [ ] **Step 1: Write the failing test for the helpers**

`tests/test_migrate_v2.py`:
```python
import importlib.util
from pathlib import Path

import pytest


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "migrate_to_v2",
        Path(__file__).resolve().parent.parent / "migrate_to_v2.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mig():
    return _load_module()


def test_classify_stock(mig):
    assert mig.classify_partition("ts_code=600519.SH") == ("ts_code", "600519.SH")


def test_classify_year(mig):
    assert mig.classify_partition("year=2025") == ("year", "2025")


def test_classify_quarter(mig):
    assert mig.classify_partition("quarter=2025Q1") == ("quarter", "2025Q1")


def test_classify_real_date(mig):
    assert mig.classify_partition("date=20260623") == ("date", "20260623")


def test_classify_residue_date_symbol(mig):
    """date=<symbol> is residue; classify so the caller can skip it."""
    assert mig.classify_partition("date=920992.BJ") == ("residue_date", "920992.BJ")


def test_target_rel_path_stock(mig):
    assert mig.target_rel_path("ts_code", "600519.SH") == "ts_code=600519.SH/data.parquet"


def test_target_rel_path_date_real(mig):
    """Real date -> renamed to trade_date=."""
    assert mig.target_rel_path("date", "20260623") == "trade_date=20260623/data.parquet"


def test_target_rel_path_year_quarter_none(mig):
    assert mig.target_rel_path("year", "2025") == "year=2025/data.parquet"
    assert mig.target_rel_path("quarter", "2025Q1") == "quarter=2025Q1/data.parquet"
    assert mig.target_rel_path("none", "") == "data.parquet"


def test_target_rel_path_rejects_residue(mig):
    """Residue date=<symbol> must raise rather than silently copy."""
    with pytest.raises(ValueError):
        mig.target_rel_path("residue_date", "920992.BJ")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/tushare-downloader && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest tests/test_migrate_v2.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'migrate_to_v2'`.

- [ ] **Step 3: Create `migrate_to_v2.py` with helpers only**

`/home/project/tushare-downloader/migrate_to_v2.py`:
```python
"""
One-shot local migration: tushare_data/  ->  tushare_data_v2/

ZERO tushare API calls. Reads existing parquet and writes to the new directory
with normalized partition keys. Per-table validation; rollback on mismatch.

See docs/superpowers/specs/2026-06-23-tushare-partition-naming-design.md
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple

DATE_8_RE = re.compile(r"^\d{8}$")

SRC_DIR = Path("/home/project/tushare-downloader/tushare_data")
DST_DIR = Path("/home/project/tushare-downloader/tushare_data_v2")


def classify_partition(dir_name: str) -> Tuple[str, str]:
    """Classify a partition directory name into (kind, value).

    kinds: 'ts_code' | 'year' | 'quarter' | 'date' (real 8-digit) |
           'residue_date' (date=<non-date>) | 'unknown'
    """
    if not dir_name.startswith(("ts_code=", "year=", "quarter=", "date=")):
        return ("unknown", dir_name)
    key, _, val = dir_name.partition("=")
    if key == "date":
        return ("date", val) if DATE_8_RE.match(val) else ("residue_date", val)
    return (key, val)


def target_rel_path(kind: str, value: str) -> str:
    """Destination path relative to <api>/ for a partition of this kind."""
    if kind == "ts_code":
        return f"ts_code={value}/data.parquet"
    if kind == "year":
        return f"year={value}/data.parquet"
    if kind == "quarter":
        return f"quarter={value}/data.parquet"
    if kind == "date":
        return f"trade_date={value}/data.parquet"
    if kind == "none":
        return "data.parquet"
    raise ValueError(f"refusing to migrate residue/unknown partition kind={kind!r} value={value!r}")


# migrate_table(), _canonical_row_count(), main() added in Task 4.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/project/tushare-downloader && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest tests/test_migrate_v2.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/project/tushare-downloader
git add migrate_to_v2.py tests/test_migrate_v2.py && \
  git commit -m "feat: migrate_to_v2 partition classify/target helpers + tests" || \
  echo "not a git repo; tracked via Lean plan"
```

---

## Task 4: Migration I/O + per-table validation (`migrate_table`, `main`)

**Files:**
- Modify: `/home/project/tushare-downloader/migrate_to_v2.py` (append functions)
- Modify: `/home/project/tushare-downloader/tests/test_migrate_v2.py` (add e2e tests)

- [ ] **Step 1: Write the failing e2e migration tests**

Append to `tests/test_migrate_v2.py`:
```python
import pandas as pd


def _write_parquet(path, df):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, engine="pyarrow", index=False)


def test_migrate_table_stock_drops_residue(tmp_path):
    src, dst, api = tmp_path / "src", tmp_path / "dst", "daily"
    good = pd.DataFrame({"ts_code": ["600519.SH"], "trade_date": ["20260101"], "close": [1.0]})
    _write_parquet(src / api / "ts_code=600519.SH" / "data.parquet", good)
    _write_parquet(src / api / "date=600519.SH" / "data.parquet", good)   # residue
    _write_parquet(src / api / "year=2026" / "data.parquet", good)        # residue

    report = mig().migrate_table(src, dst, api, strategy="STOCK")

    assert (dst / api / "ts_code=600519.SH" / "data.parquet").exists()
    assert not (dst / api / "date=600519.SH").exists()
    assert not (dst / api / "year=2026").exists()
    assert report["copied"] == 1
    assert report["skipped_residue"] == 2


def test_migrate_table_date_renames_key(tmp_path):
    src, dst, api = tmp_path / "src", tmp_path / "dst", "index_weight"
    df = pd.DataFrame({"index_code": ["000300.SH"], "trade_date": ["20260623"]})
    _write_parquet(src / api / "date=20260623" / "data.parquet", df)
    _write_parquet(src / api / "date=920992.BJ" / "data.parquet", df)  # residue, skipped

    report = mig().migrate_table(src, dst, api, strategy="DATE")

    assert (dst / api / "trade_date=20260623" / "data.parquet").exists()
    assert not (dst / api / "date=20260623").exists()
    assert report["copied"] == 1
    assert report["skipped_residue"] == 1


def test_migrate_table_validates_row_count(tmp_path):
    """Destination row count != source canonical -> raise and roll back."""
    src, dst, api = tmp_path / "src", tmp_path / "dst", "trade_cal"
    df = pd.DataFrame({"cal_date": ["20260101", "20260102"], "is_open": [0, 1]})
    _write_parquet(src / api / "data.parquet", df)
    _write_parquet(dst / api / "data.parquet", df.iloc[:1])  # stale partial dst

    with pytest.raises(RuntimeError):
        mig().migrate_table(src, dst, api, strategy="NONE")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/tushare-downloader && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest tests/test_migrate_v2.py -v`
Expected: FAIL — `AttributeError: module 'migrate_to_v2' has no attribute 'migrate_table'`.

- [ ] **Step 3: Implement `migrate_table`, `_canonical_row_count`, `main`**

Append to `/home/project/tushare-downloader/migrate_to_v2.py`:
```python
import shutil
import sys

import pandas as pd


def _row_count(parquet_path: Path) -> int:
    try:
        return len(pd.read_parquet(parquet_path, columns=None))
    except Exception:
        return -1


def _canonical_row_count(src_api: Path, strategy: str) -> int:
    """Sum rows of canonical partitions for `strategy`, excluding residue."""
    total = 0
    for entry in src_api.iterdir():
        if strategy == "NONE" and entry.name == "data.parquet" and entry.is_file():
            total += _row_count(entry)
            continue
        if not entry.is_dir():
            continue
        kind, _value = classify_partition(entry.name)
        keep = (
            (strategy == "STOCK" and kind == "ts_code")
            or (strategy == "DATE" and kind == "date")
            or (strategy == "YEAR" and kind == "year")
            or (strategy == "QUARTER" and kind == "quarter")
        )
        if keep:
            total += _row_count(entry / "data.parquet")
    return total


def migrate_table(src_root: Path, dst_root: Path, api: str, strategy: str) -> dict:
    """Migrate one table. strategy ∈ {STOCK, DATE, YEAR, QUARTER, NONE}.

    Validates destination row count == source canonical row count; rolls back on mismatch.
    """
    src_api = src_root / api
    dst_api = dst_root / api
    if not src_api.is_dir():
        return {"copied": 0, "skipped_residue": 0, "note": "no source dir"}

    report = {"copied": 0, "skipped_residue": 0}
    written = []

    def _copy(src_file: Path, rel_under_api: str):
        dst_file = dst_api / rel_under_api
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        written.append(dst_file)
        report["copied"] += 1

    try:
        for entry in sorted(src_api.iterdir()):
            name = entry.name
            if not entry.is_dir():
                if name == "data.parquet" and strategy == "NONE":
                    _copy(entry, "data.parquet")
                continue

            kind, value = classify_partition(name)
            if kind in ("residue_date", "unknown"):
                report["skipped_residue"] += 1
                continue

            if strategy == "STOCK" and kind != "ts_code":
                report["skipped_residue"] += 1
                continue
            if strategy == "DATE" and kind != "date":
                continue
            if strategy == "YEAR" and kind != "year":
                continue
            if strategy == "QUARTER" and kind != "quarter":
                continue

            src_file = entry / "data.parquet"
            if src_file.exists():
                _copy(src_file, target_rel_path(kind, value))

        src_rows = _canonical_row_count(src_api, strategy)
        dst_rows = sum(_row_count(f) for f in written)
        if src_rows >= 0 and dst_rows != src_rows:
            raise RuntimeError(
                f"{api}: row-count mismatch src(canonical)={src_rows} dst={dst_rows}"
            )
        return report
    except Exception:
        for f in written:
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        raise


def _resolve_targets(api_filter, api_configs):
    """Return list of (api_name, strategy_name). api_filter None = all."""
    wanted = None
    if api_filter:
        wanted = {a.strip() for a in api_filter.split(",")}
    out = []
    for cfg in api_configs:
        if wanted is not None and cfg.api_name not in wanted:
            continue
        out.append((cfg.api_name, cfg.chunk_strategy.name))
    return out


def main(argv=None) -> int:
    import argparse
    import api_registry

    p = argparse.ArgumentParser(description="Migrate tushare_data -> tushare_data_v2 (local, no download)")
    p.add_argument("--src", default=str(SRC_DIR))
    p.add_argument("--dst", default=str(DST_DIR))
    p.add_argument("--api", help="migrate only this comma-separated list of tables")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    api_configs = _get_api_configs(api_registry)
    targets = _resolve_targets(args.api, api_configs)
    src, dst = Path(args.src), Path(args.dst)
    print(f"Migrating {len(targets)} tables: {src} -> {dst} (dry_run={args.dry_run})")

    failures = []
    for api, strategy in targets:
        try:
            if args.dry_run:
                print(f"  [dry-run] would migrate {api} ({strategy})")
                continue
            report = migrate_table(src, dst, api, strategy)
            print(f"  OK {api} ({strategy}): {report}")
        except Exception as exc:
            failures.append((api, str(exc)))
            print(f"  FAIL {api}: {exc}", file=sys.stderr)

    if failures:
        print(f"\n{len(failures)} table(s) failed; destination incomplete. Do NOT cutover.", file=sys.stderr)
        return 1
    print("\nAll tables migrated and validated.")
    return 0


def _get_api_configs(api_registry_module):
    """Return the list of APIConfig objects from api_registry (name resolved in Task 5)."""
    for attr in ("API_REGISTRY", "ALL_APIS", "APIS"):
        obj = getattr(api_registry_module, attr, None)
        if isinstance(obj, list):
            return obj
    fn = getattr(api_registry_module, "get_all_apis", None) or getattr(api_registry_module, "get_api_configs", None)
    if callable(fn):
        return fn()
    raise RuntimeError("api_registry exposes no API_REGISTRY/ALL_APIS/APIS list or get_all_apis()")


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/project/tushare-downloader && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest tests/test_migrate_v2.py -v`
Expected: all pass (8 from Task 3 + 3 new).

- [ ] **Step 5: Commit**

```bash
cd /home/project/tushare-downloader
git add migrate_to_v2.py tests/test_migrate_v2.py && \
  git commit -m "feat: migrate_to_v2 migrate_table + validation + CLI" || \
  echo "not a git repo; tracked via Lean plan"
```

---

## Task 5: Confirm `api_registry` exposes the API list

The migration CLI resolves the registry via `_get_api_configs` (checks `API_REGISTRY`/`ALL_APIS`/`APIS`/`get_all_apis()`). Verify one of these exists.

**Files:** none (verification; fix only if none exist)

- [ ] **Step 1: Find the registry export**

Run: `grep -nE '^[A-Z_]+ *=|^API_REGISTRY|^ALL_APIS|^APIS|def get_all_apis|def get_api_configs' /home/project/tushare-downloader/api_registry.py | head`

- [ ] **Step 2: Smoke-import and resolve**

Run: `cd /home/project/tushare-downloader && /root/miniconda3/envs/ohmyquant/bin/python3 -c "import migrate_to_v2, api_registry; print(len(migrate_to_v2._get_api_configs(api_registry)))"`
Expected: prints ~73. If it raises `RuntimeError`, add the matching symbol to `_get_api_configs`'s candidate list and re-run.

- [ ] **Step 3: Commit if changed**

```bash
cd /home/project/tushare-downloader
git diff --exit-code migrate_to_v2.py || (git add migrate_to_v2.py && git commit -m "fix: resolve api_registry symbol for migration CLI") || true
```

---

## Task 6: Smoke-test migration on small tables (throwaway destination)

Prove end-to-end on real data for one table per strategy, without stopping the worker and without touching the live `tushare_data/`.

**Files:** none (operational)

- [ ] **Step 1: Dry-run all tables**

Run: `cd /home/project/tushare-downloader && /root/miniconda3/envs/ohmyquant/bin/python3 migrate_to_v2.py --dry-run 2>&1 | tail -20`
Expected: "would migrate N tables" for ~73 tables, exit 0.

- [ ] **Step 2: Migrate one table per strategy to a throwaway dir**

Run:
```bash
cd /home/project/tushare-downloader
T=/tmp/mig_smoke && rm -rf "$T" && mkdir -p "$T"
/root/miniconda3/envs/ohmyquant/bin/python3 migrate_to_v2.py \
  --src /home/project/tushare-downloader/tushare_data \
  --dst "$T/v2" \
  --api trade_cal,index_weight,fund_portfolio,cyq_perf
```
(`cyq_perf`/`fund_nav` are small STOCK tables; avoids copying all of `daily`.)
Expected: 4 `OK` lines (NONE, DATE, QUARTER, STOCK).

- [ ] **Step 3: Verify residue dropped + keys normalized**

Run:
```bash
echo "--- NONE: single file ---"; ls "$T/v2/trade_cal/"
echo "--- DATE: trade_date=, no date= ---"; ls "$T/v2/index_weight/" | head -3; ls "$T/v2/index_weight/" | grep -c '^date=' || true
echo "--- QUARTER: quarter= ---"; ls "$T/v2/fund_portfolio/" | head -2
echo "--- STOCK: only ts_code= ---"; ls "$T/v2/cyq_perf/" | grep -v '^ts_code=' | head
```
Expected: `trade_cal/` = `data.parquet`; `index_weight/` = `trade_date=...` with 0 `date=` matches; `fund_portfolio/` = `quarter=...`; `cyq_perf/` `grep -v ts_code=` empty.

- [ ] **Step 4: Clean up**

Run: `rm -rf /tmp/mig_smoke`

---

## Task 7: Fix the two analysis scripts reading the residual path

Migration deletes `daily/date=<symbol>/` residue that `analysis/06` and `analysis/07` read. Point them at the canonical `ts_code=` path.

**Files:**
- Modify: `/home/project/tushare-downloader/analysis/06_stock_potential_analysis.py:39`
- Modify: `/home/project/tushare-downloader/analysis/07_hs300_potential_duckdb.py:48`

- [ ] **Step 1: Read both lines in context**

Run: `sed -n '35,42p' /home/project/tushare-downloader/analysis/06_stock_potential_analysis.py; echo '---'; sed -n '44,52p' /home/project/tushare-downloader/analysis/07_hs300_potential_duckdb.py`

- [ ] **Step 2: Replace the read path in both files**

In both files, change:
```
FROM read_parquet('tushare_data/daily/date={ts_code}/data.parquet')
```
to:
```
FROM read_parquet('tushare_data/daily/ts_code={ts_code}/data.parquet')
```

- [ ] **Step 3: Verify no other residual reads remain**

Run: `grep -rn "daily/date={ts_code}" /home/project/tushare-downloader/ /home/project/hope/Lean/ 2>/dev/null`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
cd /home/project/tushare-downloader
git add analysis/06_stock_potential_analysis.py analysis/07_hs300_potential_duckdb.py && \
  git commit -m "fix: analysis scripts read canonical ts_code= path (residue being removed)" || \
  echo "not a git repo; tracked via Lean plan"
```

---

## Task 8: Regression test — `get_stock_list` ignores non-`ts_code` dirs

The spec flagged `get_stock_list`; the code already filters (`convert_to_lean.py:329`). Lock it in.

**Files:**
- Create: `/home/project/tushare-downloader/tests/test_get_stock_list.py`

- [ ] **Step 1: Write the test**

`tests/test_get_stock_list.py`:
```python
import importlib.util
from pathlib import Path


def _load_convert_to_lean():
    p = Path("/home/project/hope/Lean/data-source/tushare/convert_to_lean.py")
    spec = importlib.util.spec_from_file_location("convert_to_lean", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_get_stock_list_only_returns_ts_code(tmp_path):
    """Residue date=/year= dirs on daily/ must never be returned as stocks."""
    daily = tmp_path / "daily"
    daily.mkdir()
    (daily / "ts_code=600519.SH").mkdir()
    (daily / "ts_code=000001.SZ").mkdir()
    (daily / "date=920992.BJ").mkdir()   # residue
    (daily / "year=2026").mkdir()        # residue

    mod = _load_convert_to_lean()
    assert mod.get_stock_list(str(tmp_path)) == ["000001.SZ", "600519.SH"]
```

- [ ] **Step 2: Run it**

Run: `cd /home/project/tushare-downloader && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest tests/test_get_stock_list.py -v`
Expected: PASS (filter exists). If FAIL, restore `if entry.startswith("ts_code="):` at `convert_to_lean.py:329`.

- [ ] **Step 3: Commit**

```bash
cd /home/project/tushare-downloader
git add tests/test_get_stock_list.py && \
  git commit -m "test: lock in get_stock_list ts_code= prefix filter" || \
  echo "not a git repo; tracked via Lean plan"
```

---

## Task 9: Cutover — migrate, switch DATA_DIR, restart worker, gap-fill

> ⚠️ **Operator step.** Stops the supervised worker briefly. Run after market close (≥16:00 CST). All prior tasks done and committed.

**Files modified at cutover:**
- `/home/project/tushare-downloader/config.py:20`
- `/home/project/hope/Lean/data-source/tushare/config.py:20`
- New data dir: `/home/project/tushare-downloader/tushare_data_v2/`

- [ ] **Step 1: Snapshot for rollback**

Run:
```bash
cd /home/project/tushare-downloader
cp config.py config.py.bak_precutover_20260623
cp /home/project/hope/Lean/data-source/tushare/config.py /home/project/hope/Lean/data-source/tushare/config.py.bak_precutover_20260623
supervisorctl status tushare_worker
```
Expected: `tushare_worker RUNNING`.

- [ ] **Step 2: STOP the worker (quiesce the old dir)**

Run: `supervisorctl stop tushare_worker`
Verify: `supervisorctl status tushare_worker` → `STOPPED`.

- [ ] **Step 3: Run the full migration into `tushare_data_v2/`**

Run:
```bash
cd /home/project/tushare-downloader
/root/miniconda3/envs/ohmyquant/bin/python3 migrate_to_v2.py \
  --src /home/project/tushare-downloader/tushare_data \
  --dst /home/project/tushare-downloader/tushare_data_v2 2>&1 | tee logs/migrate_v2_$(date +%Y%m%d_%H%M).log
```
Expected: every table `OK`; final `All tables migrated and validated.`; exit 0. ~15-30 min for 18G.
> If ANY `FAIL` / non-zero exit: **stop**. Failed tables auto-roll back. Old `tushare_data/` untouched. Investigate + re-run.

- [ ] **Step 4: Spot-check v2**

Run:
```bash
V=/home/project/tushare-downloader/tushare_data_v2
echo "daily ts_code count: $(ls $V/daily/ | grep -c '^ts_code=')"
echo "daily non-ts_code (should be 0): $(ls $V/daily/ | grep -vc '^ts_code=')"
echo "index_weight latest: $(ls $V/index_weight/ | grep '^trade_date=' | tail -1)"
/root/miniconda3/envs/ohmyquant/bin/python3 -c "import pandas as pd; d=pd.read_parquet('$V/daily/ts_code=600519.SH/data.parquet'); print('600519 rows',len(d),'max',d['trade_date'].max())"
```
Expected: ts_code ≈ 5500; non-ts_code = 0; recent `trade_date=`; Moutai rows ~5900+, max `20260623`.

- [ ] **Step 5: Switch DATA_DIR in both configs**

In `/home/project/tushare-downloader/config.py:20` and `/home/project/hope/Lean/data-source/tushare/config.py:20`, change:
```python
DATA_DIR = "/home/project/tushare-downloader/tushare_data"
```
to:
```python
DATA_DIR = "/home/project/tushare-downloader/tushare_data_v2"
```
Verify: `grep -n '^DATA_DIR' /home/project/tushare-downloader/config.py /home/project/hope/Lean/data-source/tushare/config.py` → both `tushare_data_v2`.

- [ ] **Step 6: Make the old dir read-only (rollback source)**

Run: `chmod -R a-w /home/project/tushare-downloader/tushare_data`
Verify: `touch /home/project/tushare-downloader/tushare_data/test_write` → `Permission denied`.

- [ ] **Step 7: START the worker on v2**

Run: `supervisorctl start tushare_worker`
Verify: `supervisorctl status tushare_worker` → `RUNNING`.

- [ ] **Step 8: Confirm worker targets v2**

Wait 70s, then:
Run: `tail -5 /home/project/tushare-downloader/logs/incremental_scheduler_err.log; ls -lt /home/project/tushare-downloader/tushare_data_v2/daily/ts_code=600519.SH/`
Expected: no fresh errors; normal poll logs ("already finished today" for today is fine).

- [ ] **Step 9: Gap-fill stale tables to 20260623 (one-shot CLI, NOT the daemon)**

Run:
```bash
cd /home/project/tushare-downloader
/root/miniconda3/envs/ohmyquant/bin/python3 incremental_update.py \
  --api index_daily,index_dailybasic,index_weight,index_weekly,index_monthly,\
fund_daily,fund_adj,fund_share,hk_daily,hk_adjfactor,hk_tradecal,fut_daily,fut_settle,fut_holding,\
moneyflow_dc,moneyflow_ths,moneyflow_ind_dc,moneyflow_ind_ths,moneyflow_mkt_dc,dc_daily,dc_hot,ths_daily,ths_hot,tdx_daily,sw_daily,\
stk_premarket,stk_auction_o,stk_auction_c,bak_basic,bak_daily,kpl_list,cyq_chips \
  --target-date 20260623 2>&1 | tee logs/gapfill_$(date +%Y%m%d_%H%M).log
```
Expected: per-table rows written; some may fail on tushare limits — those land in `incremental_state.json` and the daemon retries (`--retry-interval-minutes 30`). `us_daily` excluded (optional, §11). `cyq_chips` (big gap since 20260129) may take many calls; let the daemon finish it if limits hit — non-blocking.

- [ ] **Step 10: Verify LEAN conversion off v2**

Run:
```bash
cd /home/project/hope/Lean
/root/miniconda3/envs/ohmyquant/bin/python3 data-source/tushare/convert_to_lean.py \
  --tushare-data /home/project/tushare-downloader/tushare_data_v2 \
  --tickers 600519.SH 000001.SZ --dry-run 2>&1 | tail -20
```
Expected: both tickers located via `get_stock_list` (reads `ts_code=` dirs in v2), planned conversion reported. Re-run without `--dry-run` to write `equity/{sse,szse}/daily/*.csv` and confirm files appear.

- [ ] **Step 11: Commit the config switch**

```bash
cd /home/project/tushare-downloader
git add config.py && git commit -m "ops: switch DATA_DIR to tushare_data_v2" 2>/dev/null || echo "not a git repo"
cd /home/project/hope/Lean
git add data-source/tushare/config.py
git commit -m "ops: mirror DATA_DIR switch to tushare_data_v2

Co-Authored-By: Claude <noreply@anthropic.com>"
```

- [ ] **Step 12: Observe ≥3 trading days, then optionally archive old dir**

Leave `tushare_data/` read-only. After 3 stable days, optionally:
Run: `tar czf /home/project/tushare-downloader/tushare_data_legacy_20260623.tar.gz -C /home/project/tushare-downloader tushare_data && rm -rf /home/project/tushare-downloader/tushare_data`
(only if disk needed; otherwise leave read-only indefinitely.)

---

## Task 10: Rollback runbook (execute only if cutover fails)

- [ ] **Step 1:** `supervisorctl stop tushare_worker`
- [ ] **Step 2:** Restore configs:
```bash
cd /home/project/tushare-downloader
cp config.py.bak_precutover_20260623 config.py
cp /home/project/hope/Lean/data-source/tushare/config.py.bak_precutover_20260623 /home/project/hope/Lean/data-source/tushare/config.py
```
- [ ] **Step 3:** `chmod -R u+w /home/project/tushare-downloader/tushare_data`
- [ ] **Step 4:** `supervisorctl start tushare_worker`; verify `RUNNING` + normal poll.
- [ ] **Step 5:** Investigate `tushare_data_v2/`; remove once rollback healthy: `rm -rf /home/project/tushare-downloader/tushare_data_v2`. Re-run migration from clean v2 after fixing root cause.

---

## Self-Review (completed during authoring)

**Spec coverage:**
- §4 partition spec → Task 2 ✅
- §5 migration (local, per-category, row validation, rollback) → Tasks 3, 4, 6 ✅
- §5.3 old dir read-only rollback → Task 9 Step 6 + Task 10 ✅
- §6 gap-fill → Task 9 Step 9 ✅ (us_daily excluded per §11)
- §7 supervisor cutover (stop-before-migrate, supervisorctl) → Task 9 Steps 1-8 + Task 10 ✅
- §8 code changes → Task 2 (`_get_file_path`), Task 7 (analysis scripts), config in Task 9; `get_stock_list` verified-already-fixed → Task 8 ✅
- §9 error handling → migration rollback (Task 4), cutover rollback (Task 10), gap-fill retry note (Task 9 Step 9) ✅
- §10 tests → Tasks 2, 3, 4, 6, 8 ✅

**Corrections from spec (documented inline):**
- `get_stock_list` ALREADY filters `ts_code=` (`convert_to_lean.py:329`) — no code change, regression test only (Task 8).
- `_get_file_path` rename is safe: `incremental_update.py:393` passes `date=` as a param (unchanged); no `date=*` glob readers in the pipeline. Only the output key changes.
- Two `analysis/` scripts read the residual `daily/date={ts_code}/` path and break when residue is deleted — fixed in Task 7.

**Placeholder scan:** none — all steps contain concrete code or exact commands.

**Name consistency:** `classify_partition` → kinds `ts_code|year|quarter|date|residue_date|unknown`, used consistently in `target_rel_path`, `migrate_table`, `_canonical_row_count`. `migrate_table(src_root, dst_root, api, strategy)` consistent across tests + impl. `target_rel_path(kind, value)` returns path-relative-to-`<api>/`, consistent in tests + `migrate_table`.
