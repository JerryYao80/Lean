"""Parallel backfill of the 8 hand-picked alpha101 factors for CSI300.

Drives data-source/tushare/alpha101/builder.build_day per trade day across N
worker processes. build_day writes to result_root/factor-zoo/<aid>/<date>/
(safe to parallelize — each date writes a distinct date_dir, no shared state).

Existing serial backfill_alpha101_csi300.py takes ~89s/day → 541 missing days
= ~13.3h. With 3 workers (4 cores, leave 1 for OS) that drops to ~4.5h.

This is an ops script, not a feature: it is idempotent (re-running a day just
overwrites its date_dir) and crash-resume (skips days that already have a
non-empty alpha001 date_dir).

Usage:
    python backfill_alpha101_csi300_parallel.py --start 2024-04-02 --end 2026-06-29 --workers 3
"""
from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO), str(_REPO / "data-source" / "tushare"),
           str(_REPO / "Scripts" / "factor_zoo")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

log = logging.getLogger("backfill_parallel")


def _backfill_one(date_yyyy_mm_dd: str, csi300: list[str], result_root: str) -> tuple[str, dict]:
    """Worker: build_day for one date. Returns (date, result-dict)."""
    from alpha101.builder import build_day  # import inside worker for pickle-safety
    try:
        res = build_day(date_yyyy_mm_dd, csi300, result_root=result_root)
        return date_yyyy_mm_dd, res
    except Exception as exc:  # noqa: BLE001
        return date_yyyy_mm_dd, {"rows": 0, "failed": {"_exception": f"{type(exc).__name__}: {exc}"}}


def main() -> int:
    p = argparse.ArgumentParser(description="Parallel backfill 8 alpha101 factors for CSI300")
    p.add_argument("--start", default="2024-01-02")
    p.add_argument("--end", default="2026-06-29")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--result-root", default=str(_REPO / "result"))
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from backfill_alpha101_csi300 import trade_days_in_range, load_csi300_ts_codes

    dates = trade_days_in_range(args.start, args.end)
    log.info("trade days %s..%s: %d days", args.start, args.end, len(dates))
    if not dates:
        log.error("no trade days in range; aborting")
        return 1

    # Crash-resume: skip days that already have a non-empty alpha001 date_dir.
    # alpha001 is the canary — build_day writes all 8 alphas together, so if
    # alpha001/<date>/ exists and is non-empty, all 8 were written.
    canary = Path(args.result_root) / "factor-zoo" / "alpha001"
    already = set()
    if canary.exists():
        for d in canary.iterdir():
            if d.is_dir() and any(d.glob("*.parquet")):
                already.add(d.name)
    todo = [d for d in dates if d not in already]
    log.info("already backfilled: %d, to-backfill: %d", len(already), len(todo))
    if not todo:
        log.info("nothing to do")
        return 0

    csi300 = load_csi300_ts_codes(dates[-1].replace("-", ""))
    log.info("CSI300 universe size: %d (asof %s)", len(csi300), dates[-1])

    done = 0
    failed_days = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_backfill_one, d, csi300, args.result_root): d for d in todo}
        for fut in as_completed(futs):
            d, res = fut.result()
            done += 1
            rows = res.get("rows", 0)
            failed = res.get("failed", {})
            if failed and (not rows or any(k == "_exception" for k in failed)):
                failed_days.append((d, failed))
                log.warning("[%d/%d] %s FAILED rows=%s failed=%s", done, len(todo), d, rows, failed)
            else:
                log.info("[%d/%d] %s rows=%s", done, len(todo), d, rows)

    log.info("done: %d/%d succeeded, %d failed", done - len(failed_days), len(todo), len(failed_days))
    if failed_days:
        log.error("failed days: %s", [d for d, _ in failed_days])
    return 0 if not failed_days else 2


if __name__ == "__main__":
    raise SystemExit(main())
