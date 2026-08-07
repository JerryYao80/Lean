#!/usr/bin/env python3
"""fix_ashare_market_hours.py — Fix SSE/SZSE Equity entries in market-hours-database.json.

The SSE/SZSE Equity entries were incorrectly set to 24/7 "market" with empty
holidays. This script replaces them with the correct A-share trading sessions
(Mon-Fri 09:30-11:30 / 13:00-15:00, Sat/Sun closed) and populates the holidays
array from tushare trade_cal (is_open=0 dates, 2010-2026).

Holiday date format matches LEAN convention: M/D/YYYY (no leading zeros),
e.g. "1/1/2024". Mirrors the USA entry format in the same file.

Usage:
    python3 fix_ashare_market_hours.py [--dry-run]
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

TRADE_CAL = Path("/home/project/tushare-downloader/tushare_data_v2/trade_cal/data.parquet")
MH_DB = Path("/home/project/hope/Lean/Data/market-hours/market-hours-database.json")

# A-share continuous trading sessions (Asia/Shanghai local time)
# Morning: 09:30-11:30, Afternoon: 13:00-15:00
MARKET_SESSION = {"start": "09:30:00", "end": "11:30:00", "state": "market"}
AFTERNOON_SESSION = {"start": "13:00:00", "end": "15:00:00", "state": "market"}

# Mon-Fri have both sessions; Sat/Sun closed (empty list)
WEEKDAYS = {
    "monday": [dict(MARKET_SESSION), dict(AFTERNOON_SESSION)],
    "tuesday": [dict(MARKET_SESSION), dict(AFTERNOON_SESSION)],
    "wednesday": [dict(MARKET_SESSION), dict(AFTERNOON_SESSION)],
    "thursday": [dict(MARKET_SESSION), dict(AFTERNOON_SESSION)],
    "friday": [dict(MARKET_SESSION), dict(AFTERNOON_SESSION)],
    "saturday": [],
    "sunday": [],
}


def load_holidays():
    """Return sorted list of holiday date strings in M/D/YYYY format."""
    df = pd.read_parquet(TRADE_CAL)
    # trade_cal has one row per calendar day per exchange; SSE covers A-share holidays.
    sse = df[df["exchange"] == "SSE"]
    closed = sse[sse["is_open"] == 0]
    holidays = []
    for d in closed["cal_date"].astype(str):
        # cal_date is YYYYMMDD string (e.g. "20240101")
        y, m, day = d[:4], int(d[4:6]), int(d[6:8])
        holidays.append(f"{m}/{day}/{y}")
    return sorted(holidays, key=lambda s: tuple(int(x) for x in s.split("/")[::-1]))


def build_entry(holidays):
    """Build a complete market-hours entry for SSE/SZSE Equity."""
    entry = {
        "dataTimeZone": "Asia/Shanghai",
        "exchangeTimeZone": "Asia/Shanghai",
    }
    entry.update({k: v for k, v in WEEKDAYS.items()})
    entry["holidays"] = holidays
    entry["earlyCloses"] = {}
    entry["lateOpens"] = {}
    return entry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print changes, don't write")
    args = ap.parse_args()

    holidays = load_holidays()
    print(f"Loaded {len(holidays)} holidays from trade_cal (SSE, is_open=0)")
    print(f"  range: {holidays[0]} -> {holidays[-1]}")

    raw = MH_DB.read_text()
    db = json.loads(raw)
    # Top-level structure is {"entries": {<key>: <entry>, ...}}
    entries = db.get("entries", db)

    changed = []
    for key in ("Equity-sse-[*]", "Equity-szse-[*]"):
        if key not in entries:
            print(f"WARN: {key} not found in DB, skipping")
            continue
        old = entries[key]
        new = build_entry(holidays)
        if old != new:
            entries[key] = new
            changed.append(key)

    if not changed:
        print("No changes needed (already correct).")
        return

    print(f"Updating entries: {changed}")
    if args.dry_run:
        print("[dry-run] would write updated DB.")
        for k in changed:
            print(f"  {k}: holidays={len(holidays)}, sessions=Mon-Fri 09:30-11:30/13:00-15:00")
        return

    # Preserve 2-space indentation (matching the existing file style)
    out = json.dumps(db, indent=2, ensure_ascii=False) + "\n"
    MH_DB.write_text(out)
    print(f"Wrote {MH_DB} ({len(out)} bytes)")
    print("  Mon-Fri: 09:30-11:30 + 13:00-15:00 (market)")
    print("  Sat/Sun: closed")
    print(f"  holidays: {len(holidays)} dates from trade_cal")


if __name__ == "__main__":
    sys.exit(main())
