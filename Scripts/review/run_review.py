#!/usr/bin/env python3
"""run_review CLI entrypoint. Spec §5.2. Usage:
    python3 Scripts/review/run_review.py --manifest <manifest.yaml> --results-dir <Results/<run>>
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cli import run  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Trading-review (复盘) layer. Spec 2026-07-10.")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--results-dir", required=True)
    ap.add_argument("--html", action="store_true")
    ap.add_argument("--influx", action="store_true")
    ap.add_argument("--write-manifest", action="store_true")
    args = ap.parse_args()
    sys.exit(run(args.manifest, args.results_dir, args.html, args.influx, args.write_manifest))


if __name__ == "__main__":
    main()
