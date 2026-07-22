"""STEP 0a: verify CSI300 + CSI500 universe is usable + consistent (spec §7).

Reads index_weight the same way BarraCNE5DataLoader.load_index_constituents
does (data-source/tushare/barra_cne5_data_loader.py:132): trade_date partitions
with ALL index_codes mixed per file, filtered by the `index_code` column value
(not filename glob). Confirms which index_codes resolve to member lists, the
deduped union, and flags any unresolved (e.g. 000905.SH if not backfilled).

Spec §6/§7 reference: a CSI500(000905.SH) backfill was listed as P0. If the
§6 audit diagnosed absence via filename glob, that would be a misdiagnosis
(index_codes are VALUES inside files, not in filenames). This script reads
files properly: if 000905.SH resolves, the §6 "missing" claim is refuted
(was a glob artifact); if it lands in `unresolved`, the claim stands and the
backfill in §6.1 is genuinely required.

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
            print("  -> 000905.SH here means §6 'missing' claim stands; backfill required (§6.1 P0).")
            print("     000905.SH absent means §6 'missing' claim was refuted (glob artifact).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
