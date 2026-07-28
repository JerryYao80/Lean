"""Backfill 8 hand-picked alpha101 factors for CSI300 universe, 2024-01 → 2026-06.

Drives data-source/tushare/alpha101/builder.build_day per trade day. CSI300
ts_codes loaded via BarraCNE5DataLoader.load_index_constituents("000300.SH")
(000905.SH is absent from index_weight — spec §1, CSI300-only degrade).

Usage:
    python backfill_alpha101_csi300.py --start 2024-01-02 --end 2026-06-29
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO), str(_REPO / "data-source" / "tushare"),
           str(_REPO / "Scripts" / "factor_zoo")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from alpha101.builder import build_day  # noqa: E402
from barra_cne5_data_loader import BarraCNE5DataLoader  # noqa: E402

# The 8 hand-picked cross-family alphas (spec §2.1). builder.build_day computes
# ALL 101 alphas per call — we list these 8 to document intent + let tests assert.
ALPHAS_TO_FILL = [
    "alpha001", "alpha006", "alpha030", "alpha040",
    "alpha042", "alpha055", "alpha058", "alpha101",
]

log = logging.getLogger("backfill_alpha101_csi300")


def load_csi300_ts_codes(asof_compact: str) -> list[str]:
    """CSI300 constituents (000300.SH) as of a date."""
    # BarraCNE5DataLoader requires data_root (matches factor_worker.py:165 which
    # passes TUSHARE_DATA_PATH). Use the same default as trade_days_in_range.
    tushare_path = os.environ.get(
        "TUSHARE_DATA_PATH",
        "/home/project/tushare-downloader/tushare_data_v2")
    loader = BarraCNE5DataLoader(tushare_path)
    return loader.load_index_constituents(asof_date=asof_compact, index_code="000300.SH")


def backfill_range(dates: list[str], csi300: list[str], result_root: str | None = None) -> None:
    """Call build_day for each date with the given CSI300 ts_codes list."""
    if not csi300:
        log.warning("CSI300 universe empty; skipping backfill.")
        return
    for d in dates:
        try:
            res = build_day(d, csi300, result_root=result_root)
            log.info("backfilled %s: rows=%s failed=%s",
                     d, res.get("rows"), res.get("failed", {}))
        except Exception as exc:  # noqa: BLE001
            log.error("backfill failed for %s: %s: %s", d, type(exc).__name__, exc)


def trade_days_in_range(start: str, end: str) -> list[str]:
    """Trade days (YYYY-MM-DD) between start and end inclusive, from trade_cal."""
    import pandas as pd
    # Match factor_worker's default (data-source/tushare/factor_worker.py:49-50):
    # /home/project/tushare-downloader/tushare_data_v2 (NOT _REPO.parent which
    # would resolve to /home/project/hope/tushare-downloader — wrong level).
    tushare_path = os.environ.get(
        "TUSHARE_DATA_PATH",
        "/home/project/tushare-downloader/tushare_data_v2")
    cal = pd.read_parquet(f"{tushare_path}/trade_cal/data.parquet")
    cal = cal[(cal["is_open"] == 1)
              & (cal["cal_date"].astype(str) >= start.replace("-", ""))
              & (cal["cal_date"].astype(str) <= end.replace("-", ""))]
    compact = sorted(cal["cal_date"].astype(str).tolist())
    return [f"{c[:4]}-{c[4:6]}-{c[6:]}" for c in compact]


def _cli() -> int:
    p = argparse.ArgumentParser(description="Backfill 8 alpha101 factors for CSI300")
    p.add_argument("--start", default="2024-01-02")
    p.add_argument("--end", default="2026-06-29")
    p.add_argument("--result-root", default=None)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    dates = trade_days_in_range(args.start, args.end)
    log.info("trade days %s..%s: %d days", args.start, args.end, len(dates))
    if not dates:
        log.error("no trade days in range; aborting")
        return 1

    csi300 = load_csi300_ts_codes(dates[-1].replace("-", ""))
    log.info("CSI300 universe size: %d", len(csi300))

    backfill_range(dates, csi300, result_root=args.result_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
