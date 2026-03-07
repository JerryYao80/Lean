#!/usr/bin/env python3

import argparse
import json
import math
import random
import sys
from pathlib import Path

import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from ashare_etf_t0_feature_backtest import (
    backtest_from_scores,
    compute_cross_section_scores,
    prepare_symbol_frame,
)
from tushare_data_layer import TushareDataLayer
from tushare_lean_export import load_registry_universe


PATH_KEYS = {"registry-file", "tushare-data-path", "dataset-catalog", "report-file"}
PRICE_FIELDS = ["pre_close", "open", "high", "low", "close", "pct_chg", "amount", "vol"]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config() -> dict:
    root = repo_root()
    return {
        "registry-file": str(root / "Common" / "Securities" / "Equity" / "AShareETFMetadata.cs"),
        "tushare-data-path": "/home/project/tushare-downloader/tushare_data",
        "dataset-catalog": str(root / "Launcher" / "config" / "config-ashare-dataset-catalog.json"),
        "start-date": "20240101",
        "end-date": "20251231",
        "exclude-money-market-etfs": True,
        "top-n": 3,
        "fee-rate": 0.0006,
        "trial-count": 500,
        "horizon-days": 63,
        "block-size": 5,
        "extra-fee-rate": 0.0004,
        "shock-probability": 0.04,
        "shock-mean": 0.012,
        "shock-std": 0.006,
        "seed": 42,
        "report-file": str(root / "Launcher" / "bin" / "Debug" / "AShareEtfT0FeatureIntradayAlgorithm-monte-carlo.log"),
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


def build_base_backtest(config: dict) -> tuple[pd.DataFrame, dict]:
    data_layer = TushareDataLayer(config["tushare-data-path"], config["dataset-catalog"])
    universe = load_registry_universe(
        config["registry-file"],
        exclude_money_market=config.get("exclude-money-market-etfs", True),
    )

    prepared_frames = []
    for symbol in universe:
        frame = data_layer.load_dataset(
            "fund_daily",
            symbol=symbol,
            start_date=config["start-date"],
            end_date=config["end-date"],
            fields=PRICE_FIELDS,
        )
        if frame.empty or len(frame) < 25:
            continue
        prepared_frames.append(prepare_symbol_frame(symbol, frame))

    panel = pd.concat(prepared_frames, ignore_index=True) if prepared_frames else pd.DataFrame()
    scored = compute_cross_section_scores(panel)
    daily, summary = backtest_from_scores(
        scored,
        top_n=int(config["top-n"]),
        fee_rate=float(config["fee-rate"]),
    )

    summary = {
        **summary,
        "registry_universe": len(universe),
        "loaded_symbols": len(prepared_frames),
        "scored_rows": int(len(scored)),
    }
    return daily, summary


def block_bootstrap_returns(
    returns: list[float],
    trial_count: int,
    horizon_days: int,
    block_size: int,
    seed: int | None = None,
) -> list[list[float]]:
    if trial_count <= 0 or horizon_days <= 0 or block_size <= 0 or not returns:
        return []

    rng = random.Random(seed)
    paths = []
    count = len(returns)
    for _ in range(trial_count):
        path = []
        while len(path) < horizon_days:
            start = rng.randrange(count)
            for offset in range(block_size):
                path.append(float(returns[(start + offset) % count]))
                if len(path) >= horizon_days:
                    break
        paths.append(path)
    return paths


def apply_fee_stress(paths: list[list[float]], extra_fee_rate: float) -> list[list[float]]:
    return [[float(value) - float(extra_fee_rate) for value in path] for path in paths]


def apply_market_shock_stress(
    paths: list[list[float]],
    shock_probability: float,
    shock_mean: float,
    shock_std: float,
    seed: int | None = None,
) -> list[list[float]]:
    rng = random.Random(seed)
    stressed_paths: list[list[float]] = []

    for path in paths:
        stressed_path = []
        for value in path:
            shocked = float(value)
            if shock_probability > 0 and rng.random() < shock_probability:
                magnitude = abs(rng.gauss(float(shock_mean), float(shock_std)))
                shocked = max(-0.95, shocked - magnitude)
            stressed_path.append(shocked)
        stressed_paths.append(stressed_path)

    return stressed_paths


def compute_path_metrics(path: list[float]) -> dict:
    if not path:
        return {
            "final_equity": 1.0,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
        }

    equity = 1.0
    equity_curve = []
    for value in path:
        equity *= 1 + float(value)
        equity_curve.append(equity)

    equity_series = pd.Series(equity_curve, dtype=float)
    returns = pd.Series(path, dtype=float)
    std = float(returns.std(ddof=0))
    sharpe = float((returns.mean() / std) * math.sqrt(252)) if std > 0 else 0.0
    annualized_return = float(equity ** (252 / len(path)) - 1) if path else 0.0
    drawdown = equity_series / equity_series.cummax() - 1

    return {
        "final_equity": float(equity),
        "total_return": float(equity - 1),
        "annualized_return": annualized_return,
        "max_drawdown": float(drawdown.min()),
        "sharpe": sharpe,
    }


def summarize_paths(paths: list[list[float]]) -> dict:
    if not paths:
        return {
            "trial_count": 0,
            "horizon_days": 0,
            "mean_final_equity": 1.0,
            "median_final_equity": 1.0,
            "p05_final_equity": 1.0,
            "p95_final_equity": 1.0,
            "mean_total_return": 0.0,
            "p05_total_return": 0.0,
            "p50_total_return": 0.0,
            "p95_total_return": 0.0,
            "mean_max_drawdown": 0.0,
            "p50_max_drawdown": 0.0,
            "p95_max_drawdown": 0.0,
            "mean_sharpe": 0.0,
            "median_sharpe": 0.0,
            "loss_probability": 0.0,
        }

    metrics = [compute_path_metrics(path) for path in paths]
    final_equity = pd.Series([item["final_equity"] for item in metrics], dtype=float)
    total_return = pd.Series([item["total_return"] for item in metrics], dtype=float)
    max_drawdown = pd.Series([item["max_drawdown"] for item in metrics], dtype=float)
    sharpe = pd.Series([item["sharpe"] for item in metrics], dtype=float)

    return {
        "trial_count": len(paths),
        "horizon_days": len(paths[0]),
        "mean_final_equity": float(final_equity.mean()),
        "median_final_equity": float(final_equity.median()),
        "p05_final_equity": float(final_equity.quantile(0.05)),
        "p95_final_equity": float(final_equity.quantile(0.95)),
        "mean_total_return": float(total_return.mean()),
        "p05_total_return": float(total_return.quantile(0.05)),
        "p50_total_return": float(total_return.quantile(0.50)),
        "p95_total_return": float(total_return.quantile(0.95)),
        "mean_max_drawdown": float(max_drawdown.mean()),
        "p50_max_drawdown": float(max_drawdown.quantile(0.50)),
        "p95_max_drawdown": float(max_drawdown.quantile(0.95)),
        "mean_sharpe": float(sharpe.mean()),
        "median_sharpe": float(sharpe.median()),
        "loss_probability": float((final_equity < 1).mean()),
    }


def build_report_text(report: dict, config: dict) -> str:
    base = report["base_backtest"]
    lines = [
        "AShare ETF T+0 Monte Carlo",
        f"Date Range: {config['start-date']} -> {config['end-date']}",
        f"Top N: {config['top-n']}",
        f"Base Fee Rate: {float(config['fee-rate']):.6f}",
        f"Trial Count: {report['trial_count']}",
        f"Horizon Days: {report['horizon_days']}",
        f"Block Size: {report['block_size']}",
        f"Extra Fee Rate: {float(config['extra-fee-rate']):.6f}",
        f"Shock Probability: {float(config['shock-probability']):.2%}",
        f"Shock Mean: {float(config['shock-mean']):.2%}",
        f"Shock Std: {float(config['shock-std']):.2%}",
        "Base Backtest:",
        f"- Trade Days: {base['trade_days']}",
        f"- Final Equity: {base['final_equity']:.6f}",
        f"- Total Return: {base['total_return']:.2%}",
        f"- Annualized Return: {base['annualized_return']:.2%}",
        f"- Sharpe: {base['sharpe']:.4f}",
        f"- Max Drawdown: {base['max_drawdown']:.2%}",
        f"- Loaded Symbols: {base['loaded_symbols']}",
        "Scenarios:",
    ]

    for name, summary in report["scenarios"].items():
        lines.extend([
            f"- {name}",
            f"  trial_count={summary['trial_count']}, horizon_days={summary['horizon_days']}",
            f"  median_final_equity={summary['median_final_equity']:.6f}, p05_final_equity={summary['p05_final_equity']:.6f}, p95_final_equity={summary['p95_final_equity']:.6f}",
            f"  p50_total_return={summary['p50_total_return']:.2%}, p95_total_return={summary['p95_total_return']:.2%}",
            f"  p95_max_drawdown={summary['p95_max_drawdown']:.2%}, median_sharpe={summary['median_sharpe']:.4f}, loss_probability={summary['loss_probability']:.2%}",
        ])

    return "\n".join(lines) + "\n"


def run_monte_carlo(config: dict) -> dict:
    daily, base_summary = build_base_backtest(config)
    returns = _numeric(daily.get("net_return", pd.Series(dtype=float))).dropna().astype(float).tolist()
    horizon_days = int(config.get("horizon-days") or len(returns) or 0)
    trial_count = int(config.get("trial-count", 0))
    block_size = int(config.get("block-size", 1))
    seed = int(config.get("seed", 0))

    baseline_paths = block_bootstrap_returns(
        returns,
        trial_count=trial_count,
        horizon_days=horizon_days,
        block_size=block_size,
        seed=seed,
    )
    fee_paths = apply_fee_stress(baseline_paths, float(config.get("extra-fee-rate", 0.0)))
    shock_paths = apply_market_shock_stress(
        baseline_paths,
        shock_probability=float(config.get("shock-probability", 0.0)),
        shock_mean=float(config.get("shock-mean", 0.0)),
        shock_std=float(config.get("shock-std", 0.0)),
        seed=seed + 1,
    )
    combined_paths = apply_market_shock_stress(
        fee_paths,
        shock_probability=float(config.get("shock-probability", 0.0)),
        shock_mean=float(config.get("shock-mean", 0.0)),
        shock_std=float(config.get("shock-std", 0.0)),
        seed=seed + 2,
    )

    report = {
        "base_backtest": base_summary,
        "trial_count": trial_count,
        "horizon_days": horizon_days,
        "block_size": block_size,
        "source_trade_days": len(returns),
        "scenarios": {
            "baseline": summarize_paths(baseline_paths),
            "fee_stress": summarize_paths(fee_paths),
            "shock_stress": summarize_paths(shock_paths),
            "combined_stress": summarize_paths(combined_paths),
        },
    }

    report_path = Path(config["report-file"])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report_text(report, config), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="Launcher/config/config-ashare-etf-t0-monte-carlo.json")
    parser.add_argument("--report-file")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--top-n", type=int)
    parser.add_argument("--fee-rate", type=float)
    parser.add_argument("--trial-count", type=int)
    parser.add_argument("--horizon-days", type=int)
    parser.add_argument("--block-size", type=int)
    parser.add_argument("--extra-fee-rate", type=float)
    parser.add_argument("--shock-probability", type=float)
    parser.add_argument("--shock-mean", type=float)
    parser.add_argument("--shock-std", type=float)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--include-money-market-etfs", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    overrides = {
        "report-file": args.report_file,
        "start-date": args.start_date,
        "end-date": args.end_date,
        "top-n": args.top_n,
        "fee-rate": args.fee_rate,
        "trial-count": args.trial_count,
        "horizon-days": args.horizon_days,
        "block-size": args.block_size,
        "extra-fee-rate": args.extra_fee_rate,
        "shock-probability": args.shock_probability,
        "shock-mean": args.shock_mean,
        "shock-std": args.shock_std,
        "seed": args.seed,
    }
    if args.include_money_market_etfs:
        overrides["exclude-money-market-etfs"] = False

    config = load_pipeline_config(args.config, overrides)
    report = run_monte_carlo(config)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
