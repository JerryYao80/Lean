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
    build_etf_metadata_lookup,
    build_symbol_feature_frame,
    compute_cross_section_scores,
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
        "tushare-data-path": "/home/project/tushare-downloader/tushare_data_v2",
        "dataset-catalog": str(root / "Launcher" / "config" / "config-ashare-dataset-catalog.json"),
        "start-date": "20240101",
        "end-date": "20251231",
        "exclude-money-market-etfs": True,
        "top-n": 2,
        "fee-rate": 0.0006,
        "min-score-spread": 0.7,
        "max-average-gap-abs": 0.016,
        "risk-regime-filter-enabled": True,
        "risk-regime-medium-momentum-threshold": 0.0,
        "risk-regime-medium-volatility-threshold": 1.25,
        "risk-regime-medium-exposure-scale": 0.9,
        "risk-regime-medium-top-n": 2,
        "risk-regime-medium-score-spread-add": 0.0,
        "risk-regime-medium-liquidity-quantile": 0.0,
        "risk-regime-momentum-threshold": -0.005,
        "risk-regime-volatility-threshold": 1.4,
        "risk-regime-high-exposure-scale": 0.1,
        "risk-regime-high-top-n": 2,
        "risk-regime-high-score-spread-add": 0.0,
        "risk-regime-high-liquidity-quantile": 0.0,
        "conditional-signal-nav-premium-z20-weight": 0.0,
        "conditional-signal-nav-premium-z20-orthogonalize": False,
        "conditional-signal-nav-premium-z20-normal-scale": 1.0,
        "conditional-signal-nav-premium-z20-medium-scale": 0.5,
        "conditional-signal-nav-premium-z20-high-scale": 0.0,
        "portfolio-vol-target-enabled": False,
        "portfolio-vol-target-daily-vol": 0.012,
        "portfolio-vol-target-lookback": 20,
        "portfolio-vol-target-min-observations": 10,
        "portfolio-vol-target-floor-scale": 0.5,
        "portfolio-vol-target-cap-scale": 1.0,
        "portfolio-quarter-kelly-enabled": False,
        "portfolio-quarter-kelly-lookback": 20,
        "portfolio-quarter-kelly-min-observations": 10,
        "portfolio-quarter-kelly-floor-scale": 0.25,
        "portfolio-quarter-kelly-cap-scale": 1.0,
        "portfolio-quarter-kelly-fraction": 0.25,
        "portfolio-quarter-kelly-medium-regime-multiplier": 0.75,
        "portfolio-quarter-kelly-high-regime-multiplier": 0.5,
        "trial-count": 500,
        "horizon-days": 63,
        "block-size": 5,
        "extra-fee-rate": 0.0004,
        "shock-probability": 0.04,
        "shock-mean": 0.012,
        "shock-std": 0.006,
        "slippage-probability": 0.35,
        "slippage-mean": 0.0010,
        "slippage-std": 0.0005,
        "regime-down-multiplier": 1.75,
        "regime-high-vol-multiplier": 1.25,
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
    metadata_lookup = build_etf_metadata_lookup(data_layer)
    universe = load_registry_universe(
        config["registry-file"],
        exclude_money_market=config.get("exclude-money-market-etfs", True),
    )

    prepared_frames = []
    for symbol in universe:
        frame = build_symbol_feature_frame(
            data_layer,
            symbol,
            start_date=config["start-date"],
            end_date=config["end-date"],
            metadata_lookup=metadata_lookup,
        )
        if frame.empty or len(frame) < 25:
            continue
        prepared_frames.append(frame)

    panel = pd.concat(prepared_frames, ignore_index=True) if prepared_frames else pd.DataFrame()
    scored = compute_cross_section_scores(panel, config=config)
    regime_frame = build_regime_frame(scored)
    daily, summary = backtest_from_scores(
        scored,
        top_n=int(config["top-n"]),
        fee_rate=float(config["fee-rate"]),
        min_score_spread=float(config.get("min-score-spread", 0.0) or 0.0),
        max_average_gap_abs=(
            float(config["max-average-gap-abs"])
            if config.get("max-average-gap-abs") is not None
            else None
        ),
        risk_regime_filter_enabled=bool(config.get("risk-regime-filter-enabled", False)),
        risk_regime_medium_momentum_threshold=(
            float(config["risk-regime-medium-momentum-threshold"])
            if config.get("risk-regime-medium-momentum-threshold") is not None
            else None
        ),
        risk_regime_medium_volatility_threshold=(
            float(config["risk-regime-medium-volatility-threshold"])
            if config.get("risk-regime-medium-volatility-threshold") is not None
            else None
        ),
        risk_regime_medium_exposure_scale=float(config.get("risk-regime-medium-exposure-scale", 1.0) or 1.0),
        risk_regime_medium_top_n=(
            int(config["risk-regime-medium-top-n"])
            if config.get("risk-regime-medium-top-n") is not None
            else None
        ),
        risk_regime_medium_score_spread_add=float(config.get("risk-regime-medium-score-spread-add", 0.0) or 0.0),
        risk_regime_medium_liquidity_quantile=float(config.get("risk-regime-medium-liquidity-quantile", 0.0) or 0.0),
        risk_regime_momentum_threshold=(
            float(config["risk-regime-momentum-threshold"])
            if config.get("risk-regime-momentum-threshold") is not None
            else None
        ),
        risk_regime_volatility_threshold=(
            float(config["risk-regime-volatility-threshold"])
            if config.get("risk-regime-volatility-threshold") is not None
            else None
        ),
        risk_regime_high_exposure_scale=float(config.get("risk-regime-high-exposure-scale", 0.0) or 0.0),
        risk_regime_high_top_n=(
            int(config["risk-regime-high-top-n"])
            if config.get("risk-regime-high-top-n") is not None
            else None
        ),
        risk_regime_high_score_spread_add=float(config.get("risk-regime-high-score-spread-add", 0.0) or 0.0),
        risk_regime_high_liquidity_quantile=float(config.get("risk-regime-high-liquidity-quantile", 0.0) or 0.0),
        portfolio_vol_target_enabled=bool(config.get("portfolio-vol-target-enabled", False)),
        portfolio_vol_target_daily_vol=float(config.get("portfolio-vol-target-daily-vol", 0.012) or 0.012),
        portfolio_vol_target_lookback=int(config.get("portfolio-vol-target-lookback", 20) or 20),
        portfolio_vol_target_min_observations=int(config.get("portfolio-vol-target-min-observations", 10) or 10),
        portfolio_vol_target_floor_scale=float(config.get("portfolio-vol-target-floor-scale", 0.5) or 0.5),
        portfolio_vol_target_cap_scale=float(config.get("portfolio-vol-target-cap-scale", 1.0) or 1.0),
        portfolio_quarter_kelly_enabled=bool(config.get("portfolio-quarter-kelly-enabled", False)),
        portfolio_quarter_kelly_lookback=int(config.get("portfolio-quarter-kelly-lookback", 20) or 20),
        portfolio_quarter_kelly_min_observations=int(config.get("portfolio-quarter-kelly-min-observations", 10) or 10),
        portfolio_quarter_kelly_floor_scale=float(config.get("portfolio-quarter-kelly-floor-scale", 0.25) or 0.25),
        portfolio_quarter_kelly_cap_scale=float(config.get("portfolio-quarter-kelly-cap-scale", 1.0) or 1.0),
        portfolio_quarter_kelly_fraction=float(config.get("portfolio-quarter-kelly-fraction", 0.25) or 0.25),
        portfolio_quarter_kelly_medium_regime_multiplier=float(config.get("portfolio-quarter-kelly-medium-regime-multiplier", 0.75) or 0.75),
        portfolio_quarter_kelly_high_regime_multiplier=float(config.get("portfolio-quarter-kelly-high-regime-multiplier", 0.5) or 0.5),
    )
    if not daily.empty and not regime_frame.empty:
        daily = daily.merge(regime_frame, on="trade_date", how="left")

    summary = {
        **summary,
        "registry_universe": len(universe),
        "loaded_symbols": len(prepared_frames),
        "scored_rows": int(len(scored)),
        "regime_distribution": regime_frame["regime"].value_counts().to_dict() if not regime_frame.empty else {},
    }
    return daily, summary


def build_regime_frame(scored: pd.DataFrame) -> pd.DataFrame:
    if scored.empty:
        return pd.DataFrame(columns=["trade_date", "market_return_mean", "market_volatility_mean", "market_gap_mean", "regime"])

    rows = []
    for trade_date, group in scored.groupby("trade_date", sort=True):
        rows.append({
            "trade_date": trade_date,
            "market_return_mean": float(_numeric(group["trade_return"]).mean()),
            "market_volatility_mean": float(_numeric(group["signal_volatility_10"]).mean()),
            "market_gap_mean": float(_numeric(group["signal_gap_abs"]).mean()),
        })

    regime_frame = pd.DataFrame(rows)
    if regime_frame.empty:
        return regime_frame

    low_return = float(regime_frame["market_return_mean"].quantile(0.33))
    high_return = float(regime_frame["market_return_mean"].quantile(0.67))
    high_vol = float(regime_frame["market_volatility_mean"].quantile(0.5))

    def classify(row: pd.Series) -> str:
        if float(row["market_return_mean"]) <= low_return:
            trend = "down"
        elif float(row["market_return_mean"]) >= high_return:
            trend = "up"
        else:
            trend = "flat"
        vol = "highvol" if float(row["market_volatility_mean"]) >= high_vol else "lowvol"
        return f"{trend}_{vol}"

    regime_frame["regime"] = regime_frame.apply(classify, axis=1)
    return regime_frame


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


def apply_execution_slippage_stress(
    paths: list[list[float]],
    slippage_probability: float,
    slippage_mean: float,
    slippage_std: float,
    seed: int | None = None,
) -> list[list[float]]:
    rng = random.Random(seed)
    stressed_paths: list[list[float]] = []

    for path in paths:
        stressed_path = []
        for value in path:
            slipped = float(value)
            if slippage_probability > 0 and rng.random() < slippage_probability:
                slip = abs(rng.gauss(float(slippage_mean), float(slippage_std)))
                slipped -= slip
            stressed_path.append(slipped)
        stressed_paths.append(stressed_path)

    return stressed_paths


def build_regime_stress_weights(
    regime_distribution: dict[str, int] | dict[str, float],
    down_multiplier: float,
    high_vol_multiplier: float,
) -> dict[str, float]:
    if not regime_distribution:
        return {}

    weights: dict[str, float] = {}
    for regime, value in regime_distribution.items():
        weight = float(value)
        if regime.startswith("down"):
            weight *= float(down_multiplier)
        if regime.endswith("highvol"):
            weight *= float(high_vol_multiplier)
        weights[regime] = weight

    total = sum(weights.values())
    if total <= 0:
        return {}
    return {regime: weight / total for regime, weight in weights.items()}


def regime_bootstrap_returns(
    daily: pd.DataFrame,
    trial_count: int,
    horizon_days: int,
    block_size: int,
    seed: int | None = None,
    regime_weights: dict[str, float] | None = None,
) -> list[list[float]]:
    if trial_count <= 0 or horizon_days <= 0 or block_size <= 0 or daily.empty or "regime" not in daily.columns:
        returns = _numeric(daily.get("net_return", pd.Series(dtype=float))).dropna().astype(float).tolist()
        return block_bootstrap_returns(returns, trial_count, horizon_days, block_size, seed)

    cleaned = daily[["regime", "net_return"]].copy()
    cleaned["net_return"] = _numeric(cleaned["net_return"])
    cleaned = cleaned.dropna(subset=["regime", "net_return"])
    if cleaned.empty:
        return []

    grouped = {
        regime: group["net_return"].astype(float).tolist()
        for regime, group in cleaned.groupby("regime", sort=True)
        if not group.empty
    }
    if not grouped:
        return []

    populations = list(grouped.keys())
    if regime_weights:
        weights = [float(regime_weights.get(regime, 0.0)) for regime in populations]
        if sum(weights) <= 0:
            weights = [len(grouped[regime]) for regime in populations]
    else:
        weights = [len(grouped[regime]) for regime in populations]

    rng = random.Random(seed)
    paths = []
    for _ in range(trial_count):
        path = []
        while len(path) < horizon_days:
            regime = rng.choices(populations, weights=weights, k=1)[0]
            regime_returns = grouped[regime]
            start = rng.randrange(len(regime_returns))
            for offset in range(block_size):
                path.append(float(regime_returns[(start + offset) % len(regime_returns)]))
                if len(path) >= horizon_days:
                    break
        paths.append(path)
    return paths


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
        f"Min Score Spread: {float(config.get('min-score-spread', 0.0)):.4f}",
        (
            f"Max Average Gap Abs: {float(config.get('max-average-gap-abs')):.4%}"
            if config.get("max-average-gap-abs") is not None
            else "Max Average Gap Abs: disabled"
        ),
        (
            "Risk Regime Scaling: enabled "
            f"(medium: mom5<={float(config.get('risk-regime-medium-momentum-threshold')):.4f}, vol10>={float(config.get('risk-regime-medium-volatility-threshold')):.4f}, scale={float(config.get('risk-regime-medium-exposure-scale', 1.0)):.2f}; "
            f"high: mom5<={float(config.get('risk-regime-momentum-threshold')):.4f}, vol10>={float(config.get('risk-regime-volatility-threshold')):.4f}, scale={float(config.get('risk-regime-high-exposure-scale', 0.0)):.2f})"
            if config.get("risk-regime-filter-enabled")
            else "Risk Regime Scaling: disabled"
        ),
        (
            "Conditional NavPremiumZ20 Overlay: enabled "
            f"(weight={float(config.get('conditional-signal-nav-premium-z20-weight', 0.0) or 0.0):.4f}, orthogonalize={bool(config.get('conditional-signal-nav-premium-z20-orthogonalize', False))}, "
            f"scale normal/medium/high={float(config.get('conditional-signal-nav-premium-z20-normal-scale', 1.0) or 0.0):.2f}/{float(config.get('conditional-signal-nav-premium-z20-medium-scale', 0.5) or 0.0):.2f}/{float(config.get('conditional-signal-nav-premium-z20-high-scale', 0.0) or 0.0):.2f})"
            if not math.isclose(float(config.get('conditional-signal-nav-premium-z20-weight', 0.0) or 0.0), 0.0)
            else "Conditional NavPremiumZ20 Overlay: disabled"
        ),
        (
            "Risk Regime Signal Shrinkage: enabled "
            f"(medium: top_n={int(config.get('risk-regime-medium-top-n', config['top-n']) or config['top-n'])}, spread_add={float(config.get('risk-regime-medium-score-spread-add', 0.0) or 0.0):.4f}, liquidity_q={float(config.get('risk-regime-medium-liquidity-quantile', 0.0) or 0.0):.2f}; "
            f"high: top_n={int(config.get('risk-regime-high-top-n', config['top-n']) or config['top-n'])}, spread_add={float(config.get('risk-regime-high-score-spread-add', 0.0) or 0.0):.4f}, liquidity_q={float(config.get('risk-regime-high-liquidity-quantile', 0.0) or 0.0):.2f})"
            if (
                config.get("risk-regime-filter-enabled")
                and (
                    int(config.get("risk-regime-medium-top-n", config["top-n"]) or config["top-n"]) < int(config["top-n"])
                    or int(config.get("risk-regime-high-top-n", config["top-n"]) or config["top-n"]) < int(config["top-n"])
                    or float(config.get("risk-regime-medium-score-spread-add", 0.0) or 0.0) > 0
                    or float(config.get("risk-regime-high-score-spread-add", 0.0) or 0.0) > 0
                    or float(config.get("risk-regime-medium-liquidity-quantile", 0.0) or 0.0) > 0
                    or float(config.get("risk-regime-high-liquidity-quantile", 0.0) or 0.0) > 0
                )
            )
            else "Risk Regime Signal Shrinkage: inactive"
        ),
        (
            "Portfolio Risk Overlay: enabled "
            f"(vol_target={float(config.get('portfolio-vol-target-daily-vol', 0.012) or 0.0):.4%}, lookback={int(config.get('portfolio-vol-target-lookback', 20) or 20)}, floor/cap={float(config.get('portfolio-vol-target-floor-scale', 0.5) or 0.0):.2f}/{float(config.get('portfolio-vol-target-cap-scale', 1.0) or 0.0):.2f}; "
            f"quarter_kelly lookback={int(config.get('portfolio-quarter-kelly-lookback', 20) or 20)}, floor/cap={float(config.get('portfolio-quarter-kelly-floor-scale', 0.25) or 0.0):.2f}/{float(config.get('portfolio-quarter-kelly-cap-scale', 1.0) or 0.0):.2f}, fraction={float(config.get('portfolio-quarter-kelly-fraction', 0.25) or 0.0):.2f}, regime mult={float(config.get('portfolio-quarter-kelly-medium-regime-multiplier', 0.75) or 0.0):.2f}/{float(config.get('portfolio-quarter-kelly-high-regime-multiplier', 0.5) or 0.0):.2f})"
            if config.get("portfolio-vol-target-enabled") or config.get("portfolio-quarter-kelly-enabled")
            else "Portfolio Risk Overlay: disabled"
        ),
        f"Slippage Probability: {float(config['slippage-probability']):.2%}",
        f"Slippage Mean: {float(config['slippage-mean']):.2%}",
        f"Slippage Std: {float(config['slippage-std']):.2%}",
        f"Trial Count: {report['trial_count']}",
        f"Horizon Days: {report['horizon_days']}",
        f"Block Size: {report['block_size']}",
        f"Extra Fee Rate: {float(config['extra-fee-rate']):.6f}",
        f"Shock Probability: {float(config['shock-probability']):.2%}",
        f"Shock Mean: {float(config['shock-mean']):.2%}",
        f"Shock Std: {float(config['shock-std']):.2%}",
        "Base Backtest:",
        f"- Trade Days: {base['trade_days']}",
        f"- Selected Trade Days: {base['selected_trade_days']}",
        f"- Selection Rate: {base['selection_rate']:.2%}",
        f"- Skipped Low Conviction Days: {base['skipped_low_conviction_days']}",
        f"- Skipped Gap Risk Days: {base['skipped_gap_risk_days']}",
        f"- Average Exposure Scale: {base['average_exposure_scale']:.2%}",
        f"- Average Portfolio Risk Overlay Scale: {base['average_portfolio_risk_overlay_scale']:.2%}",
        f"- Average Vol Target Scale: {base['average_portfolio_vol_target_scale']:.2%}",
        f"- Average Quarter-Kelly Scale: {base['average_portfolio_quarter_kelly_scale']:.2%}",
        f"- Average Selected Count: {base['average_selected_count']:.2f}",
        f"- Medium Risk Regime Days: {base['medium_risk_regime_days']}",
        f"- High Risk Regime Days: {base['high_risk_regime_days']}",
        f"- Final Equity: {base['final_equity']:.6f}",
        f"- Total Return: {base['total_return']:.2%}",
        f"- Annualized Return: {base['annualized_return']:.2%}",
        f"- Sharpe: {base['sharpe']:.4f}",
        f"- Max Drawdown: {base['max_drawdown']:.2%}",
        f"- Loaded Symbols: {base['loaded_symbols']}",
        f"- Regime Distribution: {base['regime_distribution']}",
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
    execution_paths = apply_execution_slippage_stress(
        baseline_paths,
        slippage_probability=float(config.get("slippage-probability", 0.0)),
        slippage_mean=float(config.get("slippage-mean", 0.0)),
        slippage_std=float(config.get("slippage-std", 0.0)),
        seed=seed + 3,
    )
    regime_baseline_paths = regime_bootstrap_returns(
        daily,
        trial_count=trial_count,
        horizon_days=horizon_days,
        block_size=block_size,
        seed=seed + 4,
    )
    regime_stress_weights = build_regime_stress_weights(
        base_summary.get("regime_distribution", {}),
        down_multiplier=float(config.get("regime-down-multiplier", 1.0)),
        high_vol_multiplier=float(config.get("regime-high-vol-multiplier", 1.0)),
    )
    regime_stress_paths = regime_bootstrap_returns(
        daily,
        trial_count=trial_count,
        horizon_days=horizon_days,
        block_size=block_size,
        seed=seed + 5,
        regime_weights=regime_stress_weights,
    )
    regime_combined_paths = apply_execution_slippage_stress(
        apply_market_shock_stress(
            apply_fee_stress(regime_stress_paths, float(config.get("extra-fee-rate", 0.0))),
            shock_probability=float(config.get("shock-probability", 0.0)),
            shock_mean=float(config.get("shock-mean", 0.0)),
            shock_std=float(config.get("shock-std", 0.0)),
            seed=seed + 6,
        ),
        slippage_probability=float(config.get("slippage-probability", 0.0)),
        slippage_mean=float(config.get("slippage-mean", 0.0)),
        slippage_std=float(config.get("slippage-std", 0.0)),
        seed=seed + 7,
    )

    report = {
        "base_backtest": base_summary,
        "trial_count": trial_count,
        "horizon_days": horizon_days,
        "block_size": block_size,
        "source_trade_days": len(returns),
        "regime_stress_weights": regime_stress_weights,
        "scenarios": {
            "baseline": summarize_paths(baseline_paths),
            "fee_stress": summarize_paths(fee_paths),
            "shock_stress": summarize_paths(shock_paths),
            "combined_stress": summarize_paths(combined_paths),
            "execution_stress": summarize_paths(execution_paths),
            "regime_baseline": summarize_paths(regime_baseline_paths),
            "regime_stress": summarize_paths(regime_stress_paths),
            "regime_combined_stress": summarize_paths(regime_combined_paths),
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
    parser.add_argument("--min-score-spread", type=float)
    parser.add_argument("--max-average-gap-abs", type=float)
    parser.add_argument("--risk-regime-filter-enabled", action="store_true")
    parser.add_argument("--risk-regime-medium-momentum-threshold", type=float)
    parser.add_argument("--risk-regime-medium-volatility-threshold", type=float)
    parser.add_argument("--risk-regime-medium-exposure-scale", type=float)
    parser.add_argument("--risk-regime-medium-top-n", type=int)
    parser.add_argument("--risk-regime-medium-score-spread-add", type=float)
    parser.add_argument("--risk-regime-medium-liquidity-quantile", type=float)
    parser.add_argument("--risk-regime-momentum-threshold", type=float)
    parser.add_argument("--risk-regime-volatility-threshold", type=float)
    parser.add_argument("--risk-regime-high-exposure-scale", type=float)
    parser.add_argument("--risk-regime-high-top-n", type=int)
    parser.add_argument("--risk-regime-high-score-spread-add", type=float)
    parser.add_argument("--risk-regime-high-liquidity-quantile", type=float)
    parser.add_argument("--conditional-signal-nav-premium-z20-weight", type=float)
    parser.add_argument("--conditional-signal-nav-premium-z20-orthogonalize", action="store_true")
    parser.add_argument("--conditional-signal-nav-premium-z20-normal-scale", type=float)
    parser.add_argument("--conditional-signal-nav-premium-z20-medium-scale", type=float)
    parser.add_argument("--conditional-signal-nav-premium-z20-high-scale", type=float)
    parser.add_argument("--portfolio-vol-target-enabled", action="store_true")
    parser.add_argument("--portfolio-vol-target-daily-vol", type=float)
    parser.add_argument("--portfolio-vol-target-lookback", type=int)
    parser.add_argument("--portfolio-vol-target-min-observations", type=int)
    parser.add_argument("--portfolio-vol-target-floor-scale", type=float)
    parser.add_argument("--portfolio-vol-target-cap-scale", type=float)
    parser.add_argument("--portfolio-quarter-kelly-enabled", action="store_true")
    parser.add_argument("--portfolio-quarter-kelly-lookback", type=int)
    parser.add_argument("--portfolio-quarter-kelly-min-observations", type=int)
    parser.add_argument("--portfolio-quarter-kelly-floor-scale", type=float)
    parser.add_argument("--portfolio-quarter-kelly-cap-scale", type=float)
    parser.add_argument("--portfolio-quarter-kelly-fraction", type=float)
    parser.add_argument("--portfolio-quarter-kelly-medium-regime-multiplier", type=float)
    parser.add_argument("--portfolio-quarter-kelly-high-regime-multiplier", type=float)
    parser.add_argument("--trial-count", type=int)
    parser.add_argument("--horizon-days", type=int)
    parser.add_argument("--block-size", type=int)
    parser.add_argument("--extra-fee-rate", type=float)
    parser.add_argument("--shock-probability", type=float)
    parser.add_argument("--shock-mean", type=float)
    parser.add_argument("--shock-std", type=float)
    parser.add_argument("--slippage-probability", type=float)
    parser.add_argument("--slippage-mean", type=float)
    parser.add_argument("--slippage-std", type=float)
    parser.add_argument("--regime-down-multiplier", type=float)
    parser.add_argument("--regime-high-vol-multiplier", type=float)
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
        "min-score-spread": args.min_score_spread,
        "max-average-gap-abs": args.max_average_gap_abs,
        "risk-regime-medium-momentum-threshold": args.risk_regime_medium_momentum_threshold,
        "risk-regime-medium-volatility-threshold": args.risk_regime_medium_volatility_threshold,
        "risk-regime-medium-exposure-scale": args.risk_regime_medium_exposure_scale,
        "risk-regime-medium-top-n": args.risk_regime_medium_top_n,
        "risk-regime-medium-score-spread-add": args.risk_regime_medium_score_spread_add,
        "risk-regime-medium-liquidity-quantile": args.risk_regime_medium_liquidity_quantile,
        "risk-regime-momentum-threshold": args.risk_regime_momentum_threshold,
        "risk-regime-volatility-threshold": args.risk_regime_volatility_threshold,
        "risk-regime-high-exposure-scale": args.risk_regime_high_exposure_scale,
        "risk-regime-high-top-n": args.risk_regime_high_top_n,
        "risk-regime-high-score-spread-add": args.risk_regime_high_score_spread_add,
        "risk-regime-high-liquidity-quantile": args.risk_regime_high_liquidity_quantile,
        "conditional-signal-nav-premium-z20-weight": args.conditional_signal_nav_premium_z20_weight,
        "conditional-signal-nav-premium-z20-normal-scale": args.conditional_signal_nav_premium_z20_normal_scale,
        "conditional-signal-nav-premium-z20-medium-scale": args.conditional_signal_nav_premium_z20_medium_scale,
        "conditional-signal-nav-premium-z20-high-scale": args.conditional_signal_nav_premium_z20_high_scale,
        "portfolio-vol-target-daily-vol": args.portfolio_vol_target_daily_vol,
        "portfolio-vol-target-lookback": args.portfolio_vol_target_lookback,
        "portfolio-vol-target-min-observations": args.portfolio_vol_target_min_observations,
        "portfolio-vol-target-floor-scale": args.portfolio_vol_target_floor_scale,
        "portfolio-vol-target-cap-scale": args.portfolio_vol_target_cap_scale,
        "portfolio-quarter-kelly-lookback": args.portfolio_quarter_kelly_lookback,
        "portfolio-quarter-kelly-min-observations": args.portfolio_quarter_kelly_min_observations,
        "portfolio-quarter-kelly-floor-scale": args.portfolio_quarter_kelly_floor_scale,
        "portfolio-quarter-kelly-cap-scale": args.portfolio_quarter_kelly_cap_scale,
        "portfolio-quarter-kelly-fraction": args.portfolio_quarter_kelly_fraction,
        "portfolio-quarter-kelly-medium-regime-multiplier": args.portfolio_quarter_kelly_medium_regime_multiplier,
        "portfolio-quarter-kelly-high-regime-multiplier": args.portfolio_quarter_kelly_high_regime_multiplier,
        "trial-count": args.trial_count,
        "horizon-days": args.horizon_days,
        "block-size": args.block_size,
        "extra-fee-rate": args.extra_fee_rate,
        "shock-probability": args.shock_probability,
        "shock-mean": args.shock_mean,
        "shock-std": args.shock_std,
        "slippage-probability": args.slippage_probability,
        "slippage-mean": args.slippage_mean,
        "slippage-std": args.slippage_std,
        "regime-down-multiplier": args.regime_down_multiplier,
        "regime-high-vol-multiplier": args.regime_high_vol_multiplier,
        "seed": args.seed,
    }
    if args.risk_regime_filter_enabled:
        overrides["risk-regime-filter-enabled"] = True
    if args.include_money_market_etfs:
        overrides["exclude-money-market-etfs"] = False
    if args.conditional_signal_nav_premium_z20_orthogonalize:
        overrides["conditional-signal-nav-premium-z20-orthogonalize"] = True
    if args.portfolio_vol_target_enabled:
        overrides["portfolio-vol-target-enabled"] = True
    if args.portfolio_quarter_kelly_enabled:
        overrides["portfolio-quarter-kelly-enabled"] = True

    config = load_pipeline_config(args.config, overrides)
    report = run_monte_carlo(config)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
