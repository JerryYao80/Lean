#!/usr/bin/env python3

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from tushare_data_layer import TushareDataLayer
from tushare_lean_export import load_registry_universe


PATH_KEYS = {"registry-file", "tushare-data-path", "dataset-catalog", "report-file"}
FEATURE_WEIGHTS = {
    "signal_momentum_20": -0.15,
    "signal_momentum_5": -0.35,
    "signal_liquidity_5": 0.20,
    "signal_close_location": -0.10,
    "signal_volatility_10": 0.15,
    "signal_gap_abs": -0.05,
}


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
        "top-n": 2,
        "fee-rate": 0.0006,
        "min-score-spread": 0.7,
        "max-average-gap-abs": 0.016,
        "risk-regime-filter-enabled": True,
        "risk-regime-momentum-threshold": -0.005,
        "risk-regime-volatility-threshold": 1.4,
        "report-file": str(root / "Launcher" / "bin" / "Debug" / "bt.log"),
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


def _safe_zscore(series: pd.Series) -> pd.Series:
    numeric = _numeric(series)
    if numeric.notna().sum() <= 1:
        return pd.Series(0.0, index=series.index)

    std = numeric.std(ddof=0)
    if pd.isna(std) or math.isclose(std, 0.0):
        return pd.Series(0.0, index=series.index)

    mean = numeric.mean()
    return (numeric - mean) / std


def prepare_symbol_frame(symbol: str, frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data["trade_date"] = data["trade_date"].astype(str).str.zfill(8)
    data = data.sort_values("trade_date").reset_index(drop=True)

    for column in ["pre_close", "open", "high", "low", "close", "pct_chg", "amount", "vol"]:
        if column in data.columns:
            data[column] = _numeric(data[column])

    data["symbol"] = symbol
    data["trade_return"] = data["close"] / data["open"] - 1
    data["gap_return"] = data["open"] / data["pre_close"] - 1
    price_range = (data["high"] - data["low"]).replace(0, float("nan"))
    data["close_location"] = ((data["close"] - data["low"]) / price_range).clip(0, 1).fillna(0.5)
    data["range_pct"] = (data["high"] - data["low"]) / data["pre_close"]
    data["momentum_5"] = data["close"] / data["close"].shift(5) - 1
    data["momentum_20"] = data["close"] / data["close"].shift(20) - 1
    data["volatility_10"] = data["pct_chg"].rolling(10).std(ddof=0)
    data["liquidity_5"] = data["amount"].rolling(5).mean()
    data["gap_abs"] = data["gap_return"].abs()

    for feature in ["momentum_20", "momentum_5", "liquidity_5", "close_location", "volatility_10", "gap_abs"]:
        data[f"signal_{feature}"] = data[feature].shift(1)

    return data.astype(object).where(pd.notna(data), None)


def compute_cross_section_scores(panel: pd.DataFrame) -> pd.DataFrame:
    required = ["trade_date", "symbol", "trade_return", *FEATURE_WEIGHTS.keys()]
    scored = panel.copy()
    scored = scored.dropna(subset=required).reset_index(drop=True)
    if scored.empty:
        return scored

    score_frames = []
    for trade_date, group in scored.groupby("trade_date", sort=True):
        group = group.copy()
        total = pd.Series(0.0, index=group.index)
        for field, weight in FEATURE_WEIGHTS.items():
            total = total + _safe_zscore(group[field]) * weight
        group["score"] = total
        score_frames.append(group)

    if not score_frames:
        return pd.DataFrame(columns=list(scored.columns) + ["score"])

    return pd.concat(score_frames, ignore_index=True)


def backtest_from_scores(
    scored: pd.DataFrame,
    top_n: int = 3,
    fee_rate: float = 0.0006,
    min_score_spread: float = 0.0,
    max_average_gap_abs: float | None = None,
    risk_regime_filter_enabled: bool = False,
    risk_regime_momentum_threshold: float | None = None,
    risk_regime_volatility_threshold: float | None = None,
) -> tuple[pd.DataFrame, dict]:
    daily_rows = []
    selection_counter: Counter[str] = Counter()
    equity = 1.0
    scored_trade_days = 0
    skipped_low_conviction_days = 0
    skipped_gap_risk_days = 0
    skipped_risk_regime_days = 0

    for trade_date, group in scored.groupby("trade_date", sort=True):
        scored_trade_days += 1
        score_series = _numeric(group["score"])
        if score_series.empty:
            continue

        score_spread = float(score_series.max() - score_series.median())
        if min_score_spread > 0 and score_spread < min_score_spread:
            skipped_low_conviction_days += 1
            continue

        market_signal_momentum5_mean = float(_numeric(group["signal_momentum_5"]).mean()) if "signal_momentum_5" in group.columns else 0.0
        market_signal_volatility10_mean = float(_numeric(group["signal_volatility_10"]).mean()) if "signal_volatility_10" in group.columns else 0.0
        in_risk_regime = (
            risk_regime_filter_enabled
            and risk_regime_momentum_threshold is not None
            and risk_regime_volatility_threshold is not None
            and market_signal_momentum5_mean <= float(risk_regime_momentum_threshold)
            and market_signal_volatility10_mean >= float(risk_regime_volatility_threshold)
        )
        if in_risk_regime:
            skipped_risk_regime_days += 1
            continue

        picks = group.sort_values("score", ascending=False).head(top_n).reset_index(drop=True)
        if picks.empty:
            continue

        average_gap_abs = float(_numeric(picks["signal_gap_abs"]).mean()) if "signal_gap_abs" in picks.columns else 0.0
        if max_average_gap_abs is not None and average_gap_abs > max_average_gap_abs:
            skipped_gap_risk_days += 1
            continue

        gross_return = float(_numeric(picks["trade_return"]).mean())
        net_return = gross_return - fee_rate
        equity *= 1 + net_return
        symbols = picks["symbol"].tolist()
        selection_counter.update(symbols)

        daily_rows.append({
            "trade_date": trade_date,
            "selected_symbols": ",".join(symbols),
            "gross_return": gross_return,
            "net_return": net_return,
            "equity": equity,
            "score_mean": float(_numeric(picks["score"]).mean()),
            "score_spread": score_spread,
            "average_gap_abs": average_gap_abs,
            "market_signal_momentum5_mean": market_signal_momentum5_mean,
            "market_signal_volatility10_mean": market_signal_volatility10_mean,
        })

    daily = pd.DataFrame(daily_rows)
    summary = summarize_backtest(daily, selection_counter)
    summary.update({
        "scored_trade_days": int(scored_trade_days),
        "skipped_low_conviction_days": int(skipped_low_conviction_days),
        "skipped_gap_risk_days": int(skipped_gap_risk_days),
        "skipped_risk_regime_days": int(skipped_risk_regime_days),
        "selected_trade_days": int(len(daily)),
        "selection_rate": float(len(daily) / scored_trade_days) if scored_trade_days > 0 else 0.0,
        "min_score_spread": float(min_score_spread),
        "max_average_gap_abs": float(max_average_gap_abs) if max_average_gap_abs is not None else None,
        "risk_regime_filter_enabled": bool(risk_regime_filter_enabled),
        "risk_regime_momentum_threshold": float(risk_regime_momentum_threshold) if risk_regime_momentum_threshold is not None else None,
        "risk_regime_volatility_threshold": float(risk_regime_volatility_threshold) if risk_regime_volatility_threshold is not None else None,
    })
    return daily, summary


def summarize_backtest(daily: pd.DataFrame, selection_counter: Counter[str]) -> dict:
    if daily.empty:
        return {
            "trade_days": 0,
            "final_equity": 1.0,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "avg_net_return": 0.0,
            "top_symbols": [],
        }

    returns = _numeric(daily["net_return"])
    equity = _numeric(daily["equity"])
    total_return = float(equity.iloc[-1] - 1)
    trade_days = len(daily)
    annualized_return = float(equity.iloc[-1] ** (252 / trade_days) - 1) if trade_days > 0 else 0.0
    std = float(returns.std(ddof=0))
    sharpe = float((returns.mean() / std) * math.sqrt(252)) if std > 0 else 0.0
    drawdown = equity / equity.cummax() - 1

    return {
        "trade_days": int(trade_days),
        "final_equity": float(equity.iloc[-1]),
        "total_return": total_return,
        "annualized_return": annualized_return,
        "sharpe": sharpe,
        "max_drawdown": float(drawdown.min()),
        "win_rate": float((returns > 0).mean()),
        "avg_net_return": float(returns.mean()),
        "top_symbols": selection_counter.most_common(10),
    }


def build_report_text(summary: dict, config: dict, universe_count: int, loaded_symbol_count: int) -> str:
    lines = [
        "AShare ETF T+0 Feature Strategy",
        f"Date Range: {config['start-date']} -> {config['end-date']}",
        f"Registry Universe: {universe_count}",
        f"Loaded Symbols: {loaded_symbol_count}",
        f"Top N: {config['top-n']}",
        f"Fee Rate: {config['fee-rate']:.6f}",
        f"Min Score Spread: {float(config.get('min-score-spread', 0.0)):.4f}",
        (
            f"Max Average Gap Abs: {float(config.get('max-average-gap-abs')):.4%}"
            if config.get("max-average-gap-abs") is not None
            else "Max Average Gap Abs: disabled"
        ),
        (
            f"Risk Regime Filter: enabled (mom5<={float(config.get('risk-regime-momentum-threshold')):.4f}, vol10>={float(config.get('risk-regime-volatility-threshold')):.4f})"
            if config.get("risk-regime-filter-enabled")
            else "Risk Regime Filter: disabled"
        ),
        "Feature Fields:",
        "- momentum_20: close / close[-20] - 1",
        "- momentum_5: close / close[-5] - 1",
        "- liquidity_5: rolling mean(amount, 5)",
        "- close_location: (close - low) / (high - low)",
        "- volatility_10: rolling std(pct_chg, 10)",
        "- gap_abs: abs(open / pre_close - 1)",
        "Results:",
        f"- Trade Days: {summary['trade_days']}",
        f"- Scored Trade Days: {summary['scored_trade_days']}",
        f"- Selected Trade Days: {summary['selected_trade_days']}",
        f"- Selection Rate: {summary['selection_rate']:.2%}",
        f"- Skipped Low Conviction Days: {summary['skipped_low_conviction_days']}",
        f"- Skipped Gap Risk Days: {summary['skipped_gap_risk_days']}",
        f"- Skipped Risk Regime Days: {summary['skipped_risk_regime_days']}",
        f"- Final Equity: {summary['final_equity']:.6f}",
        f"- Total Return: {summary['total_return']:.2%}",
        f"- Annualized Return: {summary['annualized_return']:.2%}",
        f"- Sharpe: {summary['sharpe']:.4f}",
        f"- Max Drawdown: {summary['max_drawdown']:.2%}",
        f"- Win Rate: {summary['win_rate']:.2%}",
        f"- Avg Net Return: {summary['avg_net_return']:.4%}",
        f"- Top Symbols: {summary['top_symbols']}",
    ]
    return "\n".join(lines) + "\n"


def run_backtest(config: dict) -> dict:
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
            fields=["pre_close", "open", "high", "low", "close", "pct_chg", "amount", "vol"],
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
        min_score_spread=float(config.get("min-score-spread", 0.0) or 0.0),
        max_average_gap_abs=(
            float(config["max-average-gap-abs"])
            if config.get("max-average-gap-abs") is not None
            else None
        ),
        risk_regime_filter_enabled=bool(config.get("risk-regime-filter-enabled", False)),
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
    )

    summary = {
        **summary,
        "registry_universe": len(universe),
        "loaded_symbols": len(prepared_frames),
        "scored_rows": int(len(scored)),
        "feature_weights": FEATURE_WEIGHTS,
    }

    report_text = build_report_text(summary, config, len(universe), len(prepared_frames))
    report_path = Path(config["report-file"])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="Launcher/config/config-ashare-etf-t0-feature-backtest.json")
    parser.add_argument("--report-file")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--top-n", type=int)
    parser.add_argument("--fee-rate", type=float)
    parser.add_argument("--min-score-spread", type=float)
    parser.add_argument("--max-average-gap-abs", type=float)
    parser.add_argument("--risk-regime-filter-enabled", action="store_true")
    parser.add_argument("--risk-regime-momentum-threshold", type=float)
    parser.add_argument("--risk-regime-volatility-threshold", type=float)
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
        "risk-regime-momentum-threshold": args.risk_regime_momentum_threshold,
        "risk-regime-volatility-threshold": args.risk_regime_volatility_threshold,
    }
    if args.risk_regime_filter_enabled:
        overrides["risk-regime-filter-enabled"] = True
    if args.include_money_market_etfs:
        overrides["exclude-money-market-etfs"] = False

    config = load_pipeline_config(args.config, overrides)
    summary = run_backtest(config)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
