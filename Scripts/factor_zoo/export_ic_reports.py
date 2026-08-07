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
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from ic_ir_engine import ICIREngine

LOGGER = logging.getLogger("export_ic_reports")

DEFAULT_IC_WINDOW_START = "2020-01-02"
DEFAULT_OUT_DIR = "/home/project/hope/Lean/result/ic-reports"


def _month_end_dates(start: str, end: str) -> list[str]:
    """All month-end dates in [start, end] that fall on an actual month-end;
    a trailing partial month is skipped.
    """
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
            # Trailing partial month — real month-end exceeds `end`; skip
            # (matches upstream ic_ir_engine._get_month_end_dates behavior).
            break
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
    ic_window_end: str,
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
        "ic_window_end": ic_window_end,
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

        try:
            # end_date = month_end - 1 day so _get_month_end_dates returns
            # month-ends strictly before month_end; the last IC observation is
            # the prior month-end, whose 21-day forward return has fully
            # materialized by month_end — no lookahead.
            month_end_dt = datetime.strptime(month_end, "%Y-%m-%d")
            ic_end = (month_end_dt - timedelta(days=1)).strftime("%Y-%m-%d")

            report_df = eng.compute_ic_report(
                start_date=ic_window_start,
                end_date=ic_end,
                horizon=horizon,
            )

            payload = _build_report_payload(
                report_df, min_ic, min_ir, month_end, ic_window_start, ic_end, horizon)
            if payload is None:
                LOGGER.warning("No factors passed filter for %s; skipping JSON", month_end)
                # FIX 3: on force=True with no passing factors, remove any stale
                # JSON left from a prior run so it isn't consumed as fresh.
                if force and json_path.exists():
                    json_path.unlink(missing_ok=True)
                    LOGGER.info("Deleted stale JSON for %s (no factors passed)", month_end)
                continue

            json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
            LOGGER.info("Wrote %s (%d factors)", json_path, len(payload["factors"]))
            written.append(json_path)
        except Exception:
            LOGGER.exception("Failed month %s", month_end)
            continue

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
