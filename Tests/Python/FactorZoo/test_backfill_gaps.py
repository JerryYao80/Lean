"""Tests for backfill_gaps.py gap selection (spec §6 remediation)."""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

# test file lives at Tests/Python/FactorZoo/test_*.py so parents[3] is repo root
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "Scripts"))

from factor_zoo.audit_tushare_coverage import TableReport, CoverageStatus
from factor_zoo.backfill_gaps import select_gaps, BackfillPlan, BackfillTarget, _run_incremental


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


def test_run_incremental_timeout_marks_batch_failed():
    """If subprocess.run raises TimeoutExpired, every API in the batch is marked failed."""
    targets = [
        BackfillTarget(api_name="income", start_date="20260515",
                       end_date="20260722", via="incremental"),
        BackfillTarget(api_name="balancesheet", start_date="20260515",
                       end_date="20260722", via="incremental"),
    ]
    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=kwargs.get("cmd") or args[0],
                                        timeout=kwargs.get("timeout", 3600))
    with patch("factor_zoo.backfill_gaps.subprocess.run",
               side_effect=_raise_timeout):
        results = _run_incremental(targets, dry_run=False, timeout=3600)
    assert len(results) == 2
    for r in results:
        assert r["ok"] is False
        assert "TIMEOUT" in r["stderr_tail"]
