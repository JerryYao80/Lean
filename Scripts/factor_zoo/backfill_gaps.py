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
