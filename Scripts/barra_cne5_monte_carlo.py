#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd


PATH_KEYS = {"daily-summary-file", "factor-exposure-file", "report-file", "json-report-file"}
FACTOR_COLUMNS = [
    "beta",
    "momentum",
    "size",
    "earnyld",
    "resvol",
    "growth",
    "btop",
    "leverage",
    "liquidity",
    "nlsize",
]


@dataclass(frozen=True)
class SimulationSummary:
    scenario: str
    trial_count: int
    horizon_days: int
    median_annual_return: float
    p05_annual_return: float
    p95_annual_return: float
    median_max_drawdown: float
    p95_max_drawdown: float
    median_sharpe: float
    median_calmar: float
    var_95_daily: float
    cvar_95_daily: float
    probability_negative_total_return: float


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config() -> dict:
    root = repo_root()
    return {
        "daily-summary-file": str(root / "Results" / "barra-cne5-daily-summary.csv"),
        "factor-exposure-file": str(root / "Results" / "barra-cne5-factor-exposure.csv"),
        "trial-count": 5000,
        "horizon-days": 252,
        "block-size": 21,
        "seed": 42,
        "factor-perturbation-scale": 0.15,
        "report-file": str(root / "Results" / "barra-cne5-monte-carlo-report.txt"),
        "json-report-file": str(root / "Results" / "barra-cne5-monte-carlo-report.json"),
        "progress-interval-trials": 250,
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


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


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


def progress_interval(value, default: int) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        resolved = default
    return max(1, resolved)


def print_trial_progress(prefix: str, payload: dict) -> None:
    completed = int(payload.get("completed", 0))
    total = max(1, int(payload.get("total", 0)))
    percent = completed * 100.0 / total
    print(
        f"[{prefix}] trials {completed}/{total} ({percent:5.1f}%) "
        f"elapsed={format_seconds(float(payload.get('elapsed_seconds', 0.0)))} "
        f"eta={estimate_eta(float(payload.get('elapsed_seconds', 0.0)), completed, total)}",
        flush=True,
    )


def load_daily_returns(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"trade_date": str})
    if "net_return" not in frame.columns:
        raise ValueError(f"Expected net_return column in {path}")

    data = frame.copy()
    data["trade_date"] = data["trade_date"].astype(str).str.zfill(8)
    data["net_return"] = _numeric(data["net_return"]).fillna(0.0)
    return data.sort_values("trade_date").reset_index(drop=True)


def load_factor_exposures(path: str | Path | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame(columns=["trade_date", *FACTOR_COLUMNS])
    file_path = Path(path)
    if not file_path.exists():
        return pd.DataFrame(columns=["trade_date", *FACTOR_COLUMNS])

    frame = pd.read_csv(file_path, dtype={"trade_date": str})
    data = frame.copy()
    if "trade_date" in data.columns:
        data["trade_date"] = data["trade_date"].astype(str).str.zfill(8)
    for column in FACTOR_COLUMNS:
        if column in data.columns:
            data[column] = _numeric(data[column])
    return data.sort_values("trade_date").reset_index(drop=True)


def block_bootstrap_returns(
    returns: list[float] | np.ndarray,
    trial_count: int,
    horizon_days: int,
    block_size: int,
    seed: int,
    progress_callback=None,
    progress_interval: int | None = None,
) -> list[list[float]]:
    sample = [float(value) for value in returns]
    if not sample:
        return []

    rng = np.random.default_rng(seed)
    effective_block = max(1, int(block_size))
    paths: list[list[float]] = []
    total_trials = int(trial_count)
    report_every = max(1, int(progress_interval or max(1, total_trials // 20)))
    started_at = time.perf_counter()
    for index in range(1, total_trials + 1):
        path: list[float] = []
        while len(path) < int(horizon_days):
            start_index = int(rng.integers(0, len(sample)))
            for offset in range(effective_block):
                path.append(sample[(start_index + offset) % len(sample)])
                if len(path) >= int(horizon_days):
                    break
        paths.append(path[: int(horizon_days)])
        if progress_callback and (index == 1 or index % report_every == 0 or index == total_trials):
            progress_callback({
                "scenario": "block_bootstrap",
                "completed": index,
                "total": total_trials,
                "elapsed_seconds": time.perf_counter() - started_at,
            })
    return paths


def factor_perturbation_returns(
    daily: pd.DataFrame,
    exposures: pd.DataFrame,
    trial_count: int,
    horizon_days: int,
    seed: int,
    perturbation_scale: float,
    progress_callback=None,
    progress_interval: int | None = None,
) -> list[list[float]]:
    merged = daily[["trade_date", "net_return"]].merge(exposures, on="trade_date", how="left")
    if merged.empty:
        return []

    factor_columns = [column for column in FACTOR_COLUMNS if column in merged.columns]
    if not factor_columns:
        return block_bootstrap_returns(
            daily["net_return"].tolist(),
            trial_count=trial_count,
            horizon_days=horizon_days,
            block_size=1,
            seed=seed,
            progress_callback=progress_callback,
            progress_interval=progress_interval,
        )

    design = merged[factor_columns].apply(_numeric).fillna(0.0)
    design = (design - design.mean()) / design.std(ddof=0).replace(0.0, 1.0)
    x = design.to_numpy(dtype=float)
    y = merged["net_return"].to_numpy(dtype=float)
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    fitted = x @ beta
    residuals = y - fitted

    if len(y) == 0:
        return []

    rng = np.random.default_rng(seed)
    return_std = float(np.std(y, ddof=0)) if len(y) > 1 else 0.0
    factor_sigma = np.maximum(np.abs(beta), return_std / max(1, len(factor_columns)))
    covariance = np.diag((float(perturbation_scale) * factor_sigma) ** 2)

    paths: list[list[float]] = []
    total_trials = int(trial_count)
    report_every = max(1, int(progress_interval or max(1, total_trials // 20)))
    started_at = time.perf_counter()
    for trial_index in range(1, total_trials + 1):
        indices = rng.integers(0, len(y), size=int(horizon_days))
        path: list[float] = []
        for sample_index in indices:
            shock = rng.multivariate_normal(np.zeros(len(factor_columns)), covariance)
            perturbed = float(fitted[sample_index] + residuals[sample_index] + np.dot(x[sample_index], shock))
            path.append(perturbed)
        paths.append(path)
        if progress_callback and (trial_index == 1 or trial_index % report_every == 0 or trial_index == total_trials):
            progress_callback({
                "scenario": "factor_perturbation",
                "completed": trial_index,
                "total": total_trials,
                "elapsed_seconds": time.perf_counter() - started_at,
            })
    return paths


def combine_paths(primary: list[list[float]], secondary: list[list[float]]) -> list[list[float]]:
    if not primary:
        return secondary
    if not secondary:
        return primary

    combined: list[list[float]] = []
    for left, right in zip(primary, secondary):
        combined.append([(float(a) + float(b)) / 2.0 for a, b in zip(left, right)])
    return combined


def summarize_paths(paths: list[list[float]], scenario: str) -> SimulationSummary:
    if not paths:
        return SimulationSummary(
            scenario=scenario,
            trial_count=0,
            horizon_days=0,
            median_annual_return=math.nan,
            p05_annual_return=math.nan,
            p95_annual_return=math.nan,
            median_max_drawdown=math.nan,
            p95_max_drawdown=math.nan,
            median_sharpe=math.nan,
            median_calmar=math.nan,
            var_95_daily=math.nan,
            cvar_95_daily=math.nan,
            probability_negative_total_return=math.nan,
        )

    annual_returns = []
    max_drawdowns = []
    sharpes = []
    calmars = []
    all_daily_returns = []
    terminal_returns = []

    for path in paths:
        returns = np.array(path, dtype=float)
        all_daily_returns.extend(returns.tolist())
        equity_curve = np.cumprod(1.0 + returns)
        terminal_return = float(equity_curve[-1] - 1.0)
        terminal_returns.append(terminal_return)

        peak = np.maximum.accumulate(equity_curve)
        drawdown = equity_curve / np.where(peak == 0.0, 1.0, peak) - 1.0
        max_drawdown = float(np.min(drawdown))
        max_drawdowns.append(max_drawdown)

        horizon_days = len(returns)
        annualized = float((equity_curve[-1] ** (252.0 / max(horizon_days, 1))) - 1.0)
        annual_returns.append(annualized)

        daily_std = float(np.std(returns, ddof=0))
        if daily_std <= 0:
            sharpe = 0.0
        else:
            sharpe = float(np.mean(returns) / daily_std * math.sqrt(252.0))
        sharpes.append(sharpe)
        calmars.append(annualized / abs(max_drawdown) if max_drawdown < 0 else math.nan)

    all_daily = np.array(all_daily_returns, dtype=float)
    var_95 = float(np.quantile(all_daily, 0.05))
    cvar_slice = all_daily[all_daily <= var_95]
    cvar_95 = float(cvar_slice.mean()) if len(cvar_slice) > 0 else var_95

    return SimulationSummary(
        scenario=scenario,
        trial_count=len(paths),
        horizon_days=len(paths[0]),
        median_annual_return=float(np.median(annual_returns)),
        p05_annual_return=float(np.quantile(annual_returns, 0.05)),
        p95_annual_return=float(np.quantile(annual_returns, 0.95)),
        median_max_drawdown=float(np.median(max_drawdowns)),
        p95_max_drawdown=float(np.quantile(max_drawdowns, 0.95)),
        median_sharpe=float(np.median(sharpes)),
        median_calmar=float(np.nanmedian(calmars)),
        var_95_daily=var_95,
        cvar_95_daily=cvar_95,
        probability_negative_total_return=float(np.mean(np.array(terminal_returns, dtype=float) < 0.0)),
    )


def render_report(
    config: dict,
    daily: pd.DataFrame,
    summaries: list[SimulationSummary],
) -> str:
    lines = [
        "=" * 100,
        "Barra CNE5 Monte Carlo Report",
        "=" * 100,
        f"Base period: {daily['trade_date'].iloc[0]} -> {daily['trade_date'].iloc[-1]} ({len(daily)} trading days)",
        f"Trials: {config['trial-count']} | Horizon: {config['horizon-days']} | Block size: {config['block-size']}",
        "",
    ]
    for summary in summaries:
        lines.extend([
            f"[{summary.scenario}]",
            f"  Median annual return : {summary.median_annual_return:.2%}",
            f"  5%-95% annual return : {summary.p05_annual_return:.2%} -> {summary.p95_annual_return:.2%}",
            f"  Median max drawdown  : {summary.median_max_drawdown:.2%}",
            f"  95% max drawdown     : {summary.p95_max_drawdown:.2%}",
            f"  Median sharpe/calmar : {summary.median_sharpe:.2f} / {summary.median_calmar:.2f}",
            f"  Daily VaR / CVaR     : {summary.var_95_daily:.2%} / {summary.cvar_95_daily:.2%}",
            f"  Prob(total return<0) : {summary.probability_negative_total_return:.2%}",
            "",
        ])
    lines.append("=" * 100)
    return "\n".join(lines)


def run_monte_carlo(config: dict) -> dict:
    print("=" * 100)
    print("Barra CNE5 Monte Carlo", flush=True)
    print("=" * 100)
    print(f"[load inputs] daily_summary={config['daily-summary-file']}", flush=True)
    daily = load_daily_returns(config["daily-summary-file"])
    exposures = load_factor_exposures(config.get("factor-exposure-file"))
    exposure_columns = [column for column in FACTOR_COLUMNS if column in exposures.columns]
    print(
        f"[load inputs] daily_rows={len(daily)} exposure_rows={len(exposures)} "
        f"factor_columns={len(exposure_columns)}",
        flush=True,
    )

    trial_report_every = progress_interval(config.get("progress-interval-trials"), 250)

    print(
        f"[scenario block_bootstrap] trials={config['trial-count']} horizon_days={config['horizon-days']} "
        f"block_size={config['block-size']}",
        flush=True,
    )
    baseline_paths = block_bootstrap_returns(
        daily["net_return"].tolist(),
        trial_count=int(config["trial-count"]),
        horizon_days=int(config["horizon-days"]),
        block_size=int(config["block-size"]),
        seed=int(config["seed"]),
        progress_callback=lambda payload: print_trial_progress("block bootstrap", payload),
        progress_interval=trial_report_every,
    )
    print(
        f"[scenario factor_perturbation] trials={config['trial-count']} horizon_days={config['horizon-days']} "
        f"perturbation_scale={config.get('factor-perturbation-scale', 0.15)}",
        flush=True,
    )
    if not exposure_columns:
        print("[scenario factor_perturbation] no factor exposure columns found, falling back to return resampling", flush=True)
    factor_paths = factor_perturbation_returns(
        daily=daily,
        exposures=exposures,
        trial_count=int(config["trial-count"]),
        horizon_days=int(config["horizon-days"]),
        seed=int(config["seed"]) + 1,
        perturbation_scale=float(config.get("factor-perturbation-scale", 0.15)),
        progress_callback=lambda payload: print_trial_progress("factor perturbation", payload),
        progress_interval=trial_report_every,
    )
    print(
        f"[combine] baseline_paths={len(baseline_paths)} factor_paths={len(factor_paths)}",
        flush=True,
    )
    combined_paths = combine_paths(baseline_paths, factor_paths)

    print("[summarize] computing scenario statistics", flush=True)
    summaries = [
        summarize_paths(baseline_paths, "block_bootstrap"),
        summarize_paths(factor_paths, "factor_perturbation"),
        summarize_paths(combined_paths, "combined"),
    ]
    report_text = render_report(config, daily, summaries)

    report_file = Path(config["report-file"])
    report_file.parent.mkdir(parents=True, exist_ok=True)
    print(f"[write report] text_report={report_file}", flush=True)
    report_file.write_text(report_text, encoding="utf-8")

    payload = {
        "base_backtest": {
            "start_date": str(daily["trade_date"].iloc[0]),
            "end_date": str(daily["trade_date"].iloc[-1]),
            "trade_days": int(len(daily)),
        },
        "scenarios": {summary.scenario: asdict(summary) for summary in summaries},
    }

    json_report_file = Path(config["json-report-file"])
    json_report_file.parent.mkdir(parents=True, exist_ok=True)
    print(f"[write report] json_report={json_report_file}", flush=True)
    json_report_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 100)
    print("Monte Carlo Completed", flush=True)
    print("=" * 100)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Monte Carlo analysis for Barra CNE5 backtest outputs")
    parser.add_argument("--config")
    parser.add_argument("--daily-summary-file")
    parser.add_argument("--factor-exposure-file")
    parser.add_argument("--trial-count", type=int)
    parser.add_argument("--horizon-days", type=int)
    parser.add_argument("--block-size", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--factor-perturbation-scale", type=float)
    parser.add_argument("--report-file")
    parser.add_argument("--json-report-file")
    parser.add_argument("--progress-interval-trials", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    overrides = {
        "daily-summary-file": args.daily_summary_file,
        "factor-exposure-file": args.factor_exposure_file,
        "trial-count": args.trial_count,
        "horizon-days": args.horizon_days,
        "block-size": args.block_size,
        "seed": args.seed,
        "factor-perturbation-scale": args.factor_perturbation_scale,
        "report-file": args.report_file,
        "json-report-file": args.json_report_file,
        "progress-interval-trials": args.progress_interval_trials,
    }
    report = run_monte_carlo(load_pipeline_config(args.config, overrides))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
