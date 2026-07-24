#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import os
import json
import math
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import defaultdict
from pathlib import Path
from concurrent.futures import FIRST_COMPLETED, wait

import pandas as pd
import numpy as np

CURRENT_DIR = Path(__file__).resolve().parent
DATA_SOURCE_DIR = CURRENT_DIR.parent / "data-source" / "tushare"
for candidate in [CURRENT_DIR, DATA_SOURCE_DIR]:
    path_text = str(candidate)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from barra_cne5_data_loader import BarraCNE5DataLoader
from barra_cne5_factor_builder import BarraCNE5FactorBuilder, FACTOR_COLUMNS


PATH_KEYS = {"tushare-data-path", "output-path", "report-file", "external-factor-path"}
OUTPUT_COLUMNS = [
    "trade_date",
    *FACTOR_COLUMNS,
    "total_mv",
    "turnover_rate",
    "listed_days",
    "missing_factor_count",
    "is_st",
]
CANONICAL_COLUMN_ALIASES = {
    "ts_code": {
        "ts_code", "symbol", "ticker", "code", "stock_code", "sec_code", "wind_code",
        "order_book_id", "instrument", "asset",
    },
    "trade_date": {
        "trade_date", "date", "datetime", "dt", "trading_date", "asof_date", "calc_date",
        "snapshot_date",
    },
    "beta": {"beta"},
    "momentum": {"momentum", "rstr", "mom"},
    "size": {"size", "lncap", "market_size"},
    "earnyld": {"earnyld", "earnings_yield", "earningsyield", "earning_yield", "earn_yld", "value_yield"},
    "resvol": {"resvol", "residual_volatility", "residualvolatility", "residual_vol", "residual_volatility_factor"},
    "growth": {"growth", "egro_sgro", "growth_factor"},
    "btop": {"btop", "book_to_price", "booktoprice", "book_price", "book_to_market", "bp"},
    "leverage": {"leverage", "lev"},
    "liquidity": {"liquidity", "liq"},
    "nlsize": {"nlsize", "non_linear_size", "nonlinear_size", "non_linear_market_size"},
    "total_mv": {"total_mv", "market_cap", "mkt_cap", "total_market_value", "market_value", "totalmv"},
    "turnover_rate": {"turnover_rate", "turnover", "turnover_ratio", "turnoverratio", "turnover_rate_f"},
    "listed_days": {"listed_days", "days_listed", "days_since_listed", "listing_days", "age_days"},
    "missing_factor_count": {"missing_factor_count", "factor_missing_count", "missing_count"},
    "is_st": {"is_st", "st", "st_flag", "st_status"},
}
SUPPORTED_FACTOR_EXTENSIONS = {".csv", ".parquet", ".pq"}
_PARALLEL_FACTOR_BUILDER: BarraCNE5FactorBuilder | None = None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config() -> dict:
    root = repo_root()
    return {
        "tushare-data-path": "/home/project/tushare-downloader/tushare_data_v2",
        "output-path": str(root / "Data" / "alternative" / "barra-cne5-factors"),
        "report-file": str(root / "Results" / "barra-cne5-factor-bridge-report.json"),
        "factor-source-mode": "auto",
        "random-factor-seed": 42,
        "external-factor-path": None,
        "universe": "csi300",
        "index-code": "000300.SH",
        "market-symbol": "000300.SH",
        "start-date": "20200101",
        "end-date": "20251231",
        "factor-worker-count": "auto",
        "parallel-date-block-size": 5,
        "progress-interval-symbols": 500,
        "progress-interval-files": 100,
    }


def resolve_config_paths(config: dict, base_dir: Path) -> dict:
    resolved = dict(config)
    for key in PATH_KEYS:
        value = resolved.get(key)
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        resolved[key] = str(path)
    return resolved


def load_pipeline_config(config_path: str | Path | None = None, overrides: dict | None = None) -> dict:
    config = default_config()

    if config_path:
        path = Path(config_path).resolve()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        config.update(resolve_config_paths(loaded, path.parent))

    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            config[key] = value

    return resolve_config_paths(config, repo_root())


def canonicalize_column_name(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower())
    return normalized.strip("_")


def normalize_trade_date(value) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) >= 8:
        return digits[:8]
    try:
        return pd.Timestamp(value).strftime("%Y%m%d")
    except Exception:
        return None


def format_seconds(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes > 0:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def estimate_eta(elapsed_seconds: float, completed: int, total: int) -> str:
    if completed <= 0 or total <= completed:
        return "0s"
    remaining = total - completed
    estimated = elapsed_seconds * remaining / max(completed, 1)
    return format_seconds(estimated)


def progress_interval(config: dict, key: str, default: int) -> int:
    value = config.get(key, default)
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        resolved = default
    return max(1, resolved)


def resolve_random_factor_seed(config: dict) -> int:
    value = config.get("random-factor-seed", 42)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 42


def print_progress_line(prefix: str, payload: dict, extra_fields: list[tuple[str, object]] | None = None) -> None:
    completed = int(payload.get("completed", 0))
    total = max(1, int(payload.get("total", 0)))
    percent = completed * 100.0 / total
    line = f"[{prefix}] {completed}/{total} ({percent:5.1f}%)"
    for key, value in extra_fields or []:
        if value is None:
            continue
        line += f" {key}={value}"
    line += f" elapsed={format_seconds(float(payload.get('elapsed_seconds', 0.0)))}"
    line += f" eta={estimate_eta(float(payload.get('elapsed_seconds', 0.0)), completed, total)}"
    print(line, flush=True)


def stable_random_seed(*parts: object) -> int:
    material = "::".join(str(part) for part in parts).encode("utf-8")
    digest = hashlib.blake2b(material, digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=False)


def resolve_factor_worker_count(config: dict, trading_dates: list[str], universe_size: int) -> int:
    raw_value = config.get("factor-worker-count", "auto")
    if isinstance(raw_value, str) and raw_value.strip().lower() == "auto":
        if universe_size < 200:
            return 1
        if len(trading_dates) > 1 and len(trading_dates) < 5:
            return 1
        cpu_count = os.cpu_count() or 1
        return max(1, min(4, cpu_count))

    try:
        resolved = int(raw_value)
    except (TypeError, ValueError):
        resolved = 1
    return max(1, resolved)


def resolve_parallel_date_block_size(config: dict, trading_dates: list[str]) -> int:
    default_block_size = 5
    raw_value = config.get("parallel-date-block-size", default_block_size)
    try:
        resolved = int(raw_value)
    except (TypeError, ValueError):
        resolved = default_block_size
    resolved = max(1, resolved)
    return min(resolved, max(1, len(trading_dates)))


def chunk_symbols(symbols: list[str], chunk_count: int) -> list[list[str]]:
    if not symbols:
        return []
    chunk_count = max(1, min(chunk_count, len(symbols)))
    chunk_size = max(1, math.ceil(len(symbols) / chunk_count))
    return [symbols[index:index + chunk_size] for index in range(0, len(symbols), chunk_size)]


def chunk_trading_dates(trading_dates: list[str], block_size: int) -> list[list[str]]:
    if not trading_dates:
        return []
    block_size = max(1, block_size)
    return [trading_dates[index:index + block_size] for index in range(0, len(trading_dates), block_size)]


def _initialize_parallel_builder(tushare_data_path: str) -> None:
    global _PARALLEL_FACTOR_BUILDER
    _PARALLEL_FACTOR_BUILDER = BarraCNE5FactorBuilder(tushare_data_path)


def _build_symbol_rows_chunk(payload: tuple[int, list[str], str, str, str]) -> dict:
    chunk_id, symbols, trade_date, market_symbol, tushare_data_path = payload
    global _PARALLEL_FACTOR_BUILDER
    if _PARALLEL_FACTOR_BUILDER is None:
        _PARALLEL_FACTOR_BUILDER = BarraCNE5FactorBuilder(tushare_data_path)

    rows: list[dict] = []
    for ts_code in symbols:
        row = _PARALLEL_FACTOR_BUILDER._build_symbol_row(
            ts_code=ts_code,
            trade_date=trade_date,
            market_symbol=market_symbol,
        )
        if row is not None:
            rows.append(row)

    return {
        "chunk_id": chunk_id,
        "requested": len(symbols),
        "accepted": len(rows),
        "rows": rows,
    }


def _build_date_block(payload: tuple[int, list[str], list[str], str, str]) -> dict:
    block_id, trade_dates, universe, market_symbol, tushare_data_path = payload
    global _PARALLEL_FACTOR_BUILDER
    if _PARALLEL_FACTOR_BUILDER is None:
        _PARALLEL_FACTOR_BUILDER = BarraCNE5FactorBuilder(tushare_data_path)

    rows: list[dict] = []
    snapshots: list[dict] = []
    for trade_date in trade_dates:
        snapshot = _PARALLEL_FACTOR_BUILDER.build_factor_snapshot(
            universe=universe,
            trade_date=trade_date,
            market_symbol=market_symbol,
        )
        coverage = 0.0
        if not snapshot.empty:
            coverage = 1.0 - float(snapshot["missing_factor_count"].mean() / max(len(snapshot.columns), 1))
            rows.extend(snapshot.to_dict("records"))
        snapshots.append({
            "trade_date": trade_date,
            "rows": int(len(snapshot)),
            "coverage": round(max(0.0, min(1.0, coverage)), 4),
        })

    return {
        "block_id": block_id,
        "trade_dates": trade_dates,
        "date_count": len(trade_dates),
        "row_count": len(rows),
        "rows": rows,
        "snapshots": snapshots,
    }


def infer_market_suffix(ticker: str) -> str | None:
    if not ticker:
        return None
    if ticker.startswith(("5", "6", "9")):
        return "SH"
    if ticker.startswith(("0", "1", "2", "3")):
        return "SZ"
    return None


def normalize_ts_code(value) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip().upper()
    if not text:
        return None

    text = text.replace("_", ".")
    direct_patterns = [
        (r"^(?P<ticker>\d{6})\.(?P<market>SH|SZ|SSE|SZSE|XSHG|XSHE|SHSE)$", False),
        (r"^(?P<market>SH|SZ|SSE|SZSE|XSHG|XSHE|SHSE)\.?(?P<ticker>\d{6})$", True),
    ]
    for pattern, market_first in direct_patterns:
        match = re.match(pattern, text)
        if not match:
            continue
        ticker = match.group("ticker")
        market = match.group("market")
        if market in {"SH", "SSE", "XSHG", "SHSE"}:
            return f"{ticker}.SH"
        if market in {"SZ", "SZSE", "XSHE"}:
            return f"{ticker}.SZ"

    match = re.search(r"(?P<ticker>\d{6})", text)
    if match:
        ticker = match.group("ticker")
        suffix = infer_market_suffix(ticker)
        if suffix:
            return f"{ticker}.{suffix}"

    return None


def infer_ts_code_from_path(path: str | Path) -> str | None:
    candidate = Path(path)
    probes = [candidate.stem, candidate.name, candidate.parent.name]
    if candidate.stem.lower() in {"data", "factors", "factor"}:
        probes.append(candidate.parent.parent.name)
    for probe in probes:
        normalized = normalize_ts_code(probe)
        if normalized:
            return normalized
    return None


def coalesce_columns(frame: pd.DataFrame, column_names: list[str]) -> pd.Series:
    series = frame[column_names[0]]
    for candidate in column_names[1:]:
        series = series.combine_first(frame[candidate])
    return series


def build_column_mapping(frame: pd.DataFrame) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = defaultdict(list)
    for column in frame.columns:
        canonical = canonicalize_column_name(column)
        for target, aliases in CANONICAL_COLUMN_ALIASES.items():
            if canonical == target or canonical in aliases:
                mapping[target].append(column)
                break
    return mapping


def parse_boolish(value) -> int:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0
    if isinstance(value, bool):
        return int(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y"}:
        return 1
    if text in {"0", "false", "f", "no", "n", ""}:
        return 0
    try:
        return int(float(text) != 0.0)
    except Exception:
        return 0


def collect_external_factor_files(input_path: str | Path) -> list[Path]:
    root = Path(input_path).resolve()
    if root.is_file():
        return [root] if root.suffix.lower() in SUPPORTED_FACTOR_EXTENSIONS else []
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_FACTOR_EXTENSIONS:
            files.append(path)
    return files


def read_factor_input_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    raise ValueError(f"Unsupported factor input extension: {path}")


def normalize_external_factor_frame(frame: pd.DataFrame, source_path: str | Path) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["ts_code", *OUTPUT_COLUMNS])

    column_mapping = build_column_mapping(frame)
    if "trade_date" not in column_mapping:
        return pd.DataFrame(columns=["ts_code", *OUTPUT_COLUMNS])

    normalized = pd.DataFrame(index=frame.index)
    for target, source_columns in column_mapping.items():
        normalized[target] = coalesce_columns(frame, source_columns)

    if "ts_code" not in normalized.columns:
        inferred = infer_ts_code_from_path(source_path)
        if not inferred:
            return pd.DataFrame(columns=["ts_code", *OUTPUT_COLUMNS])
        normalized["ts_code"] = inferred

    normalized["ts_code"] = normalized["ts_code"].map(normalize_ts_code)
    normalized["trade_date"] = normalized["trade_date"].map(normalize_trade_date)
    normalized = normalized[normalized["ts_code"].notna() & normalized["trade_date"].notna()].copy()
    if normalized.empty:
        return pd.DataFrame(columns=["ts_code", *OUTPUT_COLUMNS])

    for column in FACTOR_COLUMNS + ["total_mv", "turnover_rate", "listed_days", "missing_factor_count"]:
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        else:
            normalized[column] = pd.NA

    if "is_st" in normalized.columns:
        normalized["is_st"] = normalized["is_st"].map(parse_boolish)
    else:
        normalized["is_st"] = 0

    computed_missing_counts = normalized[FACTOR_COLUMNS].isna().sum(axis=1).astype(int)
    normalized["missing_factor_count"] = pd.to_numeric(normalized["missing_factor_count"], errors="coerce")
    normalized["missing_factor_count"] = normalized["missing_factor_count"].where(
        normalized["missing_factor_count"].notna(),
        computed_missing_counts,
    )
    normalized["missing_factor_count"] = normalized["missing_factor_count"].astype(int)
    normalized["is_st"] = normalized["is_st"].fillna(0).astype(int)

    result = normalized[["ts_code", *OUTPUT_COLUMNS]].copy()
    result = result.sort_values(["ts_code", "trade_date"]).drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    return result.reset_index(drop=True)


def run_external_factor_import(config: dict) -> dict:
    external_path = config.get("external-factor-path")
    if not external_path:
        raise ValueError("external-factor-path is required for external Barra factor import.")

    files = collect_external_factor_files(external_path)
    if not files:
        raise ValueError(f"No external factor files were found under {external_path}")

    normalized_frames: list[pd.DataFrame] = []
    accepted_files: list[str] = []
    ignored_files: list[str] = []
    file_report_every = progress_interval(config, "progress-interval-files", 100)
    symbol_report_every = progress_interval(config, "progress-interval-symbols", 500)
    started_at = time.perf_counter()

    print("=" * 80)
    print(f"Importing external Barra CNE5 factors from {Path(external_path).resolve()}", flush=True)
    print(f"Discovered {len(files)} candidate factor files", flush=True)
    print("=" * 80)

    for index, file_path in enumerate(files, start=1):
        frame = normalize_external_factor_frame(read_factor_input_file(file_path), file_path)
        if frame.empty:
            ignored_files.append(str(file_path))
            row_count = 0
        else:
            normalized_frames.append(frame)
            accepted_files.append(str(file_path))
            row_count = int(len(frame))

        if index == 1 or index % file_report_every == 0 or index == len(files):
            print_progress_line(
                "import files",
                {
                    "completed": index,
                    "total": len(files),
                    "elapsed_seconds": time.perf_counter() - started_at,
                },
                extra_fields=[
                    ("accepted", len(accepted_files)),
                    ("ignored", len(ignored_files)),
                    ("rows", row_count),
                    ("current", file_path.name),
                ],
            )

    if not normalized_frames:
        raise ValueError(
            "External factor import could not normalize any files. "
            "Expected a symbol/date column plus Barra factor columns or recognizable aliases."
        )

    combined = pd.concat(normalized_frames, ignore_index=True)
    combined = combined.sort_values(["ts_code", "trade_date"]).drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    print(
        f"[import combine] normalized_rows={len(combined)} unique_symbols={combined['ts_code'].nunique()} "
        f"date_range={combined['trade_date'].min()}->{combined['trade_date'].max()}",
        flush=True,
    )
    builder = BarraCNE5FactorBuilder(config["tushare-data-path"])
    written = builder.write_factor_history(
        combined,
        config["output-path"],
        progress_callback=lambda payload: print_progress_line(
            "import write",
            payload,
            extra_fields=[
                ("symbol", payload.get("ts_code")),
                ("rows", payload.get("rows")),
                ("file", Path(payload.get("file_path", "")).name if payload.get("file_path") else None),
            ],
        ),
        progress_interval=symbol_report_every,
    )

    report = {
        "status": "ok",
        "mode": "import",
        "external_factor_path": str(Path(external_path).resolve()),
        "output_path": config["output-path"],
        "accepted_file_count": len(accepted_files),
        "ignored_file_count": len(ignored_files),
        "accepted_files": accepted_files,
        "ignored_files": ignored_files,
        "written_symbol_count": len(written),
        "written_symbols": sorted(written.keys()),
        "row_count": int(len(combined)),
        "date_range": {
            "start": str(combined["trade_date"].min()),
            "end": str(combined["trade_date"].max()),
        },
    }

    report_file = config.get("report-file")
    if report_file:
        report_path = Path(report_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 80)
    print(f"Imported {len(accepted_files)} external factor files", flush=True)
    print(f"Written {len(written)} symbol files -> {config['output-path']}", flush=True)
    print("=" * 80)
    return report


def build_universe(loader: BarraCNE5DataLoader, config: dict, asof_date: str) -> list[str]:
    explicit = config.get("symbols")
    if explicit:
        if isinstance(explicit, str):
            return [symbol.strip() for symbol in explicit.split(",") if symbol.strip()]
        return [str(symbol) for symbol in explicit]

    return loader.load_stock_universe(
        asof_date=asof_date,
        universe=config.get("universe", "all-a"),
        index_code=config.get("index-code", "000300.SH"),
    )


def build_random_symbol_profile(base_seed: int, ts_code: str) -> dict[str, object]:
    rng = np.random.default_rng(stable_random_seed(base_seed, ts_code, "profile"))
    return {
        "factor_base": rng.normal(loc=0.0, scale=1.0, size=len(FACTOR_COLUMNS)),
        "total_mv_base": float(np.exp(rng.normal(loc=22.0, scale=0.8))),
        "turnover_rate_base": float(rng.uniform(0.35, 6.0)),
        "listed_days_base": int(rng.integers(600, 6000)),
    }


def build_random_factor_row(
    base_seed: int,
    ts_code: str,
    trade_date: str,
    trade_date_index: int,
    profile: dict[str, object],
) -> dict[str, object]:
    rng = np.random.default_rng(stable_random_seed(base_seed, ts_code, trade_date))
    total_mv = float(max(1.0e8, profile["total_mv_base"] * math.exp(float(rng.normal(loc=0.0, scale=0.05)))))
    turnover_rate = float(max(0.05, profile["turnover_rate_base"] + float(rng.normal(loc=0.0, scale=0.15))))

    factor_values = np.asarray(profile["factor_base"], dtype=float) * 0.65 + rng.normal(
        loc=0.0,
        scale=0.35,
        size=len(FACTOR_COLUMNS),
    )
    factor_map = {
        column: float(value)
        for column, value in zip(FACTOR_COLUMNS, factor_values)
    }
    factor_map["size"] = float(0.65 * factor_map["size"] + 0.35 * (math.log(total_mv) - 22.0))
    factor_map["liquidity"] = float(0.65 * factor_map["liquidity"] + 0.35 * math.log(turnover_rate + 1.0))
    factor_map["nlsize"] = float(0.50 * factor_map["nlsize"] + 0.50 * ((factor_map["size"] ** 2) - 1.0))

    return {
        "ts_code": ts_code,
        "trade_date": trade_date,
        **factor_map,
        "total_mv": total_mv,
        "turnover_rate": turnover_rate,
        "listed_days": int(profile["listed_days_base"]) + int(trade_date_index),
        "missing_factor_count": 0,
        "is_st": 0,
    }


def run_random_factor_generation(config: dict) -> dict:
    loader = BarraCNE5DataLoader(config["tushare-data-path"])
    builder = BarraCNE5FactorBuilder(config["tushare-data-path"], loader=loader)

    if config.get("date"):
        trading_dates = [str(config["date"])]
    else:
        trading_dates = loader.get_trading_dates(
            start_date=config["start-date"],
            end_date=config["end-date"],
            reference_symbol=config.get("market-symbol", "000300.SH"),
        )

    if not trading_dates:
        raise ValueError("No trading dates were discovered for the requested range.")

    universe = build_universe(loader, config, trading_dates[-1])
    if not universe:
        raise ValueError("No stock universe could be resolved for random Barra CNE5 factor generation.")

    seed = resolve_random_factor_seed(config)
    symbol_report_every = progress_interval(config, "progress-interval-symbols", 500)
    file_report_every = progress_interval(config, "progress-interval-files", 100)
    started_at = time.perf_counter()
    records: list[dict[str, object]] = []

    print("=" * 80)
    print(f"Generating random Barra CNE5 factors for {len(universe)} symbols", flush=True)
    print(f"Trade dates: {trading_dates[0]} -> {trading_dates[-1]} ({len(trading_dates)} sessions)", flush=True)
    print(f"Random seed: {seed}", flush=True)
    print("=" * 80)

    for index, ts_code in enumerate(universe, start=1):
        profile = build_random_symbol_profile(seed, ts_code)
        for trade_date_index, trade_date in enumerate(trading_dates):
            records.append(build_random_factor_row(seed, ts_code, trade_date, trade_date_index, profile))

        if index == 1 or index % symbol_report_every == 0 or index == len(universe):
            print_progress_line(
                "random generate",
                {
                    "completed": index,
                    "total": len(universe),
                    "elapsed_seconds": time.perf_counter() - started_at,
                },
                extra_fields=[
                    ("rows", len(records)),
                    ("current", ts_code),
                ],
            )

    combined = pd.DataFrame(records, columns=["ts_code", *OUTPUT_COLUMNS])
    unique_symbols = int(combined["ts_code"].nunique()) if not combined.empty else 0
    snapshot_summaries = [
        {
            "trade_date": trade_date,
            "rows": len(universe),
            "coverage": 1.0,
        }
        for trade_date in trading_dates
    ]

    print(
        f"[write prepare] total_rows={len(combined)} symbols={unique_symbols} "
        f"output={config['output-path']}",
        flush=True,
    )
    written = builder.write_factor_history(
        combined,
        config["output-path"],
        progress_callback=lambda payload: print_progress_line(
            "write files",
            payload,
            extra_fields=[
                ("symbol", payload.get("ts_code")),
                ("rows", payload.get("rows")),
                ("file", Path(payload.get("file_path", "")).name if payload.get("file_path") else None),
            ],
        ),
        progress_interval=file_report_every,
    )

    report = {
        "status": "ok",
        "mode": "random",
        "random_factor_seed": seed,
        "tushare_data_path": config["tushare-data-path"],
        "output_path": config["output-path"],
        "factor_worker_count": 1,
        "parallel_mode": "synthetic-single-process",
        "parallel_date_block_size": 1,
        "universe": config.get("universe", "all-a"),
        "resolved_symbol_count": len(universe),
        "row_count": int(len(combined)),
        "trading_dates": {
            "start": trading_dates[0],
            "end": trading_dates[-1],
            "count": len(trading_dates),
        },
        "written_symbol_count": len(written),
        "written_symbols": sorted(written.keys()),
        "snapshots": snapshot_summaries,
    }

    report_file = config.get("report-file")
    if report_file:
        report_path = Path(report_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 80)
    print(f"Written {len(written)} symbol files -> {config['output-path']}", flush=True)
    print("=" * 80)
    return report


def build_factor_snapshot_parallel(
    config: dict,
    universe: list[str],
    trade_date: str,
    worker_count: int,
) -> pd.DataFrame:
    if worker_count <= 1:
        raise ValueError("build_factor_snapshot_parallel requires worker_count > 1")

    total_symbols = len(universe)
    chunk_count = min(total_symbols, worker_count * 4)
    chunks = chunk_symbols(universe, chunk_count)
    rows: list[dict] = []
    completed_symbols = 0
    accepted_rows = 0
    started_at = time.perf_counter()

    print(
        f"[parallel build] trade_date={trade_date} workers={worker_count} chunks={len(chunks)} "
        f"symbols={total_symbols}",
        flush=True,
    )
    with ProcessPoolExecutor(
        max_workers=worker_count,
        initializer=_initialize_parallel_builder,
        initargs=(config["tushare-data-path"],),
    ) as executor:
        futures = [
            executor.submit(
                _build_symbol_rows_chunk,
                (
                    chunk_index,
                    chunk,
                    trade_date,
                    config.get("market-symbol", "000300.SH"),
                    config["tushare-data-path"],
                ),
            )
            for chunk_index, chunk in enumerate(chunks, start=1)
        ]
        for completed_chunks, future in enumerate(as_completed(futures), start=1):
            payload = future.result()
            rows.extend(payload["rows"])
            completed_symbols += int(payload["requested"])
            accepted_rows += int(payload["accepted"])
            print_progress_line(
                f"build {trade_date} parallel",
                {
                    "completed": completed_symbols,
                    "total": total_symbols,
                    "elapsed_seconds": time.perf_counter() - started_at,
                },
                extra_fields=[
                    ("accepted", accepted_rows),
                    ("chunks", f"{completed_chunks}/{len(chunks)}"),
                    ("chunk_id", payload["chunk_id"]),
                ],
            )

    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=[
            "ts_code",
            "trade_date",
            *OUTPUT_COLUMNS[1:],
        ])

    builder = BarraCNE5FactorBuilder(config["tushare-data-path"])
    frame = builder._compose_and_standardize(frame)
    factor_values = frame[FACTOR_COLUMNS]
    frame["missing_factor_count"] = factor_values.isna().sum(axis=1).astype(int)
    output = frame[["ts_code", *OUTPUT_COLUMNS]].copy()
    return output.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def build_factor_history_parallel(
    config: dict,
    trading_dates: list[str],
    universe: list[str],
    worker_count: int,
    heartbeat_seconds: float = 30.0,
) -> tuple[dict[str, list[dict]], list[dict]]:
    if worker_count <= 1:
        raise ValueError("build_factor_history_parallel requires worker_count > 1")

    date_block_size = resolve_parallel_date_block_size(config, trading_dates)
    date_blocks = chunk_trading_dates(trading_dates, date_block_size)
    started_at = time.perf_counter()
    rows_by_symbol: dict[str, list[dict]] = defaultdict(list)
    snapshot_summaries: list[dict] = []
    completed_dates = 0
    completed_blocks = 0

    print(
        f"[parallel range] workers={worker_count} block_size={date_block_size} "
        f"blocks={len(date_blocks)} trading_dates={len(trading_dates)}",
        flush=True,
    )

    with ProcessPoolExecutor(
        max_workers=worker_count,
        initializer=_initialize_parallel_builder,
        initargs=(config["tushare-data-path"],),
    ) as executor:
        future_to_block = {
            executor.submit(
                _build_date_block,
                (
                    block_index,
                    block,
                    universe,
                    config.get("market-symbol", "000300.SH"),
                    config["tushare-data-path"],
                ),
            ): block
            for block_index, block in enumerate(date_blocks, start=1)
        }

        pending = set(future_to_block.keys())
        while pending:
            done, pending = wait(pending, timeout=max(1.0, float(heartbeat_seconds)), return_when=FIRST_COMPLETED)
            if not done:
                print(
                    f"[parallel range heartbeat] blocks={completed_blocks}/{len(date_blocks)} "
                    f"dates={completed_dates}/{len(trading_dates)} "
                    f"elapsed={format_seconds(time.perf_counter() - started_at)}",
                    flush=True,
                )
                continue

            for future in done:
                payload = future.result()
                completed_blocks += 1
                completed_dates += int(payload["date_count"])
                for row in payload["rows"]:
                    row_data = dict(row)
                    ts_code = str(row_data.pop("ts_code"))
                    rows_by_symbol[ts_code].append(row_data)
                snapshot_summaries.extend(payload["snapshots"])
                print_progress_line(
                    "parallel dates",
                    {
                        "completed": completed_dates,
                        "total": len(trading_dates),
                        "elapsed_seconds": time.perf_counter() - started_at,
                    },
                    extra_fields=[
                        ("blocks", f"{completed_blocks}/{len(date_blocks)}"),
                        ("date_range", f"{payload['trade_dates'][0]}->{payload['trade_dates'][-1]}"),
                        ("rows", payload["row_count"]),
                    ],
                )

    snapshot_summaries = sorted(snapshot_summaries, key=lambda item: item["trade_date"])
    return rows_by_symbol, snapshot_summaries


def run_factor_bridge(config: dict) -> dict:
    source_mode = (config.get("factor-source-mode") or "auto").strip().lower()
    external_factor_path = config.get("external-factor-path")
    if source_mode == "import" or (source_mode == "auto" and external_factor_path):
        return run_external_factor_import(config)
    if source_mode == "random":
        return run_random_factor_generation(config)

    loader = BarraCNE5DataLoader(config["tushare-data-path"])
    builder = BarraCNE5FactorBuilder(config["tushare-data-path"], loader=loader)

    if config.get("date"):
        trading_dates = [str(config["date"])]
    else:
        trading_dates = loader.get_trading_dates(
            start_date=config["start-date"],
            end_date=config["end-date"],
            reference_symbol=config.get("market-symbol", "000300.SH"),
        )

    if not trading_dates:
        raise ValueError("No trading dates were discovered for the requested range.")

    universe = build_universe(loader, config, trading_dates[-1])
    if not universe:
        raise ValueError("No stock universe could be resolved for Barra CNE5 factor building.")

    rows_by_symbol: dict[str, list[dict]] = defaultdict(list)
    snapshot_summaries = []
    worker_count = resolve_factor_worker_count(config, trading_dates, len(universe))
    effective_worker_count = worker_count
    parallel_mode = "single-process"
    symbol_report_every = progress_interval(config, "progress-interval-symbols", 500)
    file_report_every = progress_interval(config, "progress-interval-files", 100)
    print("=" * 80)
    print(f"Building Barra CNE5 factors for {len(universe)} symbols", flush=True)
    print(f"Trade dates: {trading_dates[0]} -> {trading_dates[-1]} ({len(trading_dates)} sessions)", flush=True)
    print(f"Factor workers: {worker_count}", flush=True)
    if worker_count > 1 and len(trading_dates) > 1:
        print(f"Parallel date block size: {resolve_parallel_date_block_size(config, trading_dates)}", flush=True)
    print("=" * 80)

    if worker_count > 1 and len(trading_dates) > 1:
        try:
            rows_by_symbol, snapshot_summaries = build_factor_history_parallel(
                config=config,
                trading_dates=trading_dates,
                universe=universe,
                worker_count=worker_count,
            )
            parallel_mode = "date-block-process-pool"
        except (PermissionError, OSError, RuntimeError) as error:
            worker_count = 1
            effective_worker_count = 1
            print(
                f"[parallel range] unavailable in current runtime ({error}). "
                "Falling back to single-process build.",
                flush=True,
            )

    if not snapshot_summaries:
        for index, trade_date in enumerate(trading_dates, start=1):
            print(
                f"[date {index}/{len(trading_dates)}] building snapshot for {trade_date} "
                f"with {len(universe)} symbols",
                flush=True,
            )
            if worker_count > 1:
                try:
                    snapshot = build_factor_snapshot_parallel(
                        config=config,
                        universe=universe,
                        trade_date=trade_date,
                        worker_count=worker_count,
                    )
                    parallel_mode = "single-day-process-pool"
                except (PermissionError, OSError, RuntimeError) as error:
                    worker_count = 1
                    effective_worker_count = 1
                    print(
                        f"[parallel build] unavailable in current runtime ({error}). "
                        "Falling back to single-process build.",
                        flush=True,
                    )
                    snapshot = builder.build_factor_snapshot(
                        universe=universe,
                        trade_date=trade_date,
                        market_symbol=config.get("market-symbol", "000300.SH"),
                        progress_callback=lambda payload: print_progress_line(
                            f"build {payload.get('trade_date')}",
                            payload,
                            extra_fields=[
                                ("accepted", payload.get("accepted")),
                                ("current", payload.get("current_symbol")),
                            ],
                        ),
                        progress_interval=symbol_report_every,
                    )
            else:
                snapshot = builder.build_factor_snapshot(
                    universe=universe,
                    trade_date=trade_date,
                    market_symbol=config.get("market-symbol", "000300.SH"),
                    progress_callback=lambda payload: print_progress_line(
                        f"build {payload.get('trade_date')}",
                        payload,
                        extra_fields=[
                            ("accepted", payload.get("accepted")),
                            ("current", payload.get("current_symbol")),
                        ],
                    ),
                    progress_interval=symbol_report_every,
                )
            coverage = 0.0
            if not snapshot.empty:
                for _, row in snapshot.iterrows():
                    row_data = row.to_dict()
                    ts_code = str(row_data.pop("ts_code"))
                    rows_by_symbol[ts_code].append(row_data)
                coverage = 1.0 - float(snapshot["missing_factor_count"].mean() / max(len(snapshot.columns), 1))

            snapshot_summaries.append({
                "trade_date": trade_date,
                "rows": int(len(snapshot)),
                "coverage": round(max(0.0, min(1.0, coverage)), 4),
            })
            print(
                f"[date {index}/{len(trading_dates)}] completed {trade_date} rows={len(snapshot):>4} "
                f"coverage={round(max(0.0, min(1.0, coverage)) * 100.0, 2):.2f}%",
                flush=True,
            )

    combined = pd.DataFrame(
        [
            {"ts_code": ts_code, **row}
            for ts_code, rows in rows_by_symbol.items()
            for row in rows
        ]
    )
    unique_symbols = int(combined["ts_code"].nunique()) if "ts_code" in combined.columns else 0
    print(
        f"[write prepare] total_rows={len(combined)} symbols={unique_symbols} "
        f"output={config['output-path']}",
        flush=True,
    )
    written = builder.write_factor_history(
        combined,
        config["output-path"],
        progress_callback=lambda payload: print_progress_line(
            "write files",
            payload,
            extra_fields=[
                ("symbol", payload.get("ts_code")),
                ("rows", payload.get("rows")),
                ("file", Path(payload.get("file_path", "")).name if payload.get("file_path") else None),
            ],
        ),
        progress_interval=file_report_every,
    )

    report = {
        "status": "ok",
        "mode": "build",
        "tushare_data_path": config["tushare-data-path"],
        "output_path": config["output-path"],
        "factor_worker_count": effective_worker_count,
        "parallel_mode": parallel_mode,
        "parallel_date_block_size": resolve_parallel_date_block_size(config, trading_dates) if len(trading_dates) > 1 else 1,
        "universe": config.get("universe", "all-a"),
        "resolved_symbol_count": len(universe),
        "trading_dates": {
            "start": trading_dates[0],
            "end": trading_dates[-1],
            "count": len(trading_dates),
        },
        "written_symbol_count": len(written),
        "written_symbols": sorted(written.keys()),
        "snapshots": snapshot_summaries,
    }

    report_file = config.get("report-file")
    if report_file:
        report_path = Path(report_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 80)
    print(f"Written {len(written)} symbol files -> {config['output-path']}", flush=True)
    print("=" * 80)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Barra CNE5 factor snapshots from Tushare parquet datasets")
    parser.add_argument("--config")
    parser.add_argument("--tushare-data-path")
    parser.add_argument("--output-path")
    parser.add_argument("--report-file")
    parser.add_argument("--factor-source-mode")
    parser.add_argument("--random-factor-seed", type=int)
    parser.add_argument("--external-factor-path")
    parser.add_argument("--universe")
    parser.add_argument("--index-code")
    parser.add_argument("--market-symbol")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--date")
    parser.add_argument("--factor-worker-count")
    parser.add_argument("--parallel-date-block-size", type=int)
    parser.add_argument("--progress-interval-symbols", type=int)
    parser.add_argument("--progress-interval-files", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    overrides = {
        "tushare-data-path": args.tushare_data_path,
        "output-path": args.output_path,
        "report-file": args.report_file,
        "factor-source-mode": args.factor_source_mode,
        "random-factor-seed": args.random_factor_seed,
        "external-factor-path": args.external_factor_path,
        "universe": args.universe,
        "index-code": args.index_code,
        "market-symbol": args.market_symbol,
        "start-date": args.start_date,
        "end-date": args.end_date,
        "date": args.date,
        "factor-worker-count": args.factor_worker_count,
        "parallel-date-block-size": args.parallel_date_block_size,
        "progress-interval-symbols": args.progress_interval_symbols,
        "progress-interval-files": args.progress_interval_files,
    }
    report = run_factor_bridge(load_pipeline_config(args.config, overrides))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
