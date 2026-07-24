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
