"""factor_worker — Phase 3 freshness daemon (spec §3.1).

Independent supervisor program that wires existing crowding/forward/Barra factor
builders into a daily incremental-freshness loop. Per cycle:
  1. T_cal = trade_cal max is_open=1 cal_date <= today (compact YYYYMMDD).
  2. T_raw = downloader scheduler state last_finished_target_date (READ-ONLY).
  3. If T_raw < T_cal -> skip (tushare not done downloading).
  4. For each registered builder: T_f = latest_date_resolver(); if T_f < T_cal
     queue open days in (T_f, T_cal] and call build_day per date.
  5. Single-builder failure is isolated (try/except); after POISON_THRESHOLD
     consecutive failures the factor is marked poisoned and skipped.
  6. Crash-resume via Results/factor-zoo/factor_worker_state.json (per-date
     checkpoint). Writes Results/factor-zoo/freshness.json + best-effort
     InfluxDB lean_factor_freshness (skipped if no INFLUXDB_TOKEN).

This daemon NEVER modifies existing builders and NEVER writes to the tushare
downloader repo. It only READS the downloader's scheduler state file.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib import parse, request
from zoneinfo import ZoneInfo

CHINA_TZ = ZoneInfo("Asia/Shanghai")

# Ensure the repo's importable dirs are on sys.path so the lazy builder imports
# resolve under any cwd (the daemon runs with directory=data-source/tushare, but
# the builders reach into Algorithm.Python/ + Scripts/factor_zoo/ + Scripts/).
# crowding_factor_builder imports CrowdingFactors (Algorithm.Python/); the Phase 5
# builders import pit_financials (Scripts/factor_zoo/) + their sibling modules.
_REPO = Path(__file__).resolve().parents[2]  # /home/project/hope/Lean
for _p in (str(_REPO), str(_REPO / "Algorithm.Python"),
           str(_REPO / "Scripts"), str(_REPO / "Scripts" / "factor_zoo"),
           str(_REPO / "data-source" / "tushare")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── module-level config (env-overridable; tests monkeypatch these) ──────────
TUSHARE_DATA_PATH = os.environ.get(
    "TUSHARE_DATA_PATH", "/home/project/tushare-downloader/tushare_data_v2"
)
SCHEDULER_STATE_FILE = Path(os.environ.get(
    "TUSHARE_SCHEDULER_STATE",
    "/home/project/tushare-downloader/download_state/incremental_scheduler_state.json",
))
REPO_ROOT = Path(__file__).resolve().parents[2]
FACTOR_ZOO_STATE_DIR = REPO_ROOT / "Results" / "factor-zoo"
STATE_FILE = FACTOR_ZOO_STATE_DIR / "factor_worker_state.json"
FRESHNESS_FILE = FACTOR_ZOO_STATE_DIR / "freshness.json"
CROWDING_RESULT_ROOT = os.environ.get("CROWDING_RESULT_ROOT", str(REPO_ROOT / "result"))
BARRA_OUTPUT_ROOT = REPO_ROOT / "Data" / "alternative" / "barra-cne5v2-factors"
INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
FRESHNESS_MEASUREMENT = "lean_factor_freshness"
POISON_THRESHOLD = 3
MAX_BACKFILL_DAYS = 60

LOGGER = logging.getLogger("factor_worker")
if not LOGGER.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    LOGGER.addHandler(_h)
    LOGGER.setLevel(logging.INFO)

_OPEN_TRUE = {"1", "True", "true"}


# ── T_cal resolver (inlined; mirrors audit_tushare_coverage._resolve_latest_trade_date) ─
def _resolve_latest_trade_date(data_root: str | Path) -> str | None:
    """Max trade_cal.cal_date where is_open=1 AND cal_date <= today (compact YYYYMMDD).

    Returns None if trade_cal parquet is missing/empty (caller falls back).
    """
    import pandas as pd
    cal_path = Path(data_root) / "trade_cal" / "data.parquet"
    if not cal_path.exists():
        cal_dir = Path(data_root) / "trade_cal"
        if not cal_dir.exists():
            return None
        frames = []
        for f in cal_dir.rglob("data.parquet"):
            try:
                frames.append(pd.read_parquet(f, columns=["cal_date", "is_open"]))
            except Exception:
                continue
        if not frames:
            return None
        df = pd.concat(frames, ignore_index=True)
    else:
        try:
            df = pd.read_parquet(cal_path, columns=["cal_date", "is_open"])
        except Exception:
            return None
    if "cal_date" not in df.columns or "is_open" not in df.columns:
        return None
    today = datetime.now(CHINA_TZ).strftime("%Y%m%d")
    open_dates = df.loc[
        (df["is_open"].astype(str).isin(_OPEN_TRUE))
        & (df["cal_date"].astype(str) <= today),
        "cal_date",
    ].astype(str)
    if open_dates.empty:
        return None
    return str(open_dates.max())


# ── T_raw resolver (READ-ONLY; never writes the downloader repo) ───────────
def _read_tushare_finished_date() -> str | None:
    """Read last_finished_target_date from the downloader's scheduler state."""
    if not SCHEDULER_STATE_FILE.exists():
        return None
    try:
        state = json.loads(SCHEDULER_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.warning("Failed to read scheduler state %s: %s", SCHEDULER_STATE_FILE, exc)
        return None
    return state.get("last_finished_target_date")


# ── crash-resume state ──────────────────────────────────────────────────────
def _load_state() -> dict:
    """Load factor_worker_state.json. Fresh start on missing/corrupt."""
    if not STATE_FILE.exists():
        return {"factors": {}, "updated_at": None}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.warning("State file corrupt, starting fresh: %s", exc)
        return {"factors": {}, "updated_at": None}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(CHINA_TZ).isoformat()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ── builder registry ────────────────────────────────────────────────────────
@dataclass
class FactorBuilder:
    factor_id: str
    build_callable: Callable[[str], dict]
    latest_date_resolver: Callable[[], str | None]
    depends_on: tuple[str, ...] = ()
    max_backfill_days: int = MAX_BACKFILL_DAYS


def _build_crowding(date_compact: str) -> dict:
    """Wire to crowding_factor_builder.build_day. date_compact is YYYYMMDD."""
    from crowding_factor_builder import build_day
    from barra_cne5_data_loader import BarraCNE5DataLoader
    date_yyyy_mm_dd = f"{date_compact[0:4]}-{date_compact[4:6]}-{date_compact[6:8]}"
    loader = BarraCNE5DataLoader(TUSHARE_DATA_PATH)
    ts_codes = loader.load_index_constituents(asof_date=date_compact, index_code="000300.SH")
    started = time.perf_counter()
    df = build_day(
        date_yyyy_mm_dd=date_yyyy_mm_dd,
        ts_codes=ts_codes,
        data_root=TUSHARE_DATA_PATH,
        result_root=CROWDING_RESULT_ROOT,
        write_influxdb=bool(INFLUX_TOKEN),
        influx_url=INFLUX_URL, influx_org=INFLUX_ORG,
        influx_bucket=INFLUX_BUCKET, influx_token=INFLUX_TOKEN,
    )
    return {"rows": int(len(df)), "duration_ms": int((time.perf_counter() - started) * 1000)}


def _resolve_crowding_latest() -> str | None:
    """Max yyyy-MM-dd dir under result/crowding-factor/ -> compact YYYYMMDD."""
    root = Path(CROWDING_RESULT_ROOT) / "crowding-factor"
    if not root.exists():
        return None
    best: str | None = None
    for sub in root.iterdir():
        if not sub.is_dir():
            continue
        try:
            datetime.strptime(sub.name, "%Y-%m-%d")
        except ValueError:
            continue
        compact = sub.name.replace("-", "")
        if best is None or compact > best:
            best = compact
    return best


def _build_forward(date_compact: str) -> dict:
    """Wire to export_forward_factors.run. date_compact is YYYYMMDD (forward wants compact)."""
    # export_forward_factors.py lives in Scripts/, not data-source/tushare/, so it is
    # not on sys.path under the daemon's cwd. Add Scripts/ before importing.
    scripts_dir = str(REPO_ROOT / "Scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from export_forward_factors import run, DEFAULT_TS_PATH
    started = time.perf_counter()
    summary = run(trade_date=date_compact, ts_path=TUSHARE_DATA_PATH or DEFAULT_TS_PATH, dry_run=False)
    summary["duration_ms"] = int((time.perf_counter() - started) * 1000)
    return summary


# ── Phase 5: 7 new factor builders (parquet to result/factor-zoo/<id>/) ─────
# Each _build_X wires the matching factor_builders.<id>_builder.build_day; the
# _resolve_X_latest scans result/factor-zoo/<id>/ for the max yyyy-MM-dd dir.
# Financial (annual PIT) factors use max_backfill_days=1 (value changes only on
# new annual disclosure); price-volume daily factors use the default 60.

def _make_factorzoo_builder(module_name: str, factor_id: str):
    """Build a (build_callable, latest_date_resolver) pair for a Phase 5 factor.

    factor_id is the on-disk subdir under result/factor-zoo/. The builder module
    must expose build_day(date_yyyy_mm_dd, ts_codes, data_root, result_root,
    write_influxdb, influx_*). Date is compact YYYYMMDD -> converted to YYYY-MM-DD.
    """
    from barra_cne5_data_loader import BarraCNE5DataLoader

    def _build(date_compact: str) -> dict:
        import importlib
        mod = importlib.import_module(f"factor_builders.{module_name}")
        date_yyyy_mm_dd = f"{date_compact[0:4]}-{date_compact[4:6]}-{date_compact[6:8]}"
        loader = BarraCNE5DataLoader(TUSHARE_DATA_PATH)
        ts_codes = loader.load_index_constituents(asof_date=date_compact, index_code="000300.SH")
        started = time.perf_counter()
        df = mod.build_day(
            date_yyyy_mm_dd=date_yyyy_mm_dd,
            ts_codes=ts_codes,
            data_root=TUSHARE_DATA_PATH,
            result_root=CROWDING_RESULT_ROOT,
            write_influxdb=bool(INFLUX_TOKEN),
            influx_url=INFLUX_URL, influx_org=INFLUX_ORG,
            influx_bucket=INFLUX_BUCKET, influx_token=INFLUX_TOKEN,
        )
        return {"rows": int(len(df)), "duration_ms": int((time.perf_counter() - started) * 1000)}

    def _resolve() -> str | None:
        root = Path(CROWDING_RESULT_ROOT) / "factor-zoo" / factor_id
        if not root.exists():
            return None
        best: str | None = None
        for sub in root.iterdir():
            if not sub.is_dir():
                continue
            try:
                datetime.strptime(sub.name, "%Y-%m-%d")
            except ValueError:
                continue
            compact = sub.name.replace("-", "")
            if best is None or compact > best:
                best = compact
        return best

    return _build, _resolve


def _make_alpha101_group():
    """One group builder: panel loaded once, all 101 alphas in one pass.

    Freshness is group-granular; alpha001 dir is the canonical reference.
    Per-alpha failures are recorded in the build summary, do NOT poison the group.
    """
    from alpha101.builder import build_day as _alpha101_build_day
    from alpha101.panel_loader import load_csi800_universe

    def _build(date_compact: str) -> dict:
        from barra_cne5_data_loader import BarraCNE5DataLoader
        loader = BarraCNE5DataLoader(TUSHARE_DATA_PATH)
        ts_codes = load_csi800_universe(loader, date_compact)
        date_yyyy_mm_dd = f"{date_compact[0:4]}-{date_compact[4:6]}-{date_compact[6:8]}"
        summary = _alpha101_build_day(
            date_yyyy_mm_dd=date_yyyy_mm_dd, ts_codes=ts_codes,
            data_root=TUSHARE_DATA_PATH, result_root=CROWDING_RESULT_ROOT,
            write_influxdb=bool(INFLUX_TOKEN),
            influx_url=INFLUX_URL, influx_org=INFLUX_ORG,
            influx_bucket=INFLUX_BUCKET, influx_token=INFLUX_TOKEN,
        )
        return {"rows": int(summary["rows"]),
                "duration_ms": int(summary["duration_ms"]),
                "failed_alphas": list(summary.get("failed", {}).keys())}

    def _resolve() -> str | None:
        root = Path(CROWDING_RESULT_ROOT) / "factor-zoo" / "alpha001"
        if not root.exists():
            return None
        best: str | None = None
        for sub in root.iterdir():
            if not sub.is_dir():
                continue
            try:
                datetime.strptime(sub.name, "%Y-%m-%d")
            except ValueError:
                continue
            compact = sub.name.replace("-", "")
            if best is None or compact > best:
                best = compact
        return best

    return _build, _resolve


_BLD_ALPHA101, _RES_ALPHA101 = _make_alpha101_group()


# Financial (annual PIT) factors — value changes only on new annual disclosure.
_BLD_ACCRUALS, _RES_ACCRUALS = _make_factorzoo_builder("accruals_sloan_builder", "accruals_sloan")
_BLD_GP, _RES_GP = _make_factorzoo_builder("gross_profitability_builder", "gross_profitability")
_BLD_AG, _RES_AG = _make_factorzoo_builder("asset_growth_builder", "asset_growth")
_BLD_ROECH, _RES_ROECH = _make_factorzoo_builder("roe_change_builder", "roe_change")
# Price-volume daily factors.
_BLD_IVOL, _RES_IVOL = _make_factorzoo_builder("ivol_20d_builder", "ivol_20d")
_BLD_MAXRET, _RES_MAXRET = _make_factorzoo_builder("max_ret_20d_builder", "max_ret_20d")
_BLD_REVERSAL, _RES_REVERSAL = _make_factorzoo_builder("short_term_reversal_builder", "short_term_reversal")


def _resolve_forward_latest() -> str | None:
    """No parquet on disk; resolve from factor_worker's own state file (crash-resume)."""
    state = _load_state()
    entry = state.get("factors", {}).get("forward")
    return entry.get("last_built_date") if entry else None


def _build_barra_v2(date_compact: str) -> dict:
    """Wire to BarraCNE5V2FactorBuilder.build_factor_snapshot + write_factor_history.

    Writes to Data/alternative/barra-cne5v2-factors (NOT -full) to match C# consumers.
    """
    from barra_cne5_data_loader import BarraCNE5DataLoader
    from barra_cne5v2_factor_builder import BarraCNE5V2FactorBuilder
    loader = BarraCNE5DataLoader(TUSHARE_DATA_PATH)
    universe = loader.load_index_constituents(asof_date=date_compact, index_code="000300.SH")
    builder = BarraCNE5V2FactorBuilder(TUSHARE_DATA_PATH, loader=loader)
    started = time.perf_counter()
    snapshot = builder.build_factor_snapshot(
        universe=universe, trade_date=date_compact, market_symbol="000300.SH",
    )
    written = builder.write_factor_history(snapshot, BARRA_OUTPUT_ROOT)
    return {
        "rows": int(len(snapshot)),
        "symbols_written": len(written),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


def _resolve_barra_latest() -> str | None:
    """Last trade_date row of a canonical CSV (600519 sse); fallback sample across both markets."""
    canonical = BARRA_OUTPUT_ROOT / "sse" / "daily" / "600519.csv"
    best: str | None = None
    if canonical.exists():
        try:
            import pandas as pd
            df = pd.read_csv(canonical, dtype={"trade_date": str}, usecols=["trade_date"])
            if not df.empty:
                best = str(df["trade_date"].astype(str).str.zfill(8).max())
        except Exception:
            pass
    if best:
        return best
    for market in ("sse", "szse"):
        mkt_dir = BARRA_OUTPUT_ROOT / market / "daily"
        if not mkt_dir.exists():
            continue
        for csv in list(mkt_dir.glob("*.csv"))[:20]:
            try:
                import pandas as pd
                df = pd.read_csv(csv, dtype={"trade_date": str}, usecols=["trade_date"])
                if df.empty:
                    continue
                m = str(df["trade_date"].astype(str).str.zfill(8).max())
                if best is None or m > best:
                    best = m
            except Exception:
                continue
    return best


BUILDERS: list[FactorBuilder] = [
    FactorBuilder(
        factor_id="crowding",
        build_callable=_build_crowding,
        latest_date_resolver=_resolve_crowding_latest,
        depends_on=("daily_basic", "moneyflow", "margin_detail", "hsgt_top10", "cyq_perf", "adj_factor"),
    ),
    FactorBuilder(
        factor_id="forward",
        build_callable=_build_forward,
        latest_date_resolver=_resolve_forward_latest,
        depends_on=("bak_daily", "moneyflow", "moneyflow_hsgt", "stk_auction_o",
                    "express", "forecast", "stk_holdertrade", "pledge_stat",
                    "disclosure_date", "cn_m", "limit_list_d", "cb_share"),
    ),
    FactorBuilder(
        factor_id="barra_v2",
        build_callable=_build_barra_v2,
        latest_date_resolver=_resolve_barra_latest,
        depends_on=("daily", "daily_basic", "adj_factor", "income", "balancesheet",
                    "cashflow", "fina_indicator", "index_weight", "moneyflow",
                    "hsgt_top10", "margin_detail", "cyq_perf"),
        max_backfill_days=5,
    ),
    # ── Phase 5: 7 new factors (parquet result/factor-zoo/<id>/) ──
    FactorBuilder(
        factor_id="accruals_sloan",
        build_callable=_BLD_ACCRUALS, latest_date_resolver=_RES_ACCRUALS,
        depends_on=("balancesheet", "cashflow"), max_backfill_days=1,
    ),
    FactorBuilder(
        factor_id="gross_profitability",
        build_callable=_BLD_GP, latest_date_resolver=_RES_GP,
        depends_on=("income", "balancesheet"), max_backfill_days=1,
    ),
    FactorBuilder(
        factor_id="asset_growth",
        build_callable=_BLD_AG, latest_date_resolver=_RES_AG,
        depends_on=("balancesheet", "stock_basic"), max_backfill_days=1,
    ),
    FactorBuilder(
        factor_id="roe_change",
        build_callable=_BLD_ROECH, latest_date_resolver=_RES_ROECH,
        depends_on=("fina_indicator",), max_backfill_days=1,
    ),
    FactorBuilder(
        factor_id="ivol_20d",
        build_callable=_BLD_IVOL, latest_date_resolver=_RES_IVOL,
        depends_on=("daily", "adj_factor", "index_daily"), max_backfill_days=60,
    ),
    FactorBuilder(
        factor_id="max_ret_20d",
        build_callable=_BLD_MAXRET, latest_date_resolver=_RES_MAXRET,
        depends_on=("daily",), max_backfill_days=60,
    ),
    FactorBuilder(
        factor_id="short_term_reversal",
        build_callable=_BLD_REVERSAL, latest_date_resolver=_RES_REVERSAL,
        depends_on=("daily", "adj_factor"), max_backfill_days=60,
    ),
    # ── Alpha101 group (101 WorldQuant alphas, panel-loaded-once) ──
    FactorBuilder(
        factor_id="alpha101",
        build_callable=_BLD_ALPHA101,
        latest_date_resolver=_RES_ALPHA101,
        depends_on=("daily", "adj_factor", "daily_basic", "index_member_all"),
        max_backfill_days=60,
    ),
]


def _queue_pending_dates(t_f: str | None, t_cal: str, max_days: int) -> list[str]:
    """Return compact YYYYMMDD open dates in (T_f, T_cal], capped at max_days.

    If T_f is None (never built), queue just the latest open day <= t_cal
    (don't bootstrap history).
    Uses trade_cal to enumerate ONLY open days.
    """
    import pandas as pd
    cal_path = Path(TUSHARE_DATA_PATH) / "trade_cal" / "data.parquet"
    if not cal_path.exists():
        return [t_cal] if t_f is None else []
    df = pd.read_parquet(cal_path, columns=["cal_date", "is_open"])
    lower = t_f or ""
    open_days = df.loc[
        (df["is_open"].astype(str).isin(_OPEN_TRUE))
        & (df["cal_date"].astype(str) > lower)
        & (df["cal_date"].astype(str) <= t_cal),
        "cal_date",
    ].astype(str).tolist()
    open_days.sort()
    if len(open_days) > max_days:
        LOGGER.warning("Backlog %d days exceeds cap %d; truncating to latest %d",
                       len(open_days), max_days, max_days)
        open_days = open_days[-max_days:]
    if t_f is None and open_days:
        # never built -> only build the latest day, not history
        open_days = [open_days[-1]]
    return open_days


def run_once(dry_run: bool = False) -> dict:
    """One poll cycle. Idempotent + isolated per factor. Returns a report dict."""
    t_cal = _resolve_latest_trade_date(TUSHARE_DATA_PATH)
    if t_cal is None:
        LOGGER.warning("trade_cal unavailable; skipping cycle")
        return {"status": "no_trade_cal", "t_cal": None, "built": {}}
    t_raw = _read_tushare_finished_date()
    if t_raw is None or t_raw < t_cal:
        LOGGER.info("T_raw=%s < T_cal=%s; tushare not finished, skipping factor cycle", t_raw, t_cal)
        return {"status": "waiting_tushare", "t_cal": t_cal, "t_raw": t_raw, "built": {}}

    state = _load_state()
    factors_state = state.setdefault("factors", {})
    report = {"status": "ran", "t_cal": t_cal, "t_raw": t_raw, "built": {}}

    for builder in BUILDERS:
        fid = builder.factor_id
        entry = factors_state.get(fid, {})
        fail_count = entry.get("fail_count", 0)
        last_status = entry.get("last_status")
        if last_status == "poisoned":
            LOGGER.info("Factor %s poisoned (fail_count=%d); skipping", fid, fail_count)
            report["built"][fid] = {"status": "poisoned_skipped"}
            continue

        try:
            t_f = builder.latest_date_resolver()
        except Exception as exc:
            LOGGER.exception("Factor %s latest_date_resolver crashed; treating as None", fid)
            t_f = None
            entry["last_error"] = f"resolver_error: {exc}"

        pending = _queue_pending_dates(t_f, t_cal, builder.max_backfill_days)
        if not pending:
            LOGGER.info("Factor %s fresh (T_f=%s >= T_cal=%s); skip", fid, t_f, t_cal)
            report["built"][fid] = {"status": "fresh", "t_f": t_f, "built_dates": []}
            entry["last_status"] = "fresh"
            entry["last_run_at"] = datetime.now(CHINA_TZ).isoformat()
            factors_state[fid] = entry
            continue

        if dry_run:
            LOGGER.info("[dry-run] factor %s would build dates %s", fid, pending)
            report["built"][fid] = {"status": "dry_run", "t_f": t_f, "would_build": pending}
            continue

        built_dates: list[str] = []
        last_err: str | None = None
        for t in pending:
            try:
                summary = builder.build_callable(t)
                built_dates.append(t)
                last_err = None
                entry["last_built_date"] = t
                entry["last_status"] = "ok"
                entry["last_error"] = None
                entry["fail_count"] = 0
                entry["last_duration_ms"] = int(summary.get("duration_ms", 0))
                entry["last_run_at"] = datetime.now(CHINA_TZ).isoformat()
                factors_state[fid] = entry
                _save_state(state)  # crash-resume checkpoint per date
            except Exception as exc:
                LOGGER.exception("Factor %s build_day failed for date %s", fid, t)
                fail_count += 1
                last_err = f"{type(exc).__name__}: {exc}"
                entry["last_error"] = last_err
                entry["fail_count"] = fail_count
                entry["last_status"] = "failed"
                entry["last_run_at"] = datetime.now(CHINA_TZ).isoformat()
                if fail_count >= POISON_THRESHOLD:
                    entry["last_status"] = "poisoned"
                    LOGGER.error("Factor %s poisoned after %d failures", fid, fail_count)
                factors_state[fid] = entry
                _save_state(state)
                break

        report["built"][fid] = {
            "status": entry["last_status"],
            "t_f": t_f,
            "built_dates": built_dates,
            "last_error": last_err,
            "fail_count": fail_count,
        }

    _save_state(state)
    _write_freshness(factors_state, t_cal)
    return report


def _write_freshness(factors_state: dict, t_cal: str) -> None:
    """Write freshness.json {factor_id: {last_date, status, last_run, lag_days}}.

    Best-effort InfluxDB lean_factor_freshness write; skip silently if no token.
    """
    FRESHNESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, dict] = {}
    lines: list[str] = []
    for fid, entry in factors_state.items():
        last_date = entry.get("last_built_date")
        status = entry.get("last_status", "unknown")
        last_run = entry.get("last_run_at")
        lag_days = None
        if last_date and t_cal:
            try:
                d1 = datetime.strptime(last_date, "%Y%m%d").date()
                d2 = datetime.strptime(t_cal, "%Y%m%d").date()
                lag_days = (d2 - d1).days
            except ValueError:
                pass
        report[fid] = {
            "last_date": last_date,
            "status": status,
            "last_run": last_run,
            "lag_days": lag_days,
        }
        if INFLUX_TOKEN and last_date:
            try:
                ts_ns = _ts_ns(last_date)
                lines.append(
                    f'{FRESHNESS_MEASUREMENT},factor_id={fid} '
                    f'last_date="{last_date}",lag_days={lag_days or 0}i,'
                    f'status="{status}" {ts_ns}'
                )
            except Exception:
                pass
    FRESHNESS_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if lines and INFLUX_TOKEN:
        try:
            _write_influx(lines, INFLUX_URL, INFLUX_ORG, INFLUX_BUCKET, INFLUX_TOKEN)
        except Exception as exc:
            LOGGER.warning("InfluxDB freshness write failed (non-blocking): %s", exc)
    elif lines and not INFLUX_TOKEN:
        LOGGER.debug("InfluxDB freshness skipped (no INFLUXDB_TOKEN); freshness.json written")


def _ts_ns(trade_date_compact: str) -> int:
    """YYYYMMDD -> UTC nanoseconds at 15:00 Shanghai. Mirrors crowding/forward builders."""
    local = datetime(
        int(trade_date_compact[0:4]), int(trade_date_compact[4:6]),
        int(trade_date_compact[6:8]), 15, 0, 0, tzinfo=CHINA_TZ,
    )
    return int(local.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def _write_influx(lines: list[str], url: str, org: str, bucket: str, token: str) -> int:
    """Mirror crowding_factor_builder.write_influx (line-protocol POST)."""
    payload = [l for l in lines if l]
    if not payload or not token:
        return 0
    q = parse.urlencode({"org": org, "bucket": bucket, "precision": "ns"})
    req = request.Request(
        f"{url.rstrip('/')}/api/v2/write?{q}",
        data=("\n".join(payload) + "\n").encode(),
        method="POST",
        headers={"Authorization": f"Token {token}", "Content-Type": "text/plain; charset=utf-8"},
    )
    with request.urlopen(req, timeout=60) as resp:
        return len(payload) if 200 <= resp.status < 300 else 0


def _is_trading_day(today_compact: str) -> bool:
    """True if trade_cal has is_open=1 for today_compact."""
    import pandas as pd
    cal_path = Path(TUSHARE_DATA_PATH) / "trade_cal" / "data.parquet"
    if not cal_path.exists():
        return True
    try:
        df = pd.read_parquet(cal_path, columns=["cal_date", "is_open"])
    except Exception:
        return True
    row = df[df["cal_date"].astype(str) == today_compact]
    if row.empty:
        return True
    return bool(row["is_open"].astype(str).isin(_OPEN_TRUE).iloc[0])


def run_forever(poll_seconds: int = 60) -> None:
    """Main daemon loop. On non-trading days extend sleep to 600s (spec §3.1 轮询降频)."""
    LOGGER.info("factor_worker started: poll=%ss poison_threshold=%d", poll_seconds, POISON_THRESHOLD)
    while True:
        try:
            run_once(dry_run=False)
        except Exception:
            LOGGER.exception("run_once crashed; supervisor will keep us alive")
        today_compact = datetime.now(CHINA_TZ).strftime("%Y%m%d")
        sleep = poll_seconds if _is_trading_day(today_compact) else max(poll_seconds, 600)
        time.sleep(sleep)


def _cli() -> int:
    p = argparse.ArgumentParser(description="factor_worker — Phase 3 freshness daemon (spec §3.1)")
    p.add_argument("--poll-seconds", type=int, default=60,
                   help="poll interval (default 60s; extended to 600s on non-trading days)")
    p.add_argument("--once", action="store_true", help="run a single cycle and exit")
    p.add_argument("--dry-run", action="store_true",
                   help="don't call builders; print T_cal/T_raw/pending queue")
    args = p.parse_args()
    if args.once:
        report = run_once(dry_run=args.dry_run)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    run_forever(poll_seconds=args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
