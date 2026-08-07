#!/usr/bin/env python3
"""Refresh the live_paper_strategy_catalog InfluxDB measurement.

Writes one point per current live-paper algorithm_id (SoloQuant + legacy) so the
Grafana dashboard dropdown variable (InfluxQL `SHOW TAG VALUES ... WHERE time > now()-1h`)
reflects ALL live-paper strategies -- not just the lean_chart writers.

Self-pruning: removed strategies stop being rewritten, so their points age out
past the variable's 1h window and drop off the list. Run via cron every ~5 min.

    */5 * * * * /root/miniconda3/envs/quant/bin/python3 /home/project/hope/Lean/Scripts/export_live_paper_catalog.py
"""
import json
import os
import sys
import urllib.request

API_URL = "http://localhost:5000/api/strategies/algorithm-ids"
# InfluxDB 1.x auth via u/p query params (same creds as the working /query calls).
INFLUX_WRITE = "http://localhost:8086/write?db=quant&u=admin&p=admin-token-leansystem"
MEASUREMENT = "live_paper_strategy_catalog"


def _escape_tag(value: str) -> str:
    for ch in (",", " ", "="):
        value = value.replace(ch, "\\" + ch)
    return value


def main() -> int:
    try:
        with urllib.request.urlopen(API_URL, timeout=10) as resp:
            ids = json.loads(resp.read().decode())
    except Exception as exc:
        print(f"ERROR: cannot reach {API_URL}: {exc}", file=sys.stderr)
        return 1

    if not isinstance(ids, list) or not ids:
        print("no algorithm_ids returned; nothing to write", file=sys.stderr)
        return 0

    lines = [
        f"{MEASUREMENT},algorithm_id={_escape_tag(str(aid))} present=1i"
        for aid in ids
    ]
    body = "\n".join(lines).encode()
    req = urllib.request.Request(INFLUX_WRITE, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as exc:
        print(f"ERROR: influx write failed: {exc}", file=sys.stderr)
        return 1

    print(f"wrote {len(ids)} algorithm_ids to {MEASUREMENT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
