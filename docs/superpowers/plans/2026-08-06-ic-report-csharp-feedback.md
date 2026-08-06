# IC/IR 报告回灌 C# 运行时 (P2-A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通 Python 研究层 → C# 运行时 的 IC 回灌链路：离线按月端生成 expanding-window IC 报告 JSON，C# 新建 V2 alpha 模型读 JSON 替代硬编码 49 因子，零侵入旧代码。

**Architecture:** 三个独立组件互不侵入——(1) Python `export_ic_reports.py` 复用已有 `ic_ir_engine.py` 按 expanding window 月端生成 JSON；(2) C# `ICWeightedAlphaModelV2` 用 Newtonsoft.Json 读 JSON + FactorStore pull 算 composite score；(3) 新策略 `AShareCSI300EnhancedV2Strategy` + 新 config 装配 V2。旧 `ICWeightedAlphaModel` / 旧策略 / 旧 config 一行不改。

**Tech Stack:** Python 3 (pandas/numpy/scipy, 已有), C# .NET 10, Newtonsoft.Json (LEAN 既有依赖), NUnit, pytest

**Spec:** `docs/superpowers/specs/2026-08-06-ic-report-csharp-feedback-design.md`

---

## File Structure

| 文件 | 责任 | 类型 |
|---|---|---|
| `Scripts/factor_zoo/export_ic_reports.py` | 离线 expanding-window IC 报告生成（复用 ic_ir_engine） | 新建 |
| `Scripts/factor_zoo/tests/test_export_ic_reports.py` | export 脚本 pytest | 新建 |
| `Algorithm.CSharp/Models/Alpha/ICWeightedAlphaModelV2.cs` | C# 读 JSON alpha 模型 | 新建 |
| `Tests/Common/Algorithm/ICWeightedAlphaModelV2Tests.cs` | V2 NUnit 测试 | 新建 |
| `Algorithm.CSharp/AShareCSI300EnhancedV2Strategy.cs` | 装配 V2 的五层策略 | 新建 |
| `Launcher/config_enhanced_v2.json` | V2 回测 config | 新建 |
| `result/ic-reports/ic_report_*.json` | 月端 IC 报告（离线产出，不入 git） | 生成物 |

**关键 API（已核实）：**
- `FactorStore.Get(string factorId, Symbol symbol, DateTime date, IEnumerable<BaseData> history = null)` → `FactorResult`（struct）
- `FactorResult.Value` (decimal), `FactorResult.Quality` (`FactorDataQuality` enum: Valid/Missing/Outlier/Stale)
- `ICIREngine.compute_ic_report(start_date, end_date, horizon=21)` → DataFrame `[factor_id, ic_mean, ic_std, ic_ir, rank_ic_mean, rank_ic_ir, n_obs]`
- `ICIREngine.filter_factors(ic_report, min_ic=0.02, min_ir=0.3)` → `list[str]`（已按 rank_ic_ir 降序）
- IC cache key: `ic_report_{start}_{end}_h{horizon}.pkl` in `result/ic-cache/`

---

### Task 1: Python — IC 报告 export 脚本（TDD）

**Files:**
- Create: `Scripts/factor_zoo/export_ic_reports.py`
- Test: `Scripts/factor_zoo/tests/test_export_ic_reports.py`

- [ ] **Step 1: 创建 tests 目录 + 写第一个失败测试（JSON schema 验证）**

创建 `Scripts/factor_zoo/tests/__init__.py`（空文件）和 `Scripts/factor_zoo/tests/test_export_ic_reports.py`：

```python
"""Tests for export_ic_reports.py — expanding-window IC report JSON generation."""
import json
import sys
from pathlib import Path

import pytest

# Make Scripts/factor_zoo importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make_fake_engine(monkeypatch, report_rows):
    """Patch ICIREngine so compute_ic_report returns a fixed DataFrame
    without touching real parquet data."""
    import pandas as pd
    from export_ic_reports import ICIREngine

    fake_df = pd.DataFrame(report_rows)
    monkeypatch.setattr(ICIREngine, "compute_ic_report",
                        lambda self, start_date, end_date, horizon=21, factor_ids=None: fake_df)


def test_export_single_month_end_produces_valid_json(tmp_path, monkeypatch):
    """A single month-end export produces a JSON file matching the schema."""
    from export_ic_reports import export_ic_reports

    report_rows = [
        {"factor_id": "alpha012", "ic_mean": 0.05, "ic_std": 0.12, "ic_ir": 0.42,
         "rank_ic_mean": 0.052, "rank_ic_std": 0.13, "rank_ic_ir": 0.42, "n_obs": 12},
        {"factor_id": "alpha077", "ic_mean": 0.04, "ic_std": 0.11, "ic_ir": 0.36,
         "rank_ic_mean": 0.048, "rank_ic_std": 0.12, "rank_ic_ir": 0.39, "n_obs": 12},
    ]
    _make_fake_engine(monkeypatch, report_rows)

    out_dir = tmp_path / "ic-reports"
    export_ic_reports(
        month_ends=["2024-01-31"],
        ic_window_start="2020-01-02",
        horizon=21,
        min_ic=0.02,
        min_ir=0.3,
        out_dir=str(out_dir),
    )

    json_path = out_dir / "ic_report_2024-01-31.json"
    assert json_path.exists(), f"Expected JSON at {json_path}"
    data = json.loads(json_path.read_text())
    assert data["report_date"] == "2024-01-31"
    assert data["ic_window_start"] == "2020-01-02"
    assert data["ic_window_end"] == "2024-01-31"
    assert data["horizon"] == 21
    assert data["min_ic"] == 0.02
    assert data["min_ir"] == 0.3
    assert isinstance(data["factors"], list)
    assert len(data["factors"]) == 2
    f0 = data["factors"][0]
    assert set(f0.keys()) == {"factor_id", "rank_ic_mean", "rank_ic_ir", "weight"}
    assert f0["factor_id"] == "alpha012"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /home/project/hope/Lean && python -m pytest Scripts/factor_zoo/tests/test_export_ic_reports.py::test_export_single_month_end_produces_valid_json -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'export_ic_reports'`

- [ ] **Step 3: 实现 export_ic_reports.py 最小代码**

创建 `Scripts/factor_zoo/export_ic_reports.py`：

```python
"""
export_ic_reports.py — Offline expanding-window IC report JSON generator.

Generates one JSON file per month-end: result/ic-reports/ic_report_YYYY-MM-DD.json
Each report uses an EXPANDING window (ic_window_start .. month_end, exclusive of
month_end) so there is no lookahead bias. C# ICWeightedAlphaModelV2 reads the
latest report <= rebalance date at runtime.

Reuses ICIREngine (ic_ir_engine.py) + FactorPanelLoader (factor_panel_loader.py)
without modifying them.

Usage:
    python export_ic_reports.py \\
        --start 2020-01-31 --end 2024-06-28 \\
        --ic-window-start 2020-01-02 \\
        --out-dir /home/project/hope/Lean/result/ic-reports
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from ic_ir_engine import ICIREngine

LOGGER = logging.getLogger("export_ic_reports")

DEFAULT_IC_WINDOW_START = "2020-01-02"
DEFAULT_OUT_DIR = "/home/project/hope/Lean/result/ic-reports"


def _month_end_dates(start: str, end: str) -> list[str]:
    """All month-end dates in [start, end], inclusive, as yyyy-MM-dd strings."""
    from datetime import datetime, timedelta
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    dates = []
    current = s
    while current <= e:
        if current.month == 12:
            next_first = current.replace(year=current.year + 1, month=1, day=1)
        else:
            next_first = current.replace(month=current.month + 1, day=1)
        month_end = next_first - timedelta(days=1)
        if month_end > e:
            month_end = e
        if month_end >= s:
            dates.append(month_end.strftime("%Y-%m-%d"))
        current = next_first
    return dates


def _build_report_payload(
    report_df: pd.DataFrame,
    min_ic: float,
    min_ir: float,
    month_end: str,
    ic_window_start: str,
    horizon: int,
) -> dict | None:
    """Filter factors and build the JSON payload. Returns None if no factors pass."""
    if report_df is None or report_df.empty:
        return None

    filtered = report_df[
        (report_df["rank_ic_mean"].abs() >= min_ic)
        & (report_df["rank_ic_ir"].abs() >= min_ir)
    ].copy()
    if filtered.empty:
        return None

    filtered = filtered.sort_values("rank_ic_ir", ascending=False)

    # Weights = |rank_ic_mean| normalized to sum=1 (mirrors ic_weighted_alpha.py:136-156).
    abs_ic = filtered["rank_ic_mean"].abs()
    total = abs_ic.sum()
    if total <= 0:
        weights = [1.0 / len(filtered)] * len(filtered)
    else:
        weights = (abs_ic / total).tolist()

    factors = []
    for (_, row), w in zip(filtered.iterrows(), weights):
        factors.append({
            "factor_id": str(row["factor_id"]),
            "rank_ic_mean": float(row["rank_ic_mean"]),
            "rank_ic_ir": float(row["rank_ic_ir"]),
            "weight": float(w),
        })

    return {
        "report_date": month_end,
        "ic_window_start": ic_window_start,
        "ic_window_end": month_end,
        "horizon": int(horizon),
        "min_ic": float(min_ic),
        "min_ir": float(min_ir),
        "factors": factors,
    }


def export_ic_reports(
    month_ends: list[str],
    ic_window_start: str,
    horizon: int = 21,
    min_ic: float = 0.02,
    min_ir: float = 0.3,
    out_dir: str = DEFAULT_OUT_DIR,
    force: bool = False,
    engine: ICIREngine | None = None,
) -> list[Path]:
    """Export one IC report JSON per month-end.

    Args:
        month_ends: List of month-end date strings (yyyy-MM-dd), each used as
            the exclusive upper bound of the expanding IC window.
        ic_window_start: Fixed start of the expanding window (yyyy-MM-dd).
        horizon: Forward-return horizon in trading days.
        min_ic: Minimum |rank_ic_mean| to keep a factor.
        min_ir: Minimum |rank_ic_ir| to keep a factor.
        out_dir: Output directory for JSON files.
        force: If True, overwrite existing JSON files.
        engine: Optional pre-built ICIREngine (tests inject a fake). If None,
            a real ICIREngine is constructed.

    Returns:
        List of Paths to JSON files written.
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    eng = engine if engine is not None else ICIREngine()
    written: list[Path] = []

    for month_end in month_ends:
        json_path = out_path / f"ic_report_{month_end}.json"
        if json_path.exists() and not force:
            LOGGER.info("Skip existing (use --force to overwrite): %s", json_path)
            written.append(json_path)
            continue

        # Expanding window: ic_window_start .. month_end (exclusive of month_end
        # — compute_ic_report's _get_month_end_dates returns month-ends <= end_date,
        # so the last IC observation is a prior month-end, not month_end itself).
        report_df = eng.compute_ic_report(
            start_date=ic_window_start,
            end_date=month_end,
            horizon=horizon,
        )

        payload = _build_report_payload(
            report_df, min_ic, min_ir, month_end, ic_window_start, horizon)
        if payload is None:
            LOGGER.warning("No factors passed filter for %s; skipping JSON", month_end)
            continue

        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        LOGGER.info("Wrote %s (%d factors)", json_path, len(payload["factors"]))
        written.append(json_path)

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Export expanding-window IC reports")
    parser.add_argument("--start", required=True, help="First month-end (yyyy-MM-dd)")
    parser.add_argument("--end", required=True, help="Last month-end (yyyy-MM-dd)")
    parser.add_argument("--ic-window-start", default=DEFAULT_IC_WINDOW_START,
                        help="Expanding window start (yyyy-MM-dd)")
    parser.add_argument("--horizon", type=int, default=21)
    parser.add_argument("--min-ic", type=float, default=0.02)
    parser.add_argument("--min-ir", type=float, default=0.3)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--force", action="store_true", help="Overwrite existing JSON")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    month_ends = _month_end_dates(args.start, args.end)
    LOGGER.info("Exporting %d month-end reports from %s to %s",
                len(month_ends), args.start, args.end)

    written = export_ic_reports(
        month_ends=month_ends,
        ic_window_start=args.ic_window_start,
        horizon=args.horizon,
        min_ic=args.min_ic,
        min_ir=args.min_ir,
        out_dir=args.out_dir,
        force=args.force,
    )
    LOGGER.info("Done: %d JSON files written to %s", len(written), args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /home/project/hope/Lean && python -m pytest Scripts/factor_zoo/tests/test_export_ic_reports.py::test_export_single_month_end_produces_valid_json -v`
Expected: PASS

- [ ] **Step 5: 补充测试 — weight 归一化 sum=1**

在 `test_export_ic_reports.py` 追加：

```python
def test_weights_are_normalized_to_sum_one(tmp_path, monkeypatch):
    """Factor weights in each report sum to 1.0."""
    from export_ic_reports import export_ic_reports

    report_rows = [
        {"factor_id": "alpha012", "ic_mean": 0.05, "ic_std": 0.12, "ic_ir": 0.42,
         "rank_ic_mean": 0.060, "rank_ic_std": 0.13, "rank_ic_ir": 0.42, "n_obs": 12},
        {"factor_id": "alpha077", "ic_mean": 0.04, "ic_std": 0.11, "ic_ir": 0.36,
         "rank_ic_mean": 0.040, "rank_ic_std": 0.12, "rank_ic_ir": 0.39, "n_obs": 12},
        {"factor_id": "alpha001", "ic_mean": 0.03, "ic_std": 0.10, "ic_ir": 0.30,
         "rank_ic_mean": 0.030, "rank_ic_std": 0.11, "rank_ic_ir": 0.31, "n_obs": 12},
    ]
    _make_fake_engine(monkeypatch, report_rows)

    out_dir = tmp_path / "ic-reports"
    export_ic_reports(
        month_ends=["2024-03-31"],
        ic_window_start="2020-01-02",
        out_dir=str(out_dir),
    )
    data = json.loads((out_dir / "ic_report_2024-03-31.json").read_text())
    total = sum(f["weight"] for f in data["factors"])
    assert abs(total - 1.0) < 1e-9, f"weights sum to {total}, expected 1.0"
```

- [ ] **Step 6: 运行测试验证通过**

Run: `cd /home/project/hope/Lean && python -m pytest Scripts/factor_zoo/tests/test_export_ic_reports.py::test_weights_are_normalized_to_sum_one -v`
Expected: PASS

- [ ] **Step 7: 补充测试 — 幂等（重跑跳过）**

在 `test_export_ic_reports.py` 追加：

```python
def test_idempotent_skip_existing(tmp_path, monkeypatch):
    """Re-running without --force skips existing JSON files (no recompute)."""
    from export_ic_reports import export_ic_reports

    report_rows = [
        {"factor_id": "alpha012", "ic_mean": 0.05, "ic_std": 0.12, "ic_ir": 0.42,
         "rank_ic_mean": 0.052, "rank_ic_std": 0.13, "rank_ic_ir": 0.42, "n_obs": 12},
    ]
    _make_fake_engine(monkeypatch, report_rows)

    out_dir = tmp_path / "ic-reports"
    export_ic_reports(month_ends=["2024-01-31"], ic_window_start="2020-01-02",
                      out_dir=str(out_dir))

    # Second run without force — should NOT call compute_ic_report.
    call_count = {"n": 0}
    import export_ic_reports as mod
    orig = mod.ICIREngine.compute_ic_report
    def counting(self, *a, **k):
        call_count["n"] += 1
        return orig(self, *a, **k)
    monkeypatch.setattr(mod.ICIREngine, "compute_ic_report", counting)

    export_ic_reports(month_ends=["2024-01-31"], ic_window_start="2020-01-02",
                      out_dir=str(out_dir))
    assert call_count["n"] == 0, "should skip existing without force"
```

- [ ] **Step 8: 运行测试验证通过**

Run: `cd /home/project/hope/Lean && python -m pytest Scripts/factor_zoo/tests/test_export_ic_reports.py::test_idempotent_skip_existing -v`
Expected: PASS

- [ ] **Step 9: 补充测试 — expanding window 无前视（窗口 end = month_end）**

在 `test_export_ic_reports.py` 追加：

```python
def test_expanding_window_end_equals_month_end_no_lookahead(tmp_path, monkeypatch):
    """compute_ic_report is called with end_date=month_end — no future data leaks in."""
    from export_ic_reports import export_ic_reports

    report_rows = [
        {"factor_id": "alpha012", "ic_mean": 0.05, "ic_std": 0.12, "ic_ir": 0.42,
         "rank_ic_mean": 0.052, "rank_ic_std": 0.13, "rank_ic_ir": 0.42, "n_obs": 12},
    ]
    captured = {}
    import export_ic_reports as mod
    def capture(self, start_date, end_date, horizon=21, factor_ids=None):
        captured["start"] = start_date
        captured["end"] = end_date
        captured["horizon"] = horizon
        import pandas as pd
        return pd.DataFrame(report_rows)
    monkeypatch.setattr(mod.ICIREngine, "compute_ic_report", capture)

    export_ic_reports(month_ends=["2024-06-30"], ic_window_start="2020-01-02",
                      out_dir=str(tmp_path / "ic-reports"))
    assert captured["start"] == "2020-01-02"
    assert captured["end"] == "2024-06-30", "window end must equal month_end (no lookahead)"
```

- [ ] **Step 10: 运行测试验证通过**

Run: `cd /home/project/hope/Lean && python -m pytest Scripts/factor_zoo/tests/test_export_ic_reports.py::test_expanding_window_end_equals_month_end_no_lookahead -v`
Expected: PASS

- [ ] **Step 11: 提交**

```bash
cd /home/project/hope/Lean
git add Scripts/factor_zoo/export_ic_reports.py Scripts/factor_zoo/tests/__init__.py Scripts/factor_zoo/tests/test_export_ic_reports.py
git commit -m "feat(p2-a): expanding-window IC report JSON export script + tests

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: C# — ICWeightedAlphaModelV2 JSON 反序列化层（TDD）

**Files:**
- Create: `Algorithm.CSharp/Models/Alpha/ICWeightedAlphaModelV2.cs`
- Test: `Tests/Common/Algorithm/ICWeightedAlphaModelV2Tests.cs`

- [ ] **Step 1: 写第一个失败测试 — 读合法 JSON 取最新报告**

创建 `Tests/Common/Algorithm/ICWeightedAlphaModelV2Tests.cs`：

```csharp
/*
 * TDD tests for ICWeightedAlphaModelV2 — verifies JSON IC-report loading,
 * latest-report selection, and fallback behavior. Tests inject a temp JSON
 * directory so they are deterministic and do not touch real parquet data.
 */
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using NUnit.Framework;
using QuantConnect.Algorithm.CSharp.Models.Alpha;

namespace QuantConnect.Tests.Common.Algorithm
{
    [TestFixture]
    public class ICWeightedAlphaModelV2Tests
    {
        private string _tempDir;

        [SetUp]
        public void SetUp()
        {
            _tempDir = Path.Combine(Path.GetTempPath(), "ic-v2-test-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(_tempDir);
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(_tempDir)) Directory.Delete(_tempDir, true);
        }

        private void WriteReport(string date, params (string id, double ic, double ir, double weight)[] factors)
        {
            var payload = new
            {
                report_date = date,
                ic_window_start = "2020-01-02",
                ic_window_end = date,
                horizon = 21,
                min_ic = 0.02,
                min_ir = 0.3,
                factors = factors.Select(f => new
                {
                    factor_id = f.id,
                    rank_ic_mean = f.ic,
                    rank_ic_ir = f.ir,
                    weight = f.weight
                }).ToList()
            };
            File.WriteAllText(
                Path.Combine(_tempDir, $"ic_report_{date}.json"),
                JsonConvert.SerializeObject(payload, Formatting.Indented));
        }

        [Test]
        public void SelectLatestReport_ReadsJsonAndPicksLatestAtOrBeforeDate()
        {
            WriteReport("2024-01-31", ("alpha012", 0.052, 0.42, 0.6), ("alpha077", 0.048, 0.39, 0.4));
            WriteReport("2024-02-29", ("alpha012", 0.055, 0.45, 0.5), ("alpha001", 0.040, 0.35, 0.5));

            var model = new ICWeightedAlphaModelV2(_tempDir);
            var report = model.LoadLatestReport(new DateTime(2024, 3, 15));

            Assert.IsNotNull(report);
            Assert.AreEqual("2024-02-29", report.ReportDate);
            Assert.AreEqual(2, report.Factors.Count);
            Assert.AreEqual("alpha012", report.Factors[0].FactorId);
            Assert.AreEqual(0.5, report.Factors[0].Weight, 1e-9);
        }
    }
}
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~ICWeightedAlphaModelV2Tests.SelectLatestReport_ReadsJsonAndPicksLatestAtOrBeforeDate" 2>&1 | tail -20`
Expected: FAIL — type `ICWeightedAlphaModelV2` not found

- [ ] **Step 3: 实现 ICWeightedAlphaModelV2.cs**

创建 `Algorithm.CSharp/Models/Alpha/ICWeightedAlphaModelV2.cs`：

```csharp
/*
 * ICWeightedAlphaModelV2.cs — IC-Weighted Alpha Model V2 (JSON-driven)
 *
 * Replaces the hardcoded 49-factor list in ICWeightedAlphaModel with a dynamic
 * model reading offline expanding-window IC reports from JSON.
 *
 * ZERO INTRUSION: new file. Old ICWeightedAlphaModel is not modified.
 *
 * Flow:
 *   1. On rebalance, LoadLatestReport(date) scans icReportDir for the newest
 *      ic_report_YYYY-MM-DD.json with date <= rebalance date.
 *   2. Deserialize factors + weights via Newtonsoft.Json.
 *   3. For each active security, pull each factor via FactorStore.Get and
 *      compute a weighted composite score (cross-sectional normalization,
 *      same logic as ICWeightedAlphaModel.ComputeAlphaScores).
 *   4. Select top quantile, emit Price insights (weight = 1/n).
 *
 * Fallbacks: missing dir, no report <= date, corrupt JSON, all-invalid factors
 * -> equal-weight fallback or no insight, with logs. Never crashes.
 */
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Factors.Core;
using QuantConnect.Factors.Store;
using QuantConnect.Logging;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp.Models.Alpha
{
    public class ICWeightedAlphaModelV2 : IAlphaModel
    {
        private readonly string _icReportDir;
        private readonly int _rebalanceMonths;
        private readonly int _insightPeriodDays;
        private readonly decimal _topQuantile;
        private readonly FactorStore _store;

        private int _lastRebalanceYear = -1;
        private int _lastRebalanceMonth = -1;

        public string Name => "ICWeightedAlphaV2";

        public ICWeightedAlphaModelV2(
            string icReportDir,
            int rebalanceMonths = 1,
            int insightPeriodDays = 21,
            decimal topQuantile = 0.10m,
            FactorStore store = null)
        {
            _icReportDir = icReportDir ?? throw new ArgumentNullException(nameof(icReportDir));
            _rebalanceMonths = rebalanceMonths;
            _insightPeriodDays = insightPeriodDays;
            _topQuantile = topQuantile;
            _store = store ?? new FactorStore();
        }

        public ICReport LoadLatestReport(DateTime asOf)
        {
            if (!Directory.Exists(_icReportDir))
            {
                Log.Error($"[ICWeightedAlphaV2] IC report dir not found: {_icReportDir}");
                return null;
            }

            var files = Directory.GetFiles(_icReportDir, "ic_report_*.json");
            if (files.Length == 0)
            {
                Log.Error($"[ICWeightedAlphaV2] No IC reports in {_icReportDir}");
                return null;
            }

            ICReport latest = null;
            DateTime latestDate = DateTime.MinValue;
            ICReport earliest = null;
            DateTime earliestDate = DateTime.MaxValue;

            foreach (var file in files)
            {
                var name = Path.GetFileNameWithoutExtension(file);
                var dateStr = name.Substring("ic_report_".Length);
                if (!DateTime.TryParseExact(dateStr, "yyyy-MM-dd", CultureInfo.InvariantCulture,
                        DateTimeStyles.None, out var reportDate)) continue;

                ICReport parsed = null;
                try
                {
                    parsed = JsonConvert.DeserializeObject<ICReport>(File.ReadAllText(file));
                }
                catch (Exception ex)
                {
                    Log.Error($"[ICWeightedAlphaV2] Failed to parse {file}: {ex.Message}");
                    continue;
                }

                if (reportDate < earliestDate)
                {
                    earliestDate = reportDate;
                    earliest = parsed;
                }
                if (reportDate <= asOf && reportDate > latestDate)
                {
                    latestDate = reportDate;
                    latest = parsed;
                }
            }

            if (latest != null) return latest;
            if (earliest != null)
            {
                Log.Trace($"[ICWeightedAlphaV2] No report <= {asOf:yyyy-MM-dd}; "
                          + $"falling back to earliest {earliestDate:yyyy-MM-dd}");
                return earliest;
            }
            return null;
        }

        public IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            if (!IsRebalanceMonth(algorithm.Time)) yield break;

            var symbols = algorithm.ActiveSecurities.Keys.ToList();
            if (symbols.Count == 0) yield break;

            var report = LoadLatestReport(algorithm.Time);
            if (report == null || report.Factors == null || report.Factors.Count == 0)
            {
                algorithm.Debug($"[ICWeightedAlphaV2] {algorithm.Time:yyyy-MM-dd}: no IC report, skipping");
                yield break;
            }

            var dateStr = algorithm.Time.ToString("yyyy-MM-dd");
            var alphaScores = ComputeAlphaScores(symbols, algorithm.Time, report);

            if (alphaScores.Count == 0)
            {
                algorithm.Debug($"[ICWeightedAlphaV2] {dateStr}: no alpha scores computed");
                yield break;
            }

            var ranked = alphaScores.OrderByDescending(x => x.Value).ToList();
            var n = Math.Max(1, (int)Math.Floor(ranked.Count * (double)_topQuantile));
            var selected = ranked.Take(n).ToList();

            var w = 1.0 / selected.Count;
            foreach (var (symbol, score) in selected)
            {
                yield return Insight.Price(
                    symbol,
                    TimeSpan.FromDays(_insightPeriodDays),
                    InsightDirection.Up,
                    magnitude: (double)score,
                    confidence: null,
                    sourceModel: Name,
                    weight: w);
            }

            algorithm.Debug($"[ICWeightedAlphaV2] {dateStr}: selected={selected.Count}, " +
                            $"factors={report.Factors.Count}, report={report.ReportDate}");

            _lastRebalanceYear = algorithm.Time.Year;
            _lastRebalanceMonth = algorithm.Time.Month;
        }

        private Dictionary<Symbol, double> ComputeAlphaScores(
            List<Symbol> symbols, DateTime asOf, ICReport report)
        {
            var scores = new Dictionary<Symbol, double>();

            foreach (var symbol in symbols)
            {
                double compositeScore = 0;
                double totalWeight = 0;

                foreach (var f in report.Factors)
                {
                    var result = _store.Get(f.FactorId, symbol, asOf);
                    if (result.Quality == FactorDataQuality.Valid)
                    {
                        compositeScore += (double)result.Value * f.Weight;
                        totalWeight += f.Weight;
                    }
                }

                if (totalWeight > 0)
                    scores[symbol] = compositeScore / totalWeight;
            }

            if (scores.Count > 0)
            {
                var min = scores.Values.Min();
                var max = scores.Values.Max();
                if (max > min)
                {
                    foreach (var symbol in scores.Keys.ToList())
                        scores[symbol] = (scores[symbol] - min) / (max - min);
                }
            }

            return scores;
        }

        public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes) { }

        private bool IsRebalanceMonth(DateTime now)
        {
            if (_lastRebalanceYear < 0) return true;
            if (now.Year == _lastRebalanceYear && now.Month == _lastRebalanceMonth) return false;
            var elapsed = (now.Year - _lastRebalanceYear) * 12 + (now.Month - _lastRebalanceMonth);
            return elapsed >= _rebalanceMonths;
        }
    }

    public class ICReport
    {
        [JsonProperty("report_date")] public string ReportDate { get; set; }
        [JsonProperty("ic_window_start")] public string IcWindowStart { get; set; }
        [JsonProperty("ic_window_end")] public string IcWindowEnd { get; set; }
        [JsonProperty("horizon")] public int Horizon { get; set; }
        [JsonProperty("min_ic")] public double MinIc { get; set; }
        [JsonProperty("min_ir")] public double MinIr { get; set; }
        [JsonProperty("factors")] public List<ICReportFactor> Factors { get; set; } = new();
    }

    public class ICReportFactor
    {
        [JsonProperty("factor_id")] public string FactorId { get; set; }
        [JsonProperty("rank_ic_mean")] public double RankIcMean { get; set; }
        [JsonProperty("rank_ic_ir")] public double RankIcIr { get; set; }
        [JsonProperty("weight")] public double Weight { get; set; }
    }
}
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~ICWeightedAlphaModelV2Tests.SelectLatestReport_ReadsJsonAndPicksLatestAtOrBeforeDate" 2>&1 | tail -20`
Expected: PASS

- [ ] **Step 5: 补充测试 — 无 ≤ 当前日的报告时用最早报告**

在 `ICWeightedAlphaModelV2Tests.cs` 类内追加：

```csharp
        [Test]
        public void LoadLatestReport_NoReportAtOrBeforeDate_FallsBackToEarliest()
        {
            WriteReport("2024-06-30", ("alpha012", 0.052, 0.42, 1.0));

            var model = new ICWeightedAlphaModelV2(_tempDir);
            var report = model.LoadLatestReport(new DateTime(2024, 1, 1));

            Assert.IsNotNull(report, "should fall back to earliest report when none <= asOf");
            Assert.AreEqual("2024-06-30", report.ReportDate);
        }
```

- [ ] **Step 6: 运行测试验证通过**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~ICWeightedAlphaModelV2Tests" 2>&1 | tail -20`
Expected: PASS (两个测试)

- [ ] **Step 7: 补充测试 — 目录不存在返回 null**

在 `ICWeightedAlphaModelV2Tests.cs` 类内追加：

```csharp
        [Test]
        public void LoadLatestReport_MissingDir_ReturnsNull()
        {
            var model = new ICWeightedAlphaModelV2("/nonexistent/path/xyz");
            Assert.DoesNotThrow(() =>
            {
                var report = model.LoadLatestReport(new DateTime(2024, 6, 30));
                Assert.IsNull(report);
            });
        }
```

- [ ] **Step 8: 运行测试验证通过**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~ICWeightedAlphaModelV2Tests" 2>&1 | tail -20`
Expected: PASS (三个测试)

- [ ] **Step 9: 补充测试 — 损坏 JSON 不 crash，跳过该文件**

在 `ICWeightedAlphaModelV2Tests.cs` 类内追加：

```csharp
        [Test]
        public void LoadLatestReport_CorruptJson_SkipsFileDoesNotCrash()
        {
            WriteReport("2024-01-31", ("alpha012", 0.052, 0.42, 1.0));
            File.WriteAllText(Path.Combine(_tempDir, "ic_report_2024-02-29.json"),
                "{ this is not valid json }");

            var model = new ICWeightedAlphaModelV2(_tempDir);
            var report = model.LoadLatestReport(new DateTime(2024, 6, 30));
            Assert.IsNotNull(report);
            Assert.AreEqual("2024-01-31", report.ReportDate);
        }
```

- [ ] **Step 10: 运行测试验证通过**

Run: `cd /home/project/hope/Lean && dotnet test Tests/QuantConnect.Tests.csproj --filter "FullyQualifiedName~ICWeightedAlphaModelV2Tests" 2>&1 | tail -20`
Expected: PASS (四个测试)

- [ ] **Step 11: 提交**

```bash
cd /home/project/hope/Lean
git add Algorithm.CSharp/Models/Alpha/ICWeightedAlphaModelV2.cs Tests/Common/Algorithm/ICWeightedAlphaModelV2Tests.cs
git commit -m "feat(p2-a): ICWeightedAlphaModelV2 JSON-driven alpha model + NUnit tests

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: C# — 装配 V2 的新策略 + Config

**Files:**
- Create: `Algorithm.CSharp/AShareCSI300EnhancedV2Strategy.cs`
- Create: `Launcher/config_enhanced_v2.json`

- [ ] **Step 1: 创建 AShareCSI300EnhancedV2Strategy.cs**

```csharp
/*
 * AShareCSI300EnhancedV2Strategy — V2 of the Enhanced 5-layer strategy.
 *
 * Identical to AShareCSI300EnhancedStrategy EXCEPT Layer 2 uses
 * ICWeightedAlphaModelV2 (reads offline expanding-window IC report JSON)
 * instead of the hardcoded ICWeightedAlphaModel. The old strategy is NOT
 * modified — this is a new, independent strategy class.
 *
 * LEAN Framework 五层架构:
 *   Layer 1 (Universe):  AShareCSI300UniverseSelectionModel (沪深300 月度刷新)
 *   Layer 2 (Alpha):     ICWeightedAlphaModelV2 (JSON IC report, dynamic factors)
 *   Layer 3 (Portfolio): AlphaWeightedMVOPortfolioConstructionModel
 *   Layer 4 (Risk):      MaximumDrawdownPercentPortfolio (0.20)
 *   Layer 5 (Execution): AShareLotSizeExecutionModel (100 股整手)
 */
using System;
using System.Linq;
using QuantConnect.Algorithm.CSharp.Models.Alpha;
using QuantConnect.Algorithm.CSharp.Models.Portfolio;
using QuantConnect.Algorithm.CSharp.UniverseSelection;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Python;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    public class AShareCSI300EnhancedV2Strategy : QCAlgorithm
    {
        public override void Initialize()
        {
            SetStartDate(2024, 1, 2);
            SetEndDate(2026, 6, 29);

            SetAccountCurrency(Currencies.CNY);
            SetCash(1_000_000);
            SetTimeZone(TimeZones.Shanghai);
            SetBenchmark(_ => 0m);

            Settings.MinimumOrderMarginPortfolioPercentage = 0;
            UniverseSettings.Resolution = Resolution.Daily;

            SetSecurityInitializer(new AShareStockSecurityInitializer());
            PythonInitializer.Initialize();

            var dataRoot = GetParameterOrDefault("dataRoot",
                "/home/project/tushare-downloader/tushare_data_v2");
            var rootPath = GetParameterOrDefault("rootPath", Globals.DataFolder);
            var icReportDir = GetParameterOrDefault("icReportDir",
                "/home/project/hope/Lean/result/ic-reports");

            SetUniverseSelection(new AShareCSI300UniverseSelectionModel(
                dataRoot: dataRoot,
                rootPath: rootPath,
                indexCode: "000300.SH",
                refreshMonths: 1));

            int rebalanceDays = GetParameter("rebalance-days", 21);
            SetAlpha(new ICWeightedAlphaModelV2(
                icReportDir: icReportDir,
                rebalanceMonths: 1,
                insightPeriodDays: rebalanceDays,
                topQuantile: 0.10m));

            SetPortfolioConstruction(new AlphaWeightedMVOPortfolioConstructionModel(
                maxWeight: 0.10m,
                minWeight: 0.0m,
                rebalanceResolution: Resolution.Daily));

            SetRiskManagement(new MaximumDrawdownPercentPortfolio(0.20m));
            SetExecution(new AShareLotSizeExecutionModel());

            SetWarmUp(60, Resolution.Daily);

            Log("[AShareCSI300-EnhancedV2] 五层架构初始化完成:");
            Log("  L1 Universe:     AShareCSI300UniverseSelectionModel (000300.SH)");
            Log($"  L2 Alpha:        ICWeightedAlphaModelV2 (JSON: {icReportDir})");
            Log("  L3 Portfolio:    AlphaWeightedMVOPortfolioConstructionModel");
            Log("  L4 Risk:         MaximumDrawdownPercentPortfolio (0.20)");
            Log("  L5 Execution:    AShareLotSizeExecutionModel (100股整手)");
        }

        public override void OnEndOfAlgorithm()
        {
            Log($"[AShareCSI300-EnhancedV2] Final portfolio value: {Portfolio.TotalPortfolioValue:N2} CNY");
            Log($"[AShareCSI300-EnhancedV2] Total trades: {Transactions.OrdersCount}");
        }

        private string GetParameterOrDefault(string key, string defaultValue)
        {
            var val = GetParameter(key);
            return string.IsNullOrEmpty(val) ? defaultValue : val;
        }
    }
}
```

- [ ] **Step 2: 创建 config_enhanced_v2.json**

创建 `Launcher/config_enhanced_v2.json`：

```json
{
  "environment": "backtesting",
  "algorithm-type-name": "AShareCSI300EnhancedV2Strategy",
  "algorithm-language": "CSharp",
  "algorithm-location": "QuantConnect.Algorithm.CSharp.dll",
  "data-folder": "../../../Data/",
  "debugging": false,
  "log-level": "info",
  "metrics-stats": true,
  "results-destination-folder": "../../../Results/EnhancedStrategyV2",
  "parameters": {
    "rebalance-days": "21",
    "dataRoot": "/home/project/tushare-downloader/tushare_data_v2",
    "rootPath": "/home/project/hope/Lean",
    "icReportDir": "/home/project/hope/Lean/result/ic-reports"
  }
}
```

- [ ] **Step 3: 构建 Algorithm.CSharp 验证编译**

Run: `cd /home/project/hope/Lean && dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj --nologo 2>&1 | tail -15`
Expected: Build succeeded, 0 errors

- [ ] **Step 4: 提交**

```bash
cd /home/project/hope/Lean
git add Algorithm.CSharp/AShareCSI300EnhancedV2Strategy.cs Launcher/config_enhanced_v2.json
git commit -m "feat(p2-a): AShareCSI300EnhancedV2Strategy + config (assembles ICWeightedAlphaModelV2)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: 离线生成 IC 报告 + 集成回测验证

**Files:**
- Generate: `result/ic-reports/ic_report_*.json` (not in git)

- [ ] **Step 1: 生成首批 IC 报告（2020-02 ~ 2024-06 月端）**

expanding window 从 2020-01-02 起，最早可生成的有意义月端报告约 2020-02-28。

Run: `cd /home/project/hope/Lean/Scripts/factor_zoo && python export_ic_reports.py --start 2020-02-28 --end 2024-06-28 --ic-window-start 2020-01-02 --out-dir /home/project/hope/Lean/result/ic-reports 2>&1 | tail -30`

Expected: 逐月写出 JSON，最后一行 `Done: N JSON files written`。N ≈ 53。首次较慢（`result/ic-cache/` 会缓存，失败可重跑跳过已生成）。

- [ ] **Step 2: 验证生成的 JSON 合法 + 覆盖回测起点**

Run: `cd /home/project/hope/Lean && ls result/ic-reports/ic_report_*.json | wc -l && python -c "import json,glob; fs=sorted(glob.glob('result/ic-reports/ic_report_*.json')); print('first:',fs[0].split('/')[-1]); print('last:',fs[-1].split('/')[-1]); d=json.load(open(fs[-1])); print('last factors:',len(d['factors'])); print('weight sum:',round(sum(f['weight'] for f in d['factors']),6))"`

Expected: 文件数 ~53；first ≈ `ic_report_2020-02-28.json`；last = `ic_report_2024-06-28.json`；last factors ≥ 1；weight sum ≈ 1.0

- [ ] **Step 3: 构建 Launcher + 跑回测验证非零订单**

Run: `cd /home/project/hope/Lean && dotnet build Launcher/QuantConnect.Lean.Launcher.csproj --nologo 2>&1 | tail -5`

Run: `cd /home/project/hope/Lean/Launcher/bin/Debug && timeout 600 dotnet QuantConnect.Lean.Launcher.dll --config ../../../Launcher/config_enhanced_v2.json 2>&1 | grep -E "ICWeightedAlphaV2|selected=|Total trades|OrderEvent|Fill" | head -30`

Expected: 日志出现 `[ICWeightedAlphaV2] ... selected=N, factors=M, report=...`，且有 `OrderEvent`/`Fill`（非零订单）。若 `Total trades: 0`，按 [[ashare_lean_price_scaling]] 排查 daily zip 是否真元价格（非 10000x 缩放）。

- [ ] **Step 4: 验证旧代码零改动**

Run: `cd /home/project/hope/Lean && git diff --name-only HEAD~3 -- Algorithm.CSharp/Models/Alpha/ICWeightedAlphaModel.cs Algorithm.CSharp/AShareCSI300EnhancedStrategy.cs Launcher/config_enhanced.json`

Expected: 空输出（三个旧文件在 Task 1-3 commit 范围内零改动）。

- [ ] **Step 5: 推送**

```bash
cd /home/project/hope/Lean
git push origin feat/csi300-alpha101-composite
```

- [ ] **Step 6: 确认无临时文件遗留**

Run: `cd /home/project/hope/Lean && git status --short`

Expected: 干净（`result/ic-reports/` 不入 git）。不要 `git add result/`。

---

## Self-Review

**1. Spec coverage:**
- 组件 1 离线 export 脚本 → Task 1 ✓
- JSON Schema → Task 1 测试 + Task 2 ICReport 类字段 ✓
- 组件 2 C# V2 模型（读 JSON、选最新、composite score、降级）→ Task 2 ✓
- 降级表（目录缺失/无报告/JSON 损坏/因子无效）→ Task 2 测试覆盖目录缺失、无≤当前日、JSON损坏；因子无效沿袭旧逻辑 ✓
- 组件 3 策略 + config → Task 3 ✓
- 测试策略（pytest + NUnit + 集成）→ Task 1/2/4 ✓
- 验证标准（JSON产出/测试全绿/非零订单/旧代码零改动/推送）→ Task 4 ✓

**2. Placeholder scan:** 无 TBD/TODO。所有代码块完整。命令含 expected output。

**3. Type consistency:**
- `ICReport.ReportDate` / `Factors` / `Horizon` — Task 2 定义，测试用 `report.ReportDate` / `report.Factors.Count` 一致 ✓
- `ICReportFactor.FactorId` / `Weight` — Task 2 定义，`ComputeAlphaScores` 用 `f.FactorId` / `f.Weight` 一致 ✓
- `LoadLatestReport(DateTime)` — Task 2 定义，测试调用签名一致 ✓
- `export_ic_reports(month_ends, ic_window_start, horizon, min_ic, min_ir, out_dir, force, engine)` — Task 1 定义，测试调用一致 ✓
- JSON 字段名 `report_date`/`ic_window_start`/`factors`/`factor_id`/`weight` — Python 写出与 C# `[JsonProperty]` 一一对应 ✓

无问题。计划完整。
