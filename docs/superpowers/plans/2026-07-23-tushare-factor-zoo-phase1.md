# Tushare 因子动物园 — Phase 1 实现计划 (数据补齐 + STEP 0 前置验收闸)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 tushare 数据缺口 + 落地 STEP 0 两个前置验收闸 (CSI500 宇宙口径一致性、财报延迟披露 PIT 边界), 为 Phase 2 (FactorStore + factor_worker + 7 因子) 扫清前提。

**Architecture:** 不碰成熟 tushare_worker / Barra builder。新增一个独立 backfill 脚本 (`Scripts/factor_zoo/backfill_gaps.py`) 调现有 `tushare-downloader` 的 `IncrementalUpdater` / `downloader` 复用增量逻辑补齐落后表; 新增 `Scripts/factor_zoo/audit_tushare_coverage.py` 复盘审计; STEP 0a/b 两个验收脚本只读核验, 不改任何现有因子。

**Tech Stack:** Python 3 (ohmyquant conda env), pyarrow/pandas, tushare pro_api (token 复用 `/home/project/tushare-downloader/config.py`), pytest, parquet (tushare_data_v2 partition layouts).

**Spec:** `docs/superpowers/specs/2026-07-22-tushare-factor-zoo-design.md` §6 (数据补齐清单) + §7 STEP 0 (前置验收) + §4.1/§4.2 (PIT 边界错误处理 + 单测)。

---

## 前置事实 (实现前已验证, 不必重新探)

1. **运行中的下载 daemon** 用 `/home/project/tushare-downloader/` 的代码 (supervisor `directory=/home/project/tushare-downloader`, 命令 `incremental_scheduler.py --poll-seconds 60`)。token 与 `DATA_DIR=/home/project/tushare-downloader/tushare_data_v2` 在 `config.py`。
2. **`/home/project/hope/Lean/data-source/tushare/` 是另一份拷贝** (非 symlink), 用于 Lean 侧因子 builder 读取。两份代码结构基本一致但 daemon 不用它。补齐要走 daemon 那份 (`/home/project/tushare-downloader/`)。
3. **index_weight 存储与 STEP 0a 纠正**: index_weight 按 `trade_date=YYYYMMDD/data.parquet` 分区, 每个分区文件里**所有 index_code 混在一起** (含 `000300.SH`、`000905.SH`、`399300.SZ` 等 3929 个指数代码)。审计原说"000905.SH 缺失"是误判 (按文件名找)。**实测 `000905.SH` 与 `000300.SH` 都已在盘上**。故 STEP 0a 不是"下载 000905.SH", 而是"核验 CSI500 成员可用 + 与 CSI300 宇宙口径一致"。
4. **`BarraCNE5DataLoader.load_index_constituents`** (`data-source/tushare/barra_cne5_data_loader.py:132-145`) 已支持任意 `index_code`, 默认 `000300.SH`: 读 index_weight → 过滤 index_code → 取 `trade_date ≤ asof` 的最新 → 返回 `con_code` 列成员。CSI500 只需传 `index_code="000905.SH"`。Lean 侧这份 loader 可直接用。
5. **`backfill_cyq.py`** (`/home/project/tushare-downloader/backfill_cyq.py`) 是现有 backfill 脚本范本: argparse + `--target/--api/--workers`, 用 `downloader.TushareDownloader` 限流桶。新 backfill 脚本照此结构。
6. **PIT loader** `data-source/tushare/barra_cne5_data_loader.py:69` `load_point_in_time` 已实现 `f_ann_date/ann_date ≤ asof` filter + sort + iloc[-1] 单一 PIT 口径。STEP 0b 复用它。
7. 新目录 `Scripts/factor_zoo/` 与 `Tests/Python/FactorZoo/` 尚不存在, 需创建 (见 Task 1)。

---

## File Structure (本 Phase 1 涉及)

| 文件 | 职责 | 新/改 |
|---|---|---|
| `Scripts/factor_zoo/__init__.py` | 包标记 | 新 |
| `Scripts/factor_zoo/backfill_gaps.py` | 调现有 downloader 补齐落后表 (index_daily/margin_detail/4 基本面 ann_date 等) | 新 |
| `Scripts/factor_zoo/audit_tushare_coverage.py` | 复盘式审计: 逐表 ON-DISK 行数/最新日 + 15000 档核对, 产缺口清单 | 新 |
| `Scripts/factor_zoo/verify_universe_consistency.py` | STEP 0a: 核验 CSI300+500 成员在 as-of 日可用且口径一致 | 新 |
| `Scripts/factor_zoo/verify_pit_boundary.py` | STEP 0b: 核验财报延迟披露时财务因子回退去年数据无前视 | 新 |
| `Scripts/factor_zoo/pit_financials.py` | STEP 0b 复用的 PIT 财务读取辅助 (薄包 BarraCNE5DataLoader.load_point_in_time) | 新 |
| `Scripts/factor_zoo/write_coverage_report.py` | 把审计快照写到 Results/factor-zoo/coverage-report.json | 新 |
| `Tests/Python/FactorZoo/__init__.py` | 测试包标记 | 新 |
| `Tests/Python/FactorZoo/conftest.py` | pytest fixture (synthetic parquet for income/balancesheet/cashflow/fina_indicator) | 新 |
| `Tests/Python/FactorZoo/test_pit_financials.py` | STEP 0b 单测: 财报延迟披露 PIT 边界 | 新 |
| `Tests/Python/FactorZoo/test_verify_universe_consistency.py` | STEP 0a 单测 | 新 |
| `Tests/Python/FactorZoo/test_audit_tushare_coverage.py` | 审计脚本单测 | 新 |
| `Tests/Python/FactorZoo/test_backfill_gaps.py` | backfill gap 选择单测 | 新 |

**不碰**: `/home/project/tushare-downloader/incremental_scheduler.py`, `incremental_update.py`, `downloader.py`, `barra_cne5_*`, `Common/Factors/**`, 任何策略代码。

---

## Task 1: 建包骨架 + audit 脚本 (复盘点)

**Files:**
- Create: `Scripts/factor_zoo/__init__.py`
- Create: `Scripts/factor_zoo/audit_tushare_coverage.py`
- Create: `Tests/Python/FactorZoo/__init__.py`
- Create: `Tests/Python/FactorZoo/conftest.py`
- Create: `Tests/Python/FactorZoo/test_audit_tushare_coverage.py`

- [ ] **Step 1: Write the failing test for audit script (audit report shape)**

Create `Tests/Python/FactorZoo/test_audit_tushare_coverage.py`:

```python
"""Tests for the tushare coverage audit script (Phase 1, spec §6)."""
import sys
from pathlib import Path

# allow importing Scripts.factor_zoo when run from repo root
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "Scripts"))

from factor_zoo.audit_tushare_coverage import audit_table, CoverageStatus


def test_audit_table_missing_dir_returns_not_downloaded(tmp_path):
    """A table dir that does not exist -> NOT_DOWNLOADED."""
    report = audit_table("does_not_exist", data_root=str(tmp_path), latest_trade_date="20260722")
    assert report.status == CoverageStatus.NOT_DOWNLOADED
    assert report.rows == 0
    assert report.last_date is None


def test_audit_table_empty_dir_returns_empty(tmp_path):
    """A table dir that exists but has no parquet -> EMPTY."""
    (tmp_path / "empty_table").mkdir()
    report = audit_table("empty_table", data_root=str(tmp_path), latest_trade_date="20260722")
    assert report.status == CoverageStatus.EMPTY


def test_audit_table_stale_returns_stale(tmp_path):
    """A table whose last trade_date < latest - 10 calendar days -> STALE."""
    # build a fake ts_code-partitioned table with one row at 20260101
    import pyarrow as pa, pyarrow.parquet as pq
    d = tmp_path / "daily_basic" / "ts_code=600519.SH"
    d.mkdir(parents=True)
    tbl = pa.table({"ts_code": ["600519.SH"], "trade_date": ["20260101"], "close": [100.0]})
    pq.write_table(tbl, d / "data.parquet")
    report = audit_table("daily_basic", data_root=str(tmp_path), latest_trade_date="20260722")
    assert report.status == CoverageStatus.STALE
    assert report.last_date == "20260101"
    assert report.rows == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest Tests/Python/FactorZoo/test_audit_tushare_coverage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'factor_zoo.audit_tushare_coverage'`

- [ ] **Step 3: Create package skeleton + conftest**

Create `Scripts/factor_zoo/__init__.py`:
```python
"""Factor zoo support scripts (Phase 1: data backfill + pre-acceptance gates).

See docs/superpowers/specs/2026-07-22-tushare-factor-zoo-design.md.
"""
```

Create `Tests/Python/FactorZoo/__init__.py`:
```python
"""Tests for the tushare factor zoo (Phase 1)."""
```

Create `Tests/Python/FactorZoo/conftest.py`:
```python
"""Shared pytest fixtures for factor zoo tests.

All fixtures use SYNTHETIC data (never raw production). Field names and
partition layouts mirror the real tushare_data_v2 convention so tests
exercise the real read paths.
"""
import pyarrow as pa
import pyarrow.parquet as pq
import pytest


@pytest.fixture
def tmp_data_root(tmp_path):
    """A scratch tushare_data_v2-style root the test can populate."""
    return tmp_path
```

- [ ] **Step 4: Implement audit_tushare_coverage.py to make tests pass**

Create `Scripts/factor_zoo/audit_tushare_coverage.py`:
```python
"""Audit on-disk tushare coverage for factor-zoo readiness (spec §6).

Reads parquet METADATA only (no full scans) to determine per-table:
  - rows / file count
  - last trade_date present
  - status: NOT_DOWNLOADED | EMPTY | STALE | OK

STALE = last_date < (latest_trade_date - 10 calendar days) as a loose proxy
when we don't have the full trade_cal; the real freshness daemon (Phase 3)
uses trade_cal exact days.

Usage:
  python3 Scripts/factor_zoo/audit_tushare_coverage.py [--data-root DIR]
                                                        [--latest-trade-date YYYYMMDD]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

import pyarrow.parquet as pq


class CoverageStatus(str, Enum):
    NOT_DOWNLOADED = "NOT_DOWNLOADED"
    EMPTY = "EMPTY"
    STALE = "STALE"
    OK = "OK"


@dataclass
class TableReport:
    api_name: str
    status: CoverageStatus
    rows: int = 0
    last_date: Optional[str] = None
    partition_files: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


def _parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y%m%d").date()


def _table_dir(api_name: str, data_root: str) -> Path:
    return Path(data_root) / api_name


def _iter_parquet_files(table_dir: Path):
    """Yield all data.parquet under a table dir (ts_code=/year=/trade_date= partitions)."""
    if not table_dir.exists():
        return
    for p in table_dir.rglob("data.parquet"):
        yield p


def _extract_last_trade_date(table_dir: Path) -> Optional[str]:
    """Scan parquet metadata + read only the date column to find the max date.

    Tries common date columns: trade_date, cal_date, ann_date, f_ann_date, end_date.
    Returns None if no rows or no date column.
    """
    date_cols = ("trade_date", "cal_date", "ann_date", "f_ann_date", "end_date")
    last: Optional[str] = None
    for f in _iter_parquet_files(table_dir):
        pf = pq.ParquetFile(f)
        if pf.metadata.num_rows == 0:
            continue
        schema_names = set(pf.schema_arrow.names)
        col = next((c for c in date_cols if c in schema_names), None)
        if col is None:
            continue
        try:
            col_data = pf.read(columns=[col]).column(0).to_pylist()
        except Exception:
            continue
        for v in col_data:
            if v is None:
                continue
            s = str(v)
            if len(s) == 8 and s.isdigit():
                if last is None or s > last:
                    last = s
    return last


def audit_table(api_name: str, data_root: str, latest_trade_date: str) -> TableReport:
    """Audit one tushare table on disk."""
    table_dir = _table_dir(api_name, data_root)
    if not table_dir.exists():
        return TableReport(api_name, CoverageStatus.NOT_DOWNLOADED)

    files = list(_iter_parquet_files(table_dir))
    if not files:
        return TableReport(api_name, CoverageStatus.EMPTY)

    total_rows = 0
    for f in files:
        total_rows += pq.ParquetFile(f).metadata.num_rows
    if total_rows == 0:
        return TableReport(api_name, CoverageStatus.EMPTY, rows=0,
                           last_date=_extract_last_trade_date(table_dir),
                           partition_files=len(files))

    last = _extract_last_trade_date(table_dir)
    stale = False
    if last is not None:
        try:
            # loose proxy: >10 calendar days behind latest trade date
            stale = (_parse_date(latest_trade_date) - _parse_date(last)).days > 10
        except ValueError:
            stale = False

    status = CoverageStatus.STALE if stale else CoverageStatus.OK
    return TableReport(api_name, status, rows=total_rows, last_date=last,
                       partition_files=len(files))


# Tables the factor zoo depends on (spec §5 deps + §6 critical gaps).
FACTOR_CRITICAL_TABLES = [
    "daily", "daily_basic", "adj_factor", "moneyflow", "moneyflow_hsgt",
    "margin", "margin_detail", "hsgt_top10", "hk_hold", "cyq_perf", "cyq_chips",
    "income", "balancesheet", "cashflow", "fina_indicator", "forecast",
    "express", "dividend", "stk_holdertrade", "pledge_stat", "share_float",
    "block_trade", "limit_list_d", "kpl_list", "index_daily", "index_weight",
    "index_member_all", "index_dailybasic", "trade_cal", "bak_daily",
    "stk_auction_o", "disclosure_date", "top10_holders", "repurchase",
    "stk_rewards", "fina_mainbz", "fina_audit", "namechange", "stock_basic",
]


def audit_all(data_root: str, latest_trade_date: str) -> list[TableReport]:
    return [audit_table(t, data_root, latest_trade_date) for t in FACTOR_CRITICAL_TABLES]


def _resolve_latest_trade_date(data_root: str) -> str:
    """Use trade_cal max is_open=1 date <= today; fall back to today."""
    cal = _table_dir("trade_cal", data_root)
    if cal.exists():
        import pandas as pd
        last: Optional[str] = None
        for f in _iter_parquet_files(cal):
            df = pq.ParquetFile(f).read().to_pandas()
            if "cal_date" in df.columns and "is_open" in df.columns:
                open_dates = df.loc[df["is_open"].astype(str) == "1", "cal_date"].astype(str)
                if not open_dates.empty:
                    m = open_dates.max()
                    last = m if (last is None or m > last) else last
        if last:
            return last
    return dt.date.today().strftime("%Y%m%d")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Audit tushare on-disk coverage")
    ap.add_argument("--data-root", default="/home/project/tushare-downloader/tushare_data_v2")
    ap.add_argument("--latest-trade-date", default=None)
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    latest = args.latest_trade_date or _resolve_latest_trade_date(args.data_root)
    reports = audit_all(args.data_root, latest)
    if args.json:
        print(json.dumps([r.to_dict() for r in reports], ensure_ascii=False, indent=2))
    else:
        print(f"latest_trade_date={latest}")
        print(f"{'table':<20} {'status':<16} {'rows':>12} {'last_date':<10} files")
        for r in reports:
            print(f"{r.api_name:<20} {r.status.value:<16} {r.rows:>12} "
                  f"{(r.last_date or '-'):<10} {r.partition_files}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest Tests/Python/FactorZoo/test_audit_tushare_coverage.py -v`
Expected: 3 PASS

- [ ] **Step 6: Run the audit against the real data root to capture the real coverage picture**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 Scripts/factor_zoo/audit_tushare_coverage.py`
Expected: A table of 38 tables with NOT_DOWNLOADED/EMPTY/STALE/OK status. Capture the output in your notes — the STALE/EMPTY rows are the backfill TODO list for Task 3.

- [ ] **Step 7: Commit**

```bash
cd /home/project/hope/Lean
git add Scripts/factor_zoo/__init__.py Scripts/factor_zoo/audit_tushare_coverage.py \
        Tests/Python/FactorZoo/__init__.py Tests/Python/FactorZoo/conftest.py \
        Tests/Python/FactorZoo/test_audit_tushare_coverage.py
git commit -m "feat(factor-zoo): Phase 1 Task 1 — coverage audit script + package skeleton

Reads parquet metadata + date column only (no full scans) to classify each
factor-critical tushare table as NOT_DOWNLOADED/EMPTY/STALE/OK. Foundation
for the §6 backfill TODO list. New package Scripts/factor_zoo + Tests/Python/FactorZoo,
no existing code touched.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: STEP 0a — CSI500 universe consistency verification

**Goal:** Disprove the audit's "000905.SH missing" claim with code, and verify CSI300+500 members are usable + consistent with how crowding/Barra read CSI300 today.

**Files:**
- Create: `Scripts/factor_zoo/verify_universe_consistency.py`
- Create: `Tests/Python/FactorZoo/test_verify_universe_consistency.py`

- [ ] **Step 1: Write failing test (universe members are deduped + as-of correct)**

Create `Tests/Python/FactorZoo/test_verify_universe_consistency.py`:
```python
"""STEP 0a: verify CSI300 + CSI500 universe is usable + consistent (spec §7)."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "Scripts"))

import pyarrow as pa
import pyarrow.parquet as pq

from factor_zoo.verify_universe_consistency import (
    load_index_members,
    resolve_universe,
    UniverseReport,
)


def _write_index_weight_partition(root, trade_date, rows):
    """Write one trade_date= partition with mixed index_code rows."""
    d = root / "index_weight" / f"trade_date={trade_date}"
    d.mkdir(parents=True)
    tbl = pa.table(rows)
    pq.write_table(tbl, d / "data.parquet")


def test_load_index_members_filters_by_index_code(tmp_path):
    """index_weight partitions mix all index_codes; loader must filter."""
    _write_index_weight_partition(tmp_path, "20260101", {
        "index_code": ["000300.SH", "000905.SH", "000300.SH"],
        "con_code": ["600519.SH", "000001.SZ", "000002.SZ"],
        "trade_date": ["20260101", "20260101", "20260101"],
        "weight": [0.05, 0.02, 0.04],
    })
    members = load_index_members("000300.SH", data_root=str(tmp_path), asof="20260101")
    assert members == ["000002.SZ", "600519.SH"]  # sorted


def test_resolve_universe_dedupes_across_indices(tmp_path):
    """CSI300 and CSI500 overlap; universe must be the union, deduped + sorted."""
    _write_index_weight_partition(tmp_path, "20260120", {
        "index_code": ["000300.SH", "000300.SH", "000905.SH", "000905.SH"],
        "con_code": ["600519.SH", "000001.SZ", "000001.SZ", "000002.SZ"],
        "trade_date": ["20260120"] * 4,
        "weight": [0.05, 0.04, 0.02, 0.02],
    })
    rep = resolve_universe(["000300.SH", "000905.SH"], data_root=str(tmp_path), asof="20260120")
    assert isinstance(rep, UniverseReport)
    assert rep.members == ["000001.SZ", "000002.SZ", "600519.SH"]
    assert rep.csi300_count == 2
    assert rep.csi500_count == 2
    assert rep.union_count == 3
    assert rep.overlap_count == 1


def test_resolve_universe_missing_index_returns_flag(tmp_path):
    """If CSI500 members can't be resolved (missing), report it, don't crash."""
    _write_index_weight_partition(tmp_path, "20260120", {
        "index_code": ["000300.SH"],
        "con_code": ["600519.SH"],
        "trade_date": ["20260120"],
        "weight": [0.05],
    })
    rep = resolve_universe(["000300.SH", "000905.SH"], data_root=str(tmp_path), asof="20260120")
    assert "000905.SH" in rep.unresolved
    assert rep.csi300_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest Tests/Python/FactorZoo/test_verify_universe_consistency.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'factor_zoo.verify_universe_consistency'`

- [ ] **Step 3: Implement verify_universe_consistency.py**

Create `Scripts/factor_zoo/verify_universe_consistency.py`:
```python
"""STEP 0a: verify CSI300 + CSI500 universe is usable + consistent (spec §7).

Disproves the §6 audit's "000905.SH missing" claim: index_weight is
trade_date=-partitioned with ALL index_codes mixed per file, so 000905.SH
is on disk (verified) — it just wasn't found by filename glob. This script
reads index_weight the same way BarraCNE5DataLoader.load_index_constituents
does (data-source/tushare/barra_cne5_data_loader.py:132) and confirms both
000300.SH and 000905.SH resolve to member lists, and the union is deduped.

Usage:
  python3 Scripts/factor_zoo/verify_universe_consistency.py [--asof YYYYMMDD]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd
import pyarrow.parquet as pq


def _table_dir(api_name: str, data_root: str) -> Path:
    return Path(data_root) / api_name


def _read_partition_file(f: Path):
    return pq.ParquetFile(f).read().to_pandas()


def load_index_members(index_code: str, data_root: str, asof: str) -> list[str]:
    """Return sorted con_code members of index_code as-of `asof` (<= asof, latest date).

    Mirrors BarraCNE5DataLoader.load_index_constituents semantics.
    Returns [] if index_code not present or no con_code column.
    """
    table_dir = _table_dir("index_weight", data_root)
    if not table_dir.exists():
        return []
    frames = []
    for f in table_dir.rglob("data.parquet"):
        df = _read_partition_file(f)
        if df.empty:
            continue
        if "index_code" not in df.columns:
            continue
        df = df[df["index_code"].astype(str) == index_code]
        if not df.empty:
            frames.append(df)
    if not frames:
        return []
    data = pd.concat(frames, ignore_index=True)
    if "trade_date" not in data.columns or "con_code" not in data.columns:
        return []
    data = data[data["trade_date"].astype(str) <= asof]
    if data.empty:
        return []
    latest = data["trade_date"].astype(str).max()
    latest_members = data[data["trade_date"].astype(str) == latest]
    return sorted(latest_members["con_code"].dropna().astype(str).unique().tolist())


@dataclass
class UniverseReport:
    asof: str
    members: list[str] = field(default_factory=list)
    csi300_count: int = 0
    csi500_count: int = 0
    union_count: int = 0
    overlap_count: int = 0
    unresolved: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "asof": self.asof,
            "members": self.members,
            "csi300_count": self.csi300_count,
            "csi500_count": self.csi500_count,
            "union_count": self.union_count,
            "overlap_count": self.overlap_count,
            "unresolved": self.unresolved,
        }


def resolve_universe(index_codes: list[str], data_root: str, asof: str) -> UniverseReport:
    """Resolve union of members across index_codes; dedupe; report overlap."""
    per_index: dict[str, list[str]] = {}
    unresolved: list[str] = []
    for code in index_codes:
        members = load_index_members(code, data_root, asof)
        if members:
            per_index[code] = members
        else:
            unresolved.append(code)

    union_set: set[str] = set()
    for members in per_index.values():
        union_set.update(members)
    members_sorted = sorted(union_set)

    csi300 = per_index.get("000300.SH", [])
    csi500 = per_index.get("000905.SH", [])
    overlap = len(set(csi300) & set(csi500))

    return UniverseReport(
        asof=asof,
        members=members_sorted,
        csi300_count=len(csi300),
        csi500_count=len(csi500),
        union_count=len(members_sorted),
        overlap_count=overlap,
        unresolved=unresolved,
    )


def _resolve_latest_trade_date(data_root: str) -> str:
    cal = _table_dir("trade_cal", data_root)
    if cal.exists():
        last = None
        for f in cal.rglob("data.parquet"):
            df = _read_partition_file(f)
            if "cal_date" in df.columns and "is_open" in df.columns:
                open_dates = df.loc[df["is_open"].astype(str) == "1", "cal_date"].astype(str)
                if not open_dates.empty:
                    m = open_dates.max()
                    last = m if (last is None or m > last) else last
        if last:
            return last
    return dt.date.today().strftime("%Y%m%d")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="STEP 0a: CSI300+500 universe consistency")
    ap.add_argument("--data-root", default="/home/project/tushare-downloader/tushare_data_v2")
    ap.add_argument("--asof", default=None, help="YYYYMMDD; default latest trade_cal is_open date")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    asof = args.asof or _resolve_latest_trade_date(args.data_root)
    rep = resolve_universe(["000300.SH", "000905.SH"], args.data_root, asof)
    if args.json:
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"as-of: {asof}")
        print(f"CSI300 members: {rep.csi300_count}")
        print(f"CSI500 members: {rep.csi500_count}")
        print(f"union (deduped): {rep.union_count}")
        print(f"overlap: {rep.overlap_count}")
        if rep.unresolved:
            print(f"UNRESOLVED indices: {rep.unresolved}")
            print("  -> if 000905.SH here, the §6 audit 'missing' claim stands; otherwise refuted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest Tests/Python/FactorZoo/test_verify_universe_consistency.py -v`
Expected: 3 PASS

- [ ] **Step 5: Run against real data to confirm 000905.SH resolves**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 Scripts/factor_zoo/verify_universe_consistency.py`
Expected: `CSI500 members: <N> (>0)`, `UNRESOLVED indices: []`. If `000905.SH` is in unresolved, STOP and re-investigate before proceeding — the audit claim would be confirmed. (Pre-verified: it should resolve.)

- [ ] **Step 6: Commit**

```bash
cd /home/project/hope/Lean
git add Scripts/factor_zoo/verify_universe_consistency.py \
        Tests/Python/FactorZoo/test_verify_universe_consistency.py
git commit -m "feat(factor-zoo): Phase 1 Task 2 — STEP 0a universe consistency gate

Reads index_weight the same way BarraCNE5DataLoader does (trade_date
partitions, all index_codes mixed per file) and confirms both 000300.SH
and 000905.SH resolve to member lists with a deduped union. Refutes the
§6 audit's '000905.SH missing' misdiagnosis (it was a filename-glob artifact).

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: backfill_gaps.py — call existing downloader to fill STALE/EMPTY tables

**Goal:** A thin orchestrator that calls the existing `tushare-downloader` `IncrementalUpdater` / `downloader` (NOT a new download implementation) to backfill the tables flagged STALE/EMPTY in Task 1's audit. Honors "never modify existing features": the downloader stays untouched.

**Files:**
- Create: `Scripts/factor_zoo/backfill_gaps.py`
- Create: `Tests/Python/FactorZoo/test_backfill_gaps.py`

- [ ] **Step 1: Write failing test (gap selection from audit reports)**

Create `Tests/Python/FactorZoo/test_backfill_gaps.py`:
```python
"""Tests for backfill_gaps.py gap selection (spec §6 remediation)."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "Scripts"))

from factor_zoo.audit_tushare_coverage import TableReport, CoverageStatus
from factor_zoo.backfill_gaps import select_gaps, BackfillPlan, BackfillTarget


def _report(api, status, last_date=None):
    return TableReport(api_name=api, status=status, rows=10, last_date=last_date,
                       partition_files=1)


def test_select_gaps_picks_stale_and_empty_not_ok():
    reports = [
        _report("daily", CoverageStatus.OK, "20260722"),
        _report("index_daily", CoverageStatus.STALE, "20260616"),
        _report("margin_detail", CoverageStatus.STALE, "20260228"),
        _report("income", CoverageStatus.STALE, "20260515"),
        _report("namechange", CoverageStatus.EMPTY, None),
        _report("trade_cal", CoverageStatus.OK, "20260722"),
    ]
    plan = select_gaps(reports)
    assert isinstance(plan, BackfillPlan)
    apis = [t.api_name for t in plan.targets]
    assert "index_daily" in apis
    assert "margin_detail" in apis
    assert "income" in apis
    assert "namechange" in apis
    assert "daily" not in apis
    assert "trade_cal" not in apis


def test_select_gaps_attaches_window_from_last_date():
    reports = [_report("index_daily", CoverageStatus.STALE, "20260616")]
    plan = select_gaps(reports, latest_trade_date="20260722")
    t = plan.targets[0]
    assert t.api_name == "index_daily"
    assert t.start_date == "20260616"  # backfill from last known date
    assert t.end_date == "20260722"


def test_select_gaps_empty_table_uses_bootstrap_window():
    reports = [_report("namechange", CoverageStatus.EMPTY, None)]
    plan = select_gaps(reports, latest_trade_date="20260722", bootstrap_days=365)
    t = plan.targets[0]
    assert t.start_date is not None  # bootstrap window computed
    assert t.end_date == "20260722"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest Tests/Python/FactorZoo/test_backfill_gaps.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'factor_zoo.backfill_gaps'`

- [ ] **Step 3: Implement backfill_gaps.py**

Create `Scripts/factor_zoo/backfill_gaps.py`:
```python
"""Backfill tushare data gaps flagged by audit_tushare_coverage (spec §6).

THIN ORCHESTRATOR ONLY: does not re-implement downloading. It shells out to
the existing /home/project/tushare-downloader IncrementalUpdater for tables
it knows how to refresh, and to backfill_cyq.py for cyq_perf/cyq_chips.
Honors the "never modify existing features" constraint — the downloader,
incremental_update, and backfill_cyq are all called as-is, never edited.

Usage:
  python3 Scripts/factor_zoo/backfill_gaps.py [--data-root DIR]
            [--latest-trade-date YYYYMMDD] [--dry-run] [--api API,API,...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional

from factor_zoo.audit_tushare_coverage import (
    audit_all, TableReport, CoverageStatus, _resolve_latest_trade_date,
)

DOWNLOADER_DIR = "/home/project/tushare-downloader"
DOWNLOADER_PYTHON = "/root/miniconda3/envs/ohmyquant/bin/python3"

# Tables that the IncrementalUpdater refreshes directly via the per-API
# strategies already registered (DATE/YEAR/STOCK/QUARTER/NONE). We invoke
# incremental_update.py with --api <list> --bootstrap-trade-days N.
INCREMENTAL_REFRESHABLE = {
    "index_daily", "index_weight", "margin_detail", "income", "balancesheet",
    "cashflow", "fina_indicator", "forecast", "express", "dividend",
    "stk_holdertrade", "pledge_stat", "share_float", "namechange",
    "daily_basic", "moneyflow", "moneyflow_hsgt", "hsgt_top10", "hk_hold",
    "limit_list_d", "block_trade", "disclosure_date", "top10_holders",
    "repurchase", "stk_rewards", "fina_mainbz", "fina_audit", "bak_daily",
    "stk_auction_o",
}

# cyq tables use the dedicated side-channel backfill (not the main updater).
CYQ_REFRESHABLE = {"cyq_perf", "cyq_chips"}


@dataclass
class BackfillTarget:
    api_name: str
    start_date: Optional[str]
    end_date: str
    via: str  # "incremental" | "cyq"


@dataclass
class BackfillPlan:
    latest_trade_date: str
    targets: list[BackfillTarget] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "latest_trade_date": self.latest_trade_date,
            "targets": [
                {"api_name": t.api_name, "start_date": t.start_date,
                 "end_date": t.end_date, "via": t.via} for t in self.targets
            ],
        }


def _bootstrap_start(end_date: str, days: int) -> str:
    end = dt.datetime.strptime(end_date, "%Y%m%d").date()
    start = end - dt.timedelta(days=days)
    return start.strftime("%Y%m%d")


def select_gaps(reports: list[TableReport],
                latest_trade_date: str = "20260722",
                bootstrap_days: int = 365) -> BackfillPlan:
    """Pick STALE/EMPTY tables and attach a backfill window."""
    targets: list[BackfillTarget] = []
    for r in reports:
        if r.status not in (CoverageStatus.STALE, CoverageStatus.EMPTY):
            continue
        start = r.last_date if r.last_date else _bootstrap_start(latest_trade_date,
                                                                  bootstrap_days)
        if r.api_name in CYQ_REFRESHABLE:
            via = "cyq"
        elif r.api_name in INCREMENTAL_REFRESHABLE:
            via = "incremental"
        else:
            via = "incremental"  # best-effort; updater may SkipIncrementalAPI
        targets.append(BackfillTarget(r.api_name, start, latest_trade_date, via))
    return BackfillPlan(latest_trade_date=latest_trade_date, targets=targets)


def _run_incremental(targets: list[BackfillTarget], dry_run: bool) -> list[dict]:
    """Invoke the existing IncrementalUpdater per-api for the incremental-refreshable set."""
    results = []
    apis = [t.api_name for t in targets if t.via == "incremental"]
    if not apis:
        return results
    cmd = [
        DOWNLOADER_PYTHON,
        f"{DOWNLOADER_DIR}/incremental_update.py",
        "--api", ",".join(apis),
        "--bootstrap-trade-days", "365",
        "--lookback-trade-days", "5",
    ]
    if dry_run:
        print("[dry-run] would run:", " ".join(cmd))
        return [{"api": a, "dry_run": True} for a in apis]
    proc = subprocess.run(cmd, cwd=DOWNLOADER_DIR, capture_output=True, text=True)
    ok = proc.returncode == 0
    for a in apis:
        results.append({"api": a, "ok": ok, "stderr_tail": (proc.stderr or "")[-300:]})
    return results


def _run_cyq(targets: list[BackfillTarget], dry_run: bool) -> list[dict]:
    """Invoke the existing backfill_cyq.py for cyq_perf/cyq_chips."""
    results = []
    apis = [t.api_name for t in targets if t.via == "cyq"]
    if not apis:
        return results
    cmd = [
        DOWNLOADER_PYTHON,
        f"{DOWNLOADER_DIR}/backfill_cyq.py",
        "--api", ",".join(apis),
        "--workers", "8",
    ]
    if dry_run:
        print("[dry-run] would run:", " ".join(cmd))
        return [{"api": a, "dry_run": True} for a in apis]
    proc = subprocess.run(cmd, cwd=DOWNLOADER_DIR, capture_output=True, text=True)
    ok = proc.returncode == 0
    for a in apis:
        results.append({"api": a, "ok": ok, "stderr_tail": (proc.stderr or "")[-300:]})
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backfill tushare data gaps (spec §6)")
    ap.add_argument("--data-root", default="/home/project/tushare-downloader/tushare_data_v2")
    ap.add_argument("--latest-trade-date", default=None)
    ap.add_argument("--dry-run", action="store_true", help="print plan + commands, don't run")
    ap.add_argument("--api", default=None, help="comma list; default = all STALE/EMPTY from audit")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    latest = args.latest_trade_date or _resolve_latest_trade_date(args.data_root)
    reports = audit_all(args.data_root, latest)
    plan = select_gaps(reports, latest_trade_date=latest)
    if args.api:
        want = set(args.api.split(","))
        plan.targets = [t for t in plan.targets if t.api_name in want]

    if args.dry_run:
        if args.json:
            print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
        _run_incremental(plan.targets, dry_run=True)
        _run_cyq(plan.targets, dry_run=True)
        return 0

    inc_results = _run_incremental(plan.targets, dry_run=False)
    cyq_results = _run_cyq(plan.targets, dry_run=False)
    if args.json:
        print(json.dumps({"incremental": inc_results, "cyq": cyq_results},
                          ensure_ascii=False, indent=2))
    else:
        for r in inc_results + cyq_results:
            mark = "OK" if r.get("ok") else "FAIL"
            print(f"[{mark}] {r.get('api')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest Tests/Python/FactorZoo/test_backfill_gaps.py -v`
Expected: 3 PASS

- [ ] **Step 5: Dry-run against real audit to produce the backfill plan**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 Scripts/factor_zoo/backfill_gaps.py --dry-run`
Expected: A plan listing STALE/EMPTY tables (index_daily, margin_detail, income, balancesheet, cashflow, fina_indicator, namechange, etc.) and the exact `incremental_update.py` / `backfill_cyq.py` commands that would run. **Review this output before Step 6** — it's the actual remediation.

- [ ] **Step 6: Run the real backfill (NOT dry-run)**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 Scripts/factor_zoo/backfill_gaps.py`
Expected: Each flagged API prints `[OK] <api>` or `[FAIL] <api>` with stderr tail. If any FAIL, read the stderr tail — common cause is `permission_denied` (independent paid endpoint, expected for the non-factor tables; the 7 factors depend only on free/≤2000pt tables). **Do not restart tushare_worker** for this — the script calls the updater library directly, not the daemon.

- [ ] **Step 7: Re-run audit to confirm gaps closed**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 Scripts/factor_zoo/audit_tushare_coverage.py`
Expected: previously-STALE tables now show `OK` (or closer `last_date` to latest trade date). Some may remain STALE if the downloader's per-API state already covers them (idempotent skip) — that's fine, it means the data is current.

- [ ] **Step 8: Commit**

```bash
cd /home/project/hope/Lean
git add Scripts/factor_zoo/backfill_gaps.py Tests/Python/FactorZoo/test_backfill_gaps.py
git commit -m "feat(factor-zoo): Phase 1 Task 3 — backfill orchestrator for §6 gaps

Thin orchestrator that shells out to the EXISTING tushare-downloader
IncrementalUpdater (incremental_update.py) and backfill_cyq.py — no new
download code, no edits to mature downloader. STALE/EMPTY tables from the
audit get a backfill window; cyq_perf/cyq_chips routed to the cyq side-channel.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: STEP 0b — PIT financials helper + the财报延迟披露 boundary tests

**Goal:** The most-hidden lookahead trap (spec §4.1 + §7 STEP 0b + 0723 review point 1). Build a thin PIT financials reader reusing `BarraCNE5DataLoader.load_point_in_time`'s filter+sort+iloc[-1] semantics, with unit tests that explicitly cover "annual report not yet announced on day t → fall back to last year's data".

**Files:**
- Create: `Scripts/factor_zoo/pit_financials.py`
- Create: `Tests/Python/FactorZoo/test_pit_financials.py`

- [ ] **Step 1: Write failing tests for the delayed-disclosure PIT boundary**

Create `Tests/Python/FactorZoo/test_pit_financials.py`:
```python
"""STEP 0b: PIT financials — the财报延迟披露 lookahead boundary (spec §4.1, §7).

The hidden trap: if a factor reads the current-year annual report on a day
when that report has NOT been announced yet, it leaks future info. The fix:
anchor on f_ann_date <= t; if the current-year report isn't announced yet,
fall back to the most-recent announced report (last year's). These tests
enforce that.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "Scripts"))

import pyarrow as pa
import pyarrow.parquet as pq

from factor_zoo.pit_financials import load_pit_annual


def _write_balancesheet(root, ts_code, rows):
    d = root / "balancesheet" / f"ts_code={ts_code}"
    d.mkdir(parents=True)
    tbl = pa.table(rows)
    pq.write_table(tbl, d / "data.parquet")


def test_load_pit_uses_announced_report_not_period_end():
    """On 2026-03-15, the 2025 annual report (end_date 20251231) is announced
    (f_ann_date 20260315). Factor may use FY2025 data. 2026-03-14 must NOT."""
    _write_balancesheet(Path("/tmp/fz_pit_test1"), "600519.SH", {
        "ts_code": ["600519.SH", "600519.SH"],
        "end_date": ["20241231", "20251231"],
        "f_ann_date": ["20250328", "20260315"],
        "ann_date": ["20250328", "20260315"],
        "report_type": ["1", "1"],
        "end_type": ["4", "4"],
        "total_assets": [1.0e10, 1.1e10],
    })
    # 2026-03-14: FY2025 not announced yet -> must return FY2024
    r = load_pit_annual("balancesheet", "600519.SH", "20260314",
                        data_root="/tmp/fz_pit_test1", value_col="total_assets")
    assert r is not None
    assert r["end_date"] == "20241231"
    assert r["total_assets"] == 1.0e10
    # 2026-03-15: FY2025 announced -> may use FY2025
    r2 = load_pit_annual("balancesheet", "600519.SH", "20260315",
                         data_root="/tmp/fz_pit_test1", value_col="total_assets")
    assert r2["end_date"] == "20251231"
    assert r2["total_assets"] == 1.1e10


def test_load_pit_insufficient_history_returns_none():
    """A newly listed firm with <2 annual reports announced by t -> None."""
    _write_balancesheet(Path("/tmp/fz_pit_test2"), "300999.SZ", {
        "ts_code": ["300999.SZ"],
        "end_date": ["20251231"],
        "f_ann_date": ["20260420"],
        "ann_date": ["20260420"],
        "report_type": ["1"],
        "end_type": ["4"],
        "total_assets": [5.0e8],
    })
    r = load_pit_annual("balancesheet", "300999.SZ", "20260420",
                        data_root="/tmp/fz_pit_test2", value_col="total_assets",
                        min_periods=2)
    assert r is None  # only 1 period available


def test_load_pit_picks_latest_revision_within_window():
    """If a restatement (update_flag='1') is announced later than the original
    (update_flag='0'), the PIT reader on that later date must pick the restatement."""
    _write_balancesheet(Path("/tmp/fz_pit_test3"), "000001.SZ", {
        "ts_code": ["000001.SZ", "000001.SZ"],
        "end_date": ["20241231", "20241231"],
        "f_ann_date": ["20250328", "20250610"],
        "ann_date": ["20250328", "20250610"],
        "report_type": ["1", "1"],
        "end_type": ["4", "4"],
        "update_flag": ["0", "1"],
        "total_assets": [2.0e12, 2.05e12],  # restated
    })
    r = load_pit_annual("balancesheet", "000001.SZ", "20250601",
                        data_root="/tmp/fz_pit_test3", value_col="total_assets")
    # 20250601 < 20250610, so restatement not yet announced -> original
    assert r["total_assets"] == 2.0e12
    r2 = load_pit_annual("balancesheet", "000001.SZ", "20250610",
                         data_root="/tmp/fz_pit_test3", value_col="total_assets")
    assert r2["total_assets"] == 2.05e12  # restatement now usable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest Tests/Python/FactorZoo/test_pit_financials.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'factor_zoo.pit_financials'`

- [ ] **Step 3: Implement pit_financials.py**

Create `Scripts/factor_zoo/pit_financials.py`:
```python
"""PIT (point-in-time) annual financials reader (spec §4.1, §7 STEP 0b).

Thin wrapper that reuses the SAME PIT semantics as
data-source/tushare/barra_cne5_data_loader.py:69 load_point_in_time:
  filter f_ann_date/ann_date <= asof  ->  sort by f_ann_date  ->
  take the latest revision per end_date (iloc[-1]).

This single PIT boundary is shared by all 4 financial factors
(accruals_sloan, gross_profitability, asset_growth, roe_change) so the
"财报延迟披露" lookahead trap is closed in ONE place (0723 review point 1).

Only ANNUAL rows are returned (end_type='4', report_type='1' consolidated),
matching Sloan/Novy-Marx/Cooper-Gulen-Schill canonical annual measures.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
import pyarrow.parquet as pq


_ANNOUNCE_FIELDS = ("f_ann_date", "ann_date")
_END_DATE_FIELD = "end_date"
_REPORT_TYPE_FIELD = "report_type"
_END_TYPE_FIELD = "end_type"


def _table_dir(api_name: str, data_root: str) -> Path:
    return Path(data_root) / api_name


def _read_all_partitions(table_dir: Path) -> pd.DataFrame:
    frames = []
    for f in table_dir.rglob("data.parquet"):
        df = pq.ParquetFile(f).read().to_pandas()
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_pit_annual(api_name: str, ts_code: str, asof: str,
                    data_root: str, value_col: str,
                    min_periods: int = 1) -> Optional[dict]:
    """Return the most-recent PIT-annual row for ts_code as-of `asof`.

    Returns None if fewer than `min_periods` annual periods are available
    (e.g. newly-listed firms with <2 annuals -> factor = Stale/NaN).

    `asof` is YYYYMMDD string. Only rows with f_ann_date (fallback ann_date)
    <= asof are eligible. Among eligible rows, picks the latest f_ann_date per
    end_date (handles restatements), then the latest end_date (most recent
    annual report).
    """
    table_dir = _table_dir(api_name, data_root) / f"ts_code={ts_code}"
    if not table_dir.exists():
        table_dir = _table_dir(api_name, data_root)
    df = _read_all_partitions(table_dir)
    if df.empty:
        return None
    if "ts_code" in df.columns:
        df = df[df["ts_code"].astype(str) == ts_code]
    if _REPORT_TYPE_FIELD in df.columns:
        df = df[df[_REPORT_TYPE_FIELD].astype(str) == "1"]
    if _END_TYPE_FIELD in df.columns:
        df = df[df[_END_TYPE_FIELD].astype(str) == "4"]
    ann_col = next((c for c in _ANNOUNCE_FIELDS if c in df.columns), None)
    if ann_col is None:
        return None
    df = df[df[ann_col].astype(str) <= asof]
    if df.empty:
        return None
    df = df.sort_values([_END_DATE_FIELD, ann_col])
    df = df.drop_duplicates(subset=[_END_DATE_FIELD], keep="last")
    if len(df) < min_periods:
        return None
    row = df.sort_values(_END_DATE_FIELD).iloc[-1]
    if value_col not in row.index:
        return None
    out = {c: row[c] for c in row.index if c == value_col or c in
           (_END_DATE_FIELD, ann_col, _REPORT_TYPE_FIELD, _END_TYPE_FIELD)}
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser(description="PIT annual financials probe (STEP 0b)")
    ap.add_argument("--api", default="balancesheet")
    ap.add_argument("--ts-code", required=True)
    ap.add_argument("--asof", required=True)
    ap.add_argument("--value-col", required=True)
    ap.add_argument("--data-root", default="/home/project/tushare-downloader/tushare_data_v2")
    ap.add_argument("--min-periods", type=int, default=1)
    args = ap.parse_args()
    r = load_pit_annual(args.api, args.ts_code, args.asof, args.data_root,
                       args.value_col, args.min_periods)
    if r is None:
        print("None (insufficient PIT history)")
    else:
        print(r)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest Tests/Python/FactorZoo/test_pit_financials.py -v`
Expected: 3 PASS. If `test_load_pit_uses_announced_report_not_period_end` fails, the PIT anchor is wrong — fix the `f_ann_date <= asof` filter; do NOT weaken the test (this is the 0723 review's #1 correctness gate).

- [ ] **Step 5: Probe a real stock to confirm PIT boundary holds on real data**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 -m Scripts.factor_zoo.pit_financials --api balancesheet --ts-code 600519.SH --asof 20260314 --value-col total_assets`
Then: `... --asof 20260315 ...`
Expected: 20260314 returns FY2024 (end_date 20241231) total_assets; 20260315 returns FY2025 (end_date 20251231) total_assets. This proves the boundary on real贵州茅台 disclosure dates. (If the dates differ on real data, adjust `--asof` to the actual f_ann_date boundary you find in the parquet — the assertion is the *behavior*, not the specific date.)

- [ ] **Step 6: Commit**

```bash
cd /home/project/hope/Lean
git add Scripts/factor_zoo/pit_financials.py Tests/Python/FactorZoo/test_pit_financials.py
git commit -m "feat(factor-zoo): Phase 1 Task 4 — STEP 0b PIT financials boundary

Single PIT reader reusing BarraCNE5DataLoader.load_point_in_time semantics
(f_ann_date<=asof, latest revision per end_date, annual+consolidated only).
Closes the最隐蔽 lookahead trap (0723 review #1): if the current-year annual
report isn't announced on day t, fall back to last year's data — never use
un-announced period-end data. Shared by all 4 financial factors in Phase 5.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: wire audit into a checked-in coverage report writer

**Goal:** Persist the audit snapshot so Phase 2+ can diff against it. A small script that writes `Results/factor-zoo/coverage-report.json` (creating the dir).

**Files:**
- Create: `Scripts/factor_zoo/write_coverage_report.py`

- [ ] **Step 1: Implement write_coverage_report.py**

Create `Scripts/factor_zoo/write_coverage_report.py`:
```python
"""Persist the coverage audit to Results/factor-zoo/coverage-report.json (spec §6)."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from factor_zoo.audit_tushare_coverage import audit_all, _resolve_latest_trade_date


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default="/home/project/tushare-downloader/tushare_data_v2")
    ap.add_argument("--out", default="Results/factor-zoo/coverage-report.json")
    args = ap.parse_args(argv)
    latest = _resolve_latest_trade_date(args.data_root)
    reports = [r.to_dict() for r in audit_all(args.data_root, latest)]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "latest_trade_date": latest,
        "tables": reports,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"wrote {out} (latest_trade_date={latest}, {len(reports)} tables)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Generate the report**

Run: `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 Scripts/factor_zoo/write_coverage_report.py`
Expected: `wrote Results/factor-zoo/coverage-report.json (latest_trade_date=..., 38 tables)`

- [ ] **Step 3: Ensure the runtime artifact is gitignored, commit only the script**

Check if `Results/` is already gitignored; if not, add the artifact path so the generated JSON (a runtime artifact) is not committed:
Run: `cd /home/project/hope/Lean && git check-ignore Results/factor-zoo/coverage-report.json || echo "NOT IGNORED"`
If `NOT IGNORED`: add `Results/factor-zoo/coverage-report.json` to `.gitignore`.

```bash
cd /home/project/hope/Lean
git add Scripts/factor_zoo/write_coverage_report.py
# add gitignore entry only if needed:
git add -p .gitignore 2>/dev/null || true
git commit -m "feat(factor-zoo): Phase 1 Task 5 — coverage report writer

Persists the §6 audit to Results/factor-zoo/coverage-report.json so Phase 2+
can diff against a baseline snapshot. Creates the Results/factor-zoo/ dir.
The generated JSON is a runtime artifact (gitignored), only the script ships.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Phase 1 完成判据 (Definition of Done)

- [ ] `Scripts/factor_zoo/` + `Tests/Python/FactorZoo/` 包存在, 全部单测绿:
  `cd /home/project/hope/Lean && /root/miniconda3/envs/ohmyquant/bin/python3 -m pytest Tests/Python/FactorZoo/ -v`
- [ ] STEP 0a 通过: `verify_universe_consistency.py` 在真实数据上 `CSI500 members > 0`, `unresolved=[]` (000905.SH 可解析, 推翻 §6 审计误判)
- [ ] STEP 0b 通过: `test_pit_financials.py` 3 个测试绿, 尤其 `test_load_pit_uses_announced_report_not_period_end` (财报延迟披露回退去年数据), 且真实数据探针 600519.SH 在 f_ann_date 边界两侧返回不同财年
- [ ] §6 数据缺口补齐: `backfill_gaps.py` 已跑 (非 dry-run), 重审 `audit_tushare_coverage.py` 显示原 STALE 表转 OK 或更近
- [ ] `Results/factor-zoo/coverage-report.json` 已生成 (脚本已提交; 该 JSON 是运行产物, gitignore 排除)
- [ ] 无任何现有代码被改: `git diff --name-only master..HEAD` 仅含 `Scripts/factor_zoo/*`、`Tests/Python/FactorZoo/*`、`docs/` 文档 (及可能的 `.gitignore` 一行)

## Phase 1 之后的路线 (不在本 plan, 仅提示)

- **Phase 2**: FactorStore + 4 只读适配器 (C#, 纯增量) — spec §2
- **Phase 3**: factor_worker supervisor 程序 (接入现有 crowding/forward build_day) — spec §3.1
- **Phase 4**: FactorCatalog 生成器 + manifest schema + 优化器/LLM 接入 — spec §3.2
- **Phase 5**: 7 新因子 build_day (财务类复用本 Phase 的 pit_financials) — spec §5
- **Phase 6**: Barra 接入 + Grafana 面板 + 端到端回归 — spec §7 step 6/9/10
