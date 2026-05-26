#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from ashare_etf_t0_feature_backtest import prepare_symbol_frame
from tushare_data_layer import TushareDataLayer


PATH_KEYS = {
    "registry-file",
    "tushare-data-path",
    "dataset-catalog",
    "plan-directory",
    "benchmark-file",
    "daily-nav-file",
    "rebalance-file",
    "summary-file",
    "live-price-snapshot-file",
}
DEFAULT_VARIANT = "dual_rotation"
DEFAULT_START_DATE = "20200101"
DEFAULT_END_DATE = "20251231"
MONEY_MARKET_TICKERS = {"159001", "159003", "159005"}
MONEY_MARKET_NAME_PATTERN = re.compile(r"货币|保证金|快线|现金添益", re.IGNORECASE)
ETF_THEME_BROAD_KEYWORDS = (
    "沪深300",
    "上证50",
    "中证500",
    "中证800",
    "中证1000",
    "中证A500",
    "中证A50",
    "深证100",
    "科创50",
    "创业板指",
    "创业板",
    "央企",
    "全指",
    "宽基",
)
ETF_THEME_DEFENSIVE_KEYWORDS = (
    "红利",
    "低波",
    "价值",
    "黄金",
    "国债",
    "政金债",
    "信用债",
    "可转债",
    "银行",
    "公用事业",
    "公用",
    "水电",
    "煤炭",
)
ETF_THEME_GROWTH_KEYWORDS = (
    "科创",
    "创业板",
    "半导体",
    "芯片",
    "通信",
    "5G",
    "互联网",
    "软件",
    "人工智能",
    "军工",
    "新能源",
    "光伏",
    "储能",
    "机器人",
    "游戏",
    "动漫",
    "传媒",
    "创新药",
    "医药",
    "生物医药",
    "消费",
    "证券",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config() -> dict:
    root = repo_root()
    plan_root = root / "Data" / "alternative" / "ashare-etf-dual-rotation"
    return {
        "registry-file": str(root / "Common" / "Securities" / "Equity" / "AShareETFMetadata.cs"),
        "tushare-data-path": "/home/project/tushare-downloader/tushare_data",
        "dataset-catalog": str(root / "Launcher" / "config" / "config-ashare-dataset-catalog.json"),
        "start-date": DEFAULT_START_DATE,
        "end-date": DEFAULT_END_DATE,
        "benchmark-symbol": "000300.SH",
        "variant-name": DEFAULT_VARIANT,
        "initial-capital": 1000000.0,
        "target-portfolio-exposure": 0.90,
        "target-weight-buffer": 0.985,
        "exclude-money-market-etfs": True,
        "include-qdii": False,
        "min-listed-days": 180,
        "min-price": 0.50,
        "liquidity-quantile": 0.85,
        "max-symbols-per-day": 120,
        "min-sleeve-symbols": 5,
        "layer1-top-k-per-sleeve": 8,
        "t0-top-k": 1,
        "t1-top-k": 3,
        "symbol-rank-decay": 0.82,
        "symbol-min-momentum20": -0.02,
        "symbol-min-momentum60": -0.04,
        "symbol-min-trend-distance60": -0.05,
        "symbol-max-drawdown60": -0.18,
        "sleeve-score-shift": 0.10,
        "fallback-single-sleeve-score": -0.10,
        "caution-benchmark-momentum-threshold": -0.01,
        "caution-benchmark-volatility-threshold": 1.80,
        "caution-exposure-scale": 0.65,
        "defensive-benchmark-momentum-threshold": -0.03,
        "defensive-benchmark-volatility-threshold": 2.60,
        "defensive-exposure-scale": 0.25,
        "risk-off-exposure": 0.25,
        "min-regime-exposure": 0.00,
        "max-exposure-step-up": 0.10,
        "max-exposure-step-down": 0.40,
        "recovery-drawdown-enter": -0.12,
        "recovery-drawdown-exit": -0.04,
        "recovery-exposure-cap": 0.55,
        "portfolio-vol-target-enabled": True,
        "portfolio-vol-target-daily-vol": 0.0095,
        "portfolio-vol-target-lookback": 20,
        "portfolio-vol-target-min-observations": 10,
        "portfolio-vol-target-floor-scale": 0.45,
        "portfolio-vol-target-cap-scale": 0.95,
        "portfolio-quarter-kelly-enabled": True,
        "portfolio-quarter-kelly-lookback": 30,
        "portfolio-quarter-kelly-min-observations": 12,
        "portfolio-quarter-kelly-floor-scale": 0.20,
        "portfolio-quarter-kelly-cap-scale": 0.75,
        "portfolio-quarter-kelly-fraction": 0.20,
        "portfolio-quarter-kelly-medium-regime-multiplier": 0.75,
        "portfolio-quarter-kelly-high-regime-multiplier": 0.50,
        "rebalance-interval-days": 10,
        "rebalance-turnover-threshold": 0.28,
        "rebalance-exposure-change-threshold": 0.15,
        "symbol-persistence-bonus": 0.30,
        "max-single-weight": 0.18,
        "normal-t1-bias": 0.12,
        "caution-t0-bias": 0.12,
        "defensive-t0-bias": 0.24,
        "normal-broad-theme-bonus": 0.10,
        "normal-defensive-theme-penalty": 0.04,
        "caution-defensive-theme-bonus": 0.08,
        "caution-growth-theme-penalty": 0.06,
        "defensive-defensive-theme-bonus": 0.22,
        "defensive-growth-theme-penalty": 0.12,
        "rebalance-slippage-rate": 0.0002,
        "commission-rate": 0.0003,
        "minimum-commission": 5.0,
        "transfer-fee-rate": 0.00002,
        "variants": [DEFAULT_VARIANT],
        "plan-directory": str(plan_root),
        "benchmark-file": str(plan_root / "benchmark" / "000300.SH.csv"),
        "daily-nav-file": str(root / "Results" / "ashare-etf-dual-rotation-daily.csv"),
        "rebalance-file": str(root / "Results" / "ashare-etf-dual-rotation-rebalances.csv"),
        "summary-file": str(root / "Results" / "ashare-etf-dual-rotation-summary.json"),
        "live-price-snapshot-file": "",
        "live-trade-date": "",
        "append-live-trade-date": False,
    }


def resolve_config_paths(config: dict, base_dir: Path) -> dict:
    resolved = dict(config)
    for key in PATH_KEYS:
        value = resolved.get(key)
        if not value:
            continue
        path = Path(str(value))
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        resolved[key] = str(path)
    return resolved


def safe_int(value, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def safe_float(value, default: float) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def safe_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def load_pipeline_config(config_path: str | Path | None = None, overrides: dict | None = None) -> dict:
    config = default_config()
    if config_path:
        path = Path(config_path).resolve()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        config.update(resolve_config_paths(loaded, path.parent))

    if overrides:
        for key, value in overrides.items():
            if value is not None:
                config[key] = value

    config = resolve_config_paths(config, repo_root())
    config["initial-capital"] = safe_float(config.get("initial-capital"), 1000000.0)
    config["target-portfolio-exposure"] = safe_float(config.get("target-portfolio-exposure"), 0.95)
    config["target-weight-buffer"] = safe_float(config.get("target-weight-buffer"), 0.985)
    config["exclude-money-market-etfs"] = safe_bool(config.get("exclude-money-market-etfs"), True)
    config["include-qdii"] = safe_bool(config.get("include-qdii"), False)
    config["min-listed-days"] = safe_int(config.get("min-listed-days"), 120)
    config["min-price"] = safe_float(config.get("min-price"), 0.5)
    config["liquidity-quantile"] = safe_float(config.get("liquidity-quantile"), 0.65)
    config["max-symbols-per-day"] = safe_int(config.get("max-symbols-per-day"), 240)
    config["min-sleeve-symbols"] = safe_int(config.get("min-sleeve-symbols"), 5)
    config["layer1-top-k-per-sleeve"] = safe_int(config.get("layer1-top-k-per-sleeve"), 12)
    config["t0-top-k"] = safe_int(config.get("t0-top-k"), 2)
    config["t1-top-k"] = safe_int(config.get("t1-top-k"), 4)
    config["symbol-rank-decay"] = safe_float(config.get("symbol-rank-decay"), 0.85)
    config["symbol-min-momentum20"] = safe_float(config.get("symbol-min-momentum20"), -0.08)
    config["symbol-min-momentum60"] = safe_float(config.get("symbol-min-momentum60"), -0.05)
    config["symbol-min-trend-distance60"] = safe_float(config.get("symbol-min-trend-distance60"), -0.05)
    config["symbol-max-drawdown60"] = safe_float(config.get("symbol-max-drawdown60"), -0.18)
    config["sleeve-score-shift"] = safe_float(config.get("sleeve-score-shift"), 0.15)
    config["fallback-single-sleeve-score"] = safe_float(config.get("fallback-single-sleeve-score"), -0.10)
    config["caution-benchmark-momentum-threshold"] = safe_float(config.get("caution-benchmark-momentum-threshold"), -0.01)
    config["caution-benchmark-volatility-threshold"] = safe_float(config.get("caution-benchmark-volatility-threshold"), 1.80)
    config["caution-exposure-scale"] = safe_float(config.get("caution-exposure-scale"), 0.65)
    config["defensive-benchmark-momentum-threshold"] = safe_float(config.get("defensive-benchmark-momentum-threshold"), -0.03)
    config["defensive-benchmark-volatility-threshold"] = safe_float(config.get("defensive-benchmark-volatility-threshold"), 2.60)
    config["defensive-exposure-scale"] = safe_float(config.get("defensive-exposure-scale"), 0.25)
    config["risk-off-exposure"] = safe_float(config.get("risk-off-exposure"), 0.25)
    config["min-regime-exposure"] = safe_float(config.get("min-regime-exposure"), 0.00)
    config["max-exposure-step-up"] = safe_float(config.get("max-exposure-step-up"), 0.10)
    config["max-exposure-step-down"] = safe_float(config.get("max-exposure-step-down"), 0.35)
    config["recovery-drawdown-enter"] = safe_float(config.get("recovery-drawdown-enter"), -0.12)
    config["recovery-drawdown-exit"] = safe_float(config.get("recovery-drawdown-exit"), -0.04)
    config["recovery-exposure-cap"] = safe_float(config.get("recovery-exposure-cap"), 0.55)
    config["portfolio-vol-target-enabled"] = safe_bool(config.get("portfolio-vol-target-enabled"), True)
    config["portfolio-vol-target-daily-vol"] = safe_float(config.get("portfolio-vol-target-daily-vol"), 0.011)
    config["portfolio-vol-target-lookback"] = safe_int(config.get("portfolio-vol-target-lookback"), 20)
    config["portfolio-vol-target-min-observations"] = safe_int(config.get("portfolio-vol-target-min-observations"), 10)
    config["portfolio-vol-target-floor-scale"] = safe_float(config.get("portfolio-vol-target-floor-scale"), 0.45)
    config["portfolio-vol-target-cap-scale"] = safe_float(config.get("portfolio-vol-target-cap-scale"), 0.95)
    config["portfolio-quarter-kelly-enabled"] = safe_bool(config.get("portfolio-quarter-kelly-enabled"), True)
    config["portfolio-quarter-kelly-lookback"] = safe_int(config.get("portfolio-quarter-kelly-lookback"), 30)
    config["portfolio-quarter-kelly-min-observations"] = safe_int(config.get("portfolio-quarter-kelly-min-observations"), 12)
    config["portfolio-quarter-kelly-floor-scale"] = safe_float(config.get("portfolio-quarter-kelly-floor-scale"), 0.20)
    config["portfolio-quarter-kelly-cap-scale"] = safe_float(config.get("portfolio-quarter-kelly-cap-scale"), 0.75)
    config["portfolio-quarter-kelly-fraction"] = safe_float(config.get("portfolio-quarter-kelly-fraction"), 0.20)
    config["portfolio-quarter-kelly-medium-regime-multiplier"] = safe_float(config.get("portfolio-quarter-kelly-medium-regime-multiplier"), 0.75)
    config["portfolio-quarter-kelly-high-regime-multiplier"] = safe_float(config.get("portfolio-quarter-kelly-high-regime-multiplier"), 0.50)
    config["rebalance-interval-days"] = safe_int(config.get("rebalance-interval-days"), 5)
    config["rebalance-turnover-threshold"] = safe_float(config.get("rebalance-turnover-threshold"), 0.18)
    config["rebalance-exposure-change-threshold"] = safe_float(config.get("rebalance-exposure-change-threshold"), 0.10)
    config["symbol-persistence-bonus"] = safe_float(config.get("symbol-persistence-bonus"), 0.18)
    config["max-single-weight"] = safe_float(config.get("max-single-weight"), 0.22)
    config["normal-t1-bias"] = safe_float(config.get("normal-t1-bias"), 0.08)
    config["caution-t0-bias"] = safe_float(config.get("caution-t0-bias"), 0.10)
    config["defensive-t0-bias"] = safe_float(config.get("defensive-t0-bias"), 0.18)
    config["normal-broad-theme-bonus"] = safe_float(config.get("normal-broad-theme-bonus"), 0.10)
    config["normal-defensive-theme-penalty"] = safe_float(config.get("normal-defensive-theme-penalty"), 0.04)
    config["caution-defensive-theme-bonus"] = safe_float(config.get("caution-defensive-theme-bonus"), 0.08)
    config["caution-growth-theme-penalty"] = safe_float(config.get("caution-growth-theme-penalty"), 0.06)
    config["defensive-defensive-theme-bonus"] = safe_float(config.get("defensive-defensive-theme-bonus"), 0.22)
    config["defensive-growth-theme-penalty"] = safe_float(config.get("defensive-growth-theme-penalty"), 0.12)
    config["rebalance-slippage-rate"] = safe_float(config.get("rebalance-slippage-rate"), 0.0002)
    config["commission-rate"] = safe_float(config.get("commission-rate"), 0.0003)
    config["minimum-commission"] = safe_float(config.get("minimum-commission"), 5.0)
    config["transfer-fee-rate"] = safe_float(config.get("transfer-fee-rate"), 0.00002)
    config["append-live-trade-date"] = safe_bool(config.get("append-live-trade-date"), False)
    config["variants"] = [str(value) for value in (config.get("variants") or [DEFAULT_VARIANT])]
    return config


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _safe_zscore(series: pd.Series) -> pd.Series:
    values = _numeric(series)
    if values.notna().sum() <= 1:
        return pd.Series(0.0, index=series.index)
    std = values.std(ddof=0)
    if pd.isna(std) or math.isclose(float(std), 0.0):
        return pd.Series(0.0, index=series.index)
    return (values - values.mean()) / std


def _window_return(series: pd.Series, periods: int) -> pd.Series:
    values = _numeric(series).replace(0, float("nan"))
    return values / values.shift(periods).replace(0, float("nan")) - 1.0


def _rolling_drawdown(series: pd.Series, window: int) -> pd.Series:
    values = _numeric(series)
    rolling_peak = values.rolling(window, min_periods=1).max().replace(0, float("nan"))
    return values / rolling_peak - 1.0


def _clamp(value: float, lower: float, upper: float) -> float:
    lower_bound = min(float(lower), float(upper))
    upper_bound = max(float(lower), float(upper))
    return max(lower_bound, min(float(value), upper_bound))


def _lerp(start: float, end: float, t: float) -> float:
    return float(start) + (float(end) - float(start)) * float(t)


def _score_trend(price: float | None, average: float | None, weight: float) -> float:
    if price is None or average is None or not math.isfinite(float(price)) or not math.isfinite(float(average)) or float(average) <= 0.0:
        return 0.0
    distance = float(price) / float(average) - 1.0
    return _clamp(distance / 0.08, -1.0, 1.0) * float(weight)


def _score_centered(value: float | None, center: float, scale: float, weight: float) -> float:
    if value is None or not math.isfinite(float(value)):
        return 0.0
    if not math.isfinite(float(scale)) or math.isclose(float(scale), 0.0):
        return 0.0
    normalized = (float(value) - float(center)) / float(scale)
    return _clamp(normalized, -1.0, 1.0) * float(weight)


def annualized_return(total_return: float, trade_days: int) -> float:
    if trade_days <= 0:
        return 0.0
    if total_return <= -1.0:
        return -1.0
    return (1.0 + total_return) ** (252.0 / float(trade_days)) - 1.0


def compute_max_drawdown(nav: pd.Series) -> float:
    values = _numeric(nav)
    if values.empty:
        return 0.0
    running_max = values.cummax()
    drawdown = values / running_max - 1.0
    return float(drawdown.min())


def sharpe_ratio(returns: pd.Series) -> float:
    values = _numeric(returns).dropna()
    if len(values) < 2:
        return 0.0
    std = values.std(ddof=0)
    if pd.isna(std) or math.isclose(float(std), 0.0):
        return 0.0
    return float(values.mean() / std * math.sqrt(252.0))


def ts_code_market(ts_code: str) -> str:
    return "SSE" if str(ts_code).upper().endswith(".SH") else "SZSE"


def load_registry_trading_modes(registry_file: str | Path) -> dict[str, str]:
    text = Path(registry_file).read_text(encoding="utf-8")
    pattern = re.compile(r'\{\s*"(?P<ticker>\d{6})"\s*,\s*new AShareETFMetadata\s*\{(?P<body>.*?)\}\s*\},', re.S)
    result: dict[str, str] = {}
    for match in pattern.finditer(text):
        ticker = str(match.group("ticker"))
        body = match.group("body")
        mode = "T0" if "TradingMode = ETFTradingMode.T0" in body else "T1"
        market_match = re.search(r'Market\s*=\s*"(?P<market>SSE|SZSE)"', body)
        if market_match is None:
            continue
        suffix = "SH" if market_match.group("market") == "SSE" else "SZ"
        result[f"{ticker}.{suffix}"] = mode
    return result


def is_money_market_etf(ts_code: str, name: str) -> bool:
    ticker = str(ts_code).split(".", 1)[0]
    if ticker.startswith("511") or ticker in MONEY_MARKET_TICKERS:
        return True
    name_text = str(name or "").strip()
    return bool(name_text) and MONEY_MARKET_NAME_PATTERN.search(name_text) is not None


def _contains_any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    normalized = str(text or "").upper()
    return any(keyword.upper() in normalized for keyword in keywords)


def classify_etf_theme(name: str, index_name: str, etf_type: str) -> dict[str, float]:
    combined = f"{name or ''} {index_name or ''} {etf_type or ''}".strip()
    broad = 1.0 if _contains_any_keyword(combined, ETF_THEME_BROAD_KEYWORDS) else 0.0
    defensive = 1.0 if _contains_any_keyword(combined, ETF_THEME_DEFENSIVE_KEYWORDS) else 0.0
    growth = 1.0 if _contains_any_keyword(combined, ETF_THEME_GROWTH_KEYWORDS) else 0.0
    return {
        "broad_theme_score": broad,
        "defensive_theme_score": defensive,
        "growth_theme_score": growth,
    }


def load_etf_metadata(layer: TushareDataLayer, config: dict) -> pd.DataFrame:
    frame = layer.load_dataset("etf_basic")
    if frame.empty:
        raise RuntimeError("etf_basic dataset is empty.")

    registry_modes = load_registry_trading_modes(config["registry-file"])
    data = frame.copy()
    data["ts_code"] = data["ts_code"].astype(str).str.strip().str.upper()
    data["exchange"] = data.get("exchange", "").astype(str).str.strip().str.upper()
    data["list_status"] = data.get("list_status", "").astype(str).str.strip().str.upper()
    data["list_date"] = data.get("list_date", "").astype(str).str.zfill(8)
    data["csname"] = data.get("csname", "").astype(str)
    data["index_name"] = data.get("index_name", "").astype(str)
    data["etf_type"] = data.get("etf_type", "").astype(str)
    data["mgt_fee"] = _numeric(data.get("mgt_fee", 0.0)).fillna(0.0)

    data = data[data["exchange"].isin({"SH", "SZ"})].copy()
    if "list_status" in data.columns:
        data = data[data["list_status"] == "L"].copy()
    if not safe_bool(config.get("include-qdii"), True):
        data = data[data["etf_type"] != "QDII"].copy()
    if safe_bool(config.get("exclude-money-market-etfs"), True):
        data = data[~data.apply(lambda row: is_money_market_etf(row["ts_code"], row.get("csname")), axis=1)].copy()

    data["market"] = data["exchange"].map({"SH": "SSE", "SZ": "SZSE"})
    data["trading_mode"] = data["ts_code"].map(lambda value: registry_modes.get(value, "T1"))
    data = data.drop_duplicates("ts_code").sort_values("ts_code").reset_index(drop=True)
    return data[
        [
            "ts_code",
            "csname",
            "index_code",
            "index_name",
            "list_date",
            "mgt_fee",
            "etf_type",
            "market",
            "trading_mode",
        ]
    ]


def load_benchmark_frame(layer: TushareDataLayer, config: dict) -> pd.DataFrame:
    frame = layer.load_dataset(
        "index_daily",
        symbol=config["benchmark-symbol"],
        start_date=config["start-date"],
        end_date=config["end-date"],
        fields=["pre_close", "open", "close", "pct_chg"],
    )
    if frame.empty:
        raise RuntimeError(f"Benchmark {config['benchmark-symbol']} has no data in the requested range.")
    data = frame.copy()
    data["trade_date"] = data["trade_date"].astype(str).str.zfill(8)
    for field in ["pre_close", "open", "close", "pct_chg"]:
        data[field] = _numeric(data[field])
    data["benchmark_return"] = data["pct_chg"].fillna(0.0) / 100.0
    data["benchmark_ma20"] = _numeric(data["close"]).rolling(20).mean()
    data["benchmark_ma60"] = _numeric(data["close"]).rolling(60).mean()
    data["benchmark_ma120"] = _numeric(data["close"]).rolling(120).mean()
    data["benchmark_momentum_20"] = _window_return(data["close"], 20)
    data["benchmark_momentum_60"] = _window_return(data["close"], 60)
    data["benchmark_volatility_20"] = data["benchmark_return"].rolling(20).std(ddof=0) * 100.0
    data["benchmark_drawdown_120"] = _rolling_drawdown(data["close"], 120)
    data["benchmark_drawdown_252"] = _rolling_drawdown(data["close"], 252)
    return data.sort_values("trade_date").reset_index(drop=True)


def load_live_snapshot_frame(config: dict) -> tuple[str | None, pd.DataFrame]:
    snapshot_path = str(config.get("live-price-snapshot-file") or "").strip()
    if not snapshot_path:
        return None, pd.DataFrame()
    try:
        import ashare_live_market_cache as live_market_cache
        from rt_daily_downloader import TushareRtDailyClient
    except Exception:
        return None, pd.DataFrame()

    payload, quotes = live_market_cache.load_snapshot(snapshot_path)
    trade_date = str(config.get("live-trade-date") or payload.get("trade_date") or "").strip()
    if quotes.empty:
        return (trade_date or None), pd.DataFrame()

    frame = quotes.copy()
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip().str.upper()
    frame = frame[frame["ts_code"].map(TushareRtDailyClient.is_etf_code)].copy()
    if trade_date:
        frame["trade_date"] = trade_date
    elif "trade_date" in frame.columns and frame["trade_date"].notna().any():
        trade_date = str(frame["trade_date"].astype(str).iloc[-1]).zfill(8)
    else:
        trade_date = None
    return trade_date, frame.reset_index(drop=True)


def extend_benchmark_with_live_trade_date(benchmark: pd.DataFrame, live_trade_date: str | None) -> pd.DataFrame:
    if benchmark.empty or not live_trade_date:
        return benchmark
    trade_date = str(live_trade_date).zfill(8)
    if trade_date in set(benchmark["trade_date"].astype(str)):
        return benchmark
    latest = benchmark.sort_values("trade_date").iloc[-1].to_dict()
    latest["trade_date"] = trade_date
    latest["pct_chg"] = 0.0
    latest["benchmark_return"] = 0.0
    latest["benchmark_momentum_20"] = latest.get("benchmark_momentum_20")
    latest["benchmark_volatility_20"] = latest.get("benchmark_volatility_20")
    extended = pd.concat([benchmark, pd.DataFrame([latest])], ignore_index=True)
    return extended.sort_values("trade_date").drop_duplicates("trade_date", keep="last").reset_index(drop=True)


def extend_reference_frame(frame: pd.DataFrame | None, date_field: str, live_trade_date: str | None) -> pd.DataFrame:
    if frame is None or frame.empty or not live_trade_date:
        return frame if frame is not None else pd.DataFrame()
    data = frame.copy()
    if date_field not in data.columns:
        return data
    data[date_field] = data[date_field].astype(str).str.zfill(8)
    trade_date = str(live_trade_date).zfill(8)
    if trade_date in set(data[date_field]):
        return data
    latest = data.sort_values(date_field).iloc[-1].to_dict()
    latest[date_field] = trade_date
    return pd.concat([data, pd.DataFrame([latest])], ignore_index=True).sort_values(date_field).reset_index(drop=True)


def append_live_quote_to_raw_frame(raw_history: pd.DataFrame, quote_row: dict, live_trade_date: str) -> pd.DataFrame:
    data = raw_history.copy()
    if data.empty:
        data = pd.DataFrame(columns=["trade_date", "pre_close", "open", "high", "low", "close", "pct_chg", "amount", "vol"])
    data["trade_date"] = data["trade_date"].astype(str).str.zfill(8)
    trade_date = str(live_trade_date).zfill(8)
    data = data[data["trade_date"] != trade_date].copy()
    latest = data.sort_values("trade_date").iloc[-1].to_dict() if not data.empty else {}

    close = pd.to_numeric(quote_row.get("close"), errors="coerce")
    if pd.isna(close):
        close = pd.to_numeric(quote_row.get("price"), errors="coerce")
    open_price = pd.to_numeric(quote_row.get("open"), errors="coerce")
    high = pd.to_numeric(quote_row.get("high"), errors="coerce")
    low = pd.to_numeric(quote_row.get("low"), errors="coerce")
    pre_close = pd.to_numeric(quote_row.get("pre_close"), errors="coerce")
    pct_chg = pd.to_numeric(quote_row.get("pct_chg"), errors="coerce")
    amount = pd.to_numeric(quote_row.get("amount"), errors="coerce")
    vol = pd.to_numeric(quote_row.get("vol"), errors="coerce")

    if pd.isna(close):
        return data
    if pd.isna(pre_close):
        pre_close = pd.to_numeric(latest.get("close"), errors="coerce")
    if pd.isna(open_price):
        open_price = close
    if pd.isna(high):
        high = max(float(open_price), float(close))
    if pd.isna(low):
        low = min(float(open_price), float(close))
    if pd.isna(pct_chg) and not pd.isna(pre_close) and not math.isclose(float(pre_close), 0.0):
        pct_chg = (float(close) / float(pre_close) - 1.0) * 100.0

    live_row = {
        "trade_date": trade_date,
        "pre_close": pre_close,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "pct_chg": pct_chg,
        "amount": amount,
        "vol": vol,
    }
    return pd.concat([data, pd.DataFrame([live_row])], ignore_index=True).sort_values("trade_date").reset_index(drop=True)


def build_symbol_feature_frame_with_live(
    layer: TushareDataLayer,
    symbol: str,
    metadata_row: pd.Series,
    start_date: str,
    end_date: str,
    live_quotes: dict[str, dict] | None = None,
    live_trade_date: str | None = None,
) -> pd.DataFrame:
    raw_frame = layer.load_dataset(
        "fund_daily",
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        fields=["pre_close", "open", "high", "low", "close", "pct_chg", "amount", "vol"],
    )
    if raw_frame.empty:
        return raw_frame

    if live_trade_date and live_quotes and symbol in live_quotes:
        raw_frame = append_live_quote_to_raw_frame(raw_frame, live_quotes[symbol], live_trade_date)

    nav_frame = pd.DataFrame()
    if "fund_nav" in layer.catalog:
        nav_frame = layer.load_dataset(
            "fund_nav",
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            fields=["unit_nav", "adj_nav"],
        )
        nav_frame = extend_reference_frame(nav_frame, "nav_date", live_trade_date)

    share_size_frame = pd.DataFrame()
    if "etf_share_size" in layer.catalog:
        share_size_frame = layer.load_dataset(
            "etf_share_size",
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            fields=["total_share", "total_size"],
        )
        share_size_frame = extend_reference_frame(share_size_frame, "trade_date", live_trade_date)

    index_frame = pd.DataFrame()
    index_code = str(metadata_row.get("index_code") or "").strip()
    if index_code and "index_daily" in layer.catalog:
        index_frame = layer.load_dataset(
            "index_daily",
            symbol=index_code,
            start_date=start_date,
            end_date=end_date,
            fields=["pre_close", "open", "close"],
        )
        index_frame = extend_reference_frame(index_frame, "trade_date", live_trade_date)

    prepared = prepare_symbol_frame(
        symbol,
        raw_frame,
        nav_frame=nav_frame,
        share_size_frame=share_size_frame,
        index_frame=index_frame,
    )
    if prepared.empty:
        return prepared

    prepared["ts_code"] = symbol
    prepared["name"] = metadata_row.get("csname")
    prepared["trading_mode"] = metadata_row.get("trading_mode")
    prepared["market"] = metadata_row.get("market")
    prepared["index_name"] = metadata_row.get("index_name")
    prepared["etf_type"] = metadata_row.get("etf_type")
    prepared["mgt_fee"] = metadata_row.get("mgt_fee")
    prepared["list_date"] = metadata_row.get("list_date")
    prepared["return_1d"] = _numeric(prepared["pct_chg"]).fillna(0.0) / 100.0
    prepared["premium_abs"] = _numeric(prepared["nav_premium_z20"]).abs()
    close_series = _numeric(prepared["close"])
    index_close_series = _numeric(prepared.get("index_close"))
    prepared["momentum_60"] = _window_return(close_series, 60)
    prepared["momentum_120"] = _window_return(close_series, 120)
    prepared["volatility_20"] = _numeric(prepared["return_1d"]).rolling(20).std(ddof=0)
    prepared["drawdown_60"] = _rolling_drawdown(close_series, 60)
    prepared["liquidity_20"] = _numeric(prepared["amount"]).rolling(20).mean()
    prepared["ma20"] = close_series.rolling(20).mean()
    prepared["ma60"] = close_series.rolling(60).mean()
    prepared["trend_distance_20"] = close_series / _numeric(prepared["ma20"]).replace(0, float("nan")) - 1.0
    prepared["trend_distance_60"] = close_series / _numeric(prepared["ma60"]).replace(0, float("nan")) - 1.0
    prepared["index_momentum_20"] = _window_return(index_close_series, 20)
    prepared["index_momentum_60"] = _window_return(index_close_series, 60)
    prepared["tracking_error_20"] = (_numeric(prepared["return_1d"]) - _numeric(prepared["index_close_return_1"])).rolling(20).std(ddof=0)
    theme_scores = classify_etf_theme(
        str(metadata_row.get("csname") or ""),
        str(metadata_row.get("index_name") or ""),
        str(metadata_row.get("etf_type") or ""),
    )
    for field, value in theme_scores.items():
        prepared[field] = float(value)

    trade_dates = pd.to_datetime(prepared["trade_date"].astype(str), format="%Y%m%d", errors="coerce")
    listed_at = pd.to_datetime(str(metadata_row.get("list_date") or ""), format="%Y%m%d", errors="coerce")
    if pd.isna(listed_at):
        prepared["listed_days"] = None
    else:
        prepared["listed_days"] = (trade_dates - listed_at).dt.days
    return prepared.astype(object).where(pd.notna(prepared), None)


def build_feature_panel(config: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str | None]:
    layer = TushareDataLayer(config["tushare-data-path"], config["dataset-catalog"])
    metadata = load_etf_metadata(layer, config)
    benchmark = load_benchmark_frame(layer, config)
    live_trade_date, live_snapshot = load_live_snapshot_frame(config)

    if config.get("append-live-trade-date"):
        benchmark = extend_benchmark_with_live_trade_date(benchmark, live_trade_date or str(config.get("live-trade-date") or ""))

    live_quotes = {}
    if live_trade_date and not live_snapshot.empty:
        live_quotes = {
            str(row["ts_code"]).strip().upper(): dict(row)
            for row in live_snapshot.to_dict(orient="records")
        }
        if config.get("append-live-trade-date"):
            ranking = live_snapshot.copy()
            ranking["amount"] = _numeric(ranking.get("amount", pd.Series(index=ranking.index, dtype=float)))
            ranking["vol"] = _numeric(ranking.get("vol", pd.Series(index=ranking.index, dtype=float)))
            ranking["close"] = _numeric(ranking.get("close", pd.Series(index=ranking.index, dtype=float)))
            ranking["notional_proxy"] = ranking["amount"].fillna(ranking["vol"] * ranking["close"])
            preselect_count = max(int(config.get("max-symbols-per-day", 0)) * 2, 60)
            selected = ranking.sort_values("notional_proxy", ascending=False).head(preselect_count)
            selected_symbols = set(selected["ts_code"].astype(str).str.strip().str.upper().tolist())
            if selected_symbols:
                metadata = metadata[metadata["ts_code"].isin(selected_symbols)].copy().reset_index(drop=True)

    frames = []
    total = len(metadata.index)
    for index, row in enumerate(metadata.itertuples(index=False), start=1):
        row_series = pd.Series(row._asdict())
        frame = build_symbol_feature_frame_with_live(
            layer=layer,
            symbol=str(row_series["ts_code"]),
            metadata_row=row_series,
            start_date=config["start-date"],
            end_date=config["end-date"],
            live_quotes=live_quotes,
            live_trade_date=live_trade_date if config.get("append-live-trade-date") else None,
        )
        if frame.empty or len(frame.index) < 25:
            continue
        frames.append(frame)
        if index % 100 == 0 or index == total:
            print(f"[etf dual rotation][load] {index}/{total} symbols processed", flush=True)

    panel = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return panel, metadata, benchmark, live_trade_date


def compute_symbol_scores(panel: pd.DataFrame, config: dict) -> pd.DataFrame:
    if panel.empty:
        return panel.copy()

    empty_like = panel.iloc[0:0].copy()
    frames: list[pd.DataFrame] = []
    min_price = float(config["min-price"])
    min_listed_days = int(config["min-listed-days"])
    liquidity_quantile = float(config["liquidity-quantile"])
    max_symbols_per_day = int(config["max-symbols-per-day"])

    for trade_date, group in panel.groupby("trade_date", sort=True):
        working = group.copy()
        working["close"] = _numeric(working["close"])
        working["listed_days"] = _numeric(working["listed_days"])
        working["liquidity_metric"] = _numeric(working.get("liquidity_20")).fillna(_numeric(working.get("liquidity_5")))
        working["momentum_medium"] = _numeric(working.get("momentum_60")).fillna(_numeric(working.get("momentum_20")))
        working["momentum_long"] = _numeric(working.get("momentum_120")).fillna(_numeric(working.get("momentum_60"))).fillna(_numeric(working.get("momentum_20")))
        working["trend_distance_medium"] = _numeric(working.get("trend_distance_60")).fillna(_numeric(working.get("trend_distance_20")))
        working["volatility_metric"] = _numeric(working.get("volatility_20")).fillna(_numeric(working.get("volatility_10")) / 100.0)
        working["tracking_error_metric"] = _numeric(working.get("tracking_error_20")).fillna(_numeric(working.get("tracking_error_10")) / 100.0)
        working = working[
            (working["close"] >= min_price)
            & (working["listed_days"].fillna(10_000) >= min_listed_days)
            & working["liquidity_metric"].notna()
            & working["momentum_20"].notna()
            & working["volatility_metric"].notna()
        ].copy()
        if working.empty:
            continue

        liquidity = _numeric(working["liquidity_metric"])
        if liquidity_quantile > 0:
            cutoff = float(liquidity.quantile(min(max(liquidity_quantile, 0.0), 1.0)))
            working = working[liquidity >= cutoff].copy()
        if working.empty:
            continue
        if max_symbols_per_day > 0 and len(working.index) > max_symbols_per_day:
            working = working.sort_values("liquidity_metric", ascending=False).head(max_symbols_per_day).copy()

        working["momentum_consistency"] = (
            (_numeric(working["momentum_20"]) > 0).astype(int)
            + (_numeric(working["momentum_medium"]) > 0).astype(int)
            + (_numeric(working["momentum_long"]) > 0).astype(int)
        ) / 3.0
        components = {
            "momentum_long": 0.24,
            "momentum_medium": 0.20,
            "momentum_20": 0.14,
            "index_momentum_60": 0.10,
            "index_momentum_20": 0.06,
            "trend_distance_medium": 0.09,
            "trend_distance_20": 0.05,
            "liquidity_metric": 0.08,
            "liquidity_5": 0.04,
            "size_change_5": 0.04,
            "momentum_consistency": 0.06,
            "close_location": 0.02,
            "drawdown_60": 0.05,
            "broad_theme_score": 0.04,
            "volatility_metric": -0.08,
            "volatility_10": -0.04,
            "tracking_error_metric": -0.04,
            "tracking_error_10": -0.02,
            "gap_abs": -0.02,
            "premium_abs": -0.02,
            "mgt_fee": -0.02,
        }
        score = pd.Series(0.0, index=working.index)
        for field, weight in components.items():
            if field not in working.columns:
                continue
            score = score + _safe_zscore(working[field]).fillna(0.0) * float(weight)
        working["symbol_score"] = score
        working["positive_momentum"] = (
            (_numeric(working["momentum_20"]) > 0).astype(int)
            + (_numeric(working["momentum_medium"]) > 0).astype(int)
            + (_numeric(working["momentum_long"]) > 0).astype(int)
        ) / 3.0
        frames.append(working)

    return pd.concat(frames, ignore_index=True) if frames else empty_like


def classify_regime(benchmark_row: pd.Series | None, config: dict) -> tuple[str, float]:
    if benchmark_row is None or benchmark_row.empty:
        return "normal", float(config["target-portfolio-exposure"])
    momentum = safe_float(benchmark_row.get("benchmark_momentum_20"), 0.0)
    volatility = safe_float(benchmark_row.get("benchmark_volatility_20"), 0.0)
    target = float(config["target-portfolio-exposure"])
    if momentum <= float(config["defensive-benchmark-momentum-threshold"]) and volatility >= float(config["defensive-benchmark-volatility-threshold"]):
        return "defensive", target * float(config["defensive-exposure-scale"])
    if momentum <= float(config["caution-benchmark-momentum-threshold"]) and volatility >= float(config["caution-benchmark-volatility-threshold"]):
        return "caution", target * float(config["caution-exposure-scale"])
    return "normal", target


def build_sleeve_allocations(group: pd.DataFrame, config: dict, risk_label: str = "normal") -> tuple[pd.DataFrame, dict[str, float]]:
    sleeve_rows = []
    min_sleeve_symbols = int(config["min-sleeve-symbols"])
    layer1_top_k = int(config["layer1-top-k-per-sleeve"])

    for sleeve, sleeve_frame in group.groupby("trading_mode", sort=True):
        if len(sleeve_frame.index) < min_sleeve_symbols:
            continue
        ordered = sleeve_frame.sort_values("symbol_score", ascending=False).copy()
        top = ordered.head(layer1_top_k)
        sleeve_rows.append(
            {
                "trading_mode": sleeve,
                "sleeve_symbol_count": int(len(ordered.index)),
                "sleeve_strength": float(_numeric(top["symbol_score"]).mean()),
                "sleeve_momentum": float(_numeric(top["momentum_20"]).mean()),
                "sleeve_breadth": float(_numeric(top["positive_momentum"]).mean()),
                "sleeve_liquidity": float(_numeric(top["liquidity_5"]).mean()),
                "sleeve_volatility": float(_numeric(top["volatility_10"]).mean()),
                "sleeve_premium_abs": float(_numeric(top["premium_abs"]).mean()),
            }
        )

    sleeves = pd.DataFrame(sleeve_rows)
    if sleeves.empty:
        return sleeves, {}

    sleeves["sleeve_score"] = (
        _safe_zscore(sleeves["sleeve_strength"]) * 0.45
        + _safe_zscore(sleeves["sleeve_momentum"]) * 0.20
        + _safe_zscore(sleeves["sleeve_breadth"]) * 0.15
        + _safe_zscore(sleeves["sleeve_liquidity"]) * 0.10
        - _safe_zscore(sleeves["sleeve_volatility"]) * 0.07
        - _safe_zscore(sleeves["sleeve_premium_abs"]) * 0.03
    )
    if risk_label == "normal":
        sleeves["sleeve_score"] = sleeves["sleeve_score"] + sleeves["trading_mode"].map({
            "T1": float(config.get("normal-t1-bias", 0.0) or 0.0),
            "T0": -float(config.get("normal-t1-bias", 0.0) or 0.0) * 0.35,
        }).fillna(0.0)
    elif risk_label == "caution":
        sleeves["sleeve_score"] = sleeves["sleeve_score"] + sleeves["trading_mode"].map({
            "T0": float(config.get("caution-t0-bias", 0.0) or 0.0),
            "T1": -float(config.get("caution-t0-bias", 0.0) or 0.0) * 0.40,
        }).fillna(0.0)
    else:
        sleeves["sleeve_score"] = sleeves["sleeve_score"] + sleeves["trading_mode"].map({
            "T0": float(config.get("defensive-t0-bias", 0.0) or 0.0),
            "T1": -float(config.get("defensive-t0-bias", 0.0) or 0.0) * 0.60,
        }).fillna(0.0)

    raw_weights = {
        str(row["trading_mode"]): max(0.0, float(row["sleeve_score"]) + float(config["sleeve-score-shift"]))
        for _, row in sleeves.iterrows()
    }
    if sum(raw_weights.values()) <= 0:
        best_row = sleeves.sort_values("sleeve_score", ascending=False).iloc[0]
        best_mode = str(best_row["trading_mode"])
        if float(best_row["sleeve_score"]) >= float(config["fallback-single-sleeve-score"]):
            raw_weights = {best_mode: 1.0}
        else:
            raw_weights = {}

    total = sum(raw_weights.values())
    normalized = {key: value / total for key, value in raw_weights.items()} if total > 0 else {}
    return sleeves, normalized


def estimate_buy_cost(symbol: str, trade_value: float, config: dict) -> float:
    commission = max(trade_value * float(config["commission-rate"]), float(config["minimum-commission"]))
    transfer_fee = trade_value * float(config["transfer-fee-rate"]) if symbol.endswith(".SH") else 0.0
    slippage = trade_value * float(config["rebalance-slippage-rate"])
    return commission + transfer_fee + slippage


def estimate_sell_cost(symbol: str, trade_value: float, config: dict) -> float:
    commission = max(trade_value * float(config["commission-rate"]), float(config["minimum-commission"]))
    transfer_fee = trade_value * float(config["transfer-fee-rate"]) if symbol.endswith(".SH") else 0.0
    slippage = trade_value * float(config["rebalance-slippage-rate"])
    return commission + transfer_fee + slippage


def rebalance_state(
    cash: float,
    positions: dict[str, float],
    target_weights: dict[str, float],
    snapshot: pd.DataFrame,
    config: dict,
) -> tuple[float, dict[str, float], dict]:
    equity = cash + sum(positions.values())
    if equity <= 0.0:
        return cash, positions, {"turnover": 0.0, "cost": 0.0}

    current = dict(positions)
    target_values = {symbol: equity * float(weight) * float(config["target-weight-buffer"]) for symbol, weight in target_weights.items()}
    turnover = 0.0
    cost = 0.0

    for symbol in sorted(set(current) | set(target_values)):
        current_value = current.get(symbol, 0.0)
        target_value = target_values.get(symbol, 0.0)
        if target_value < current_value:
            sell_value = current_value - target_value
            cash += sell_value
            current[symbol] = target_value
            turnover += sell_value
            cost += estimate_sell_cost(symbol, sell_value, config)

    for symbol in list(current.keys()):
        if current[symbol] <= 1e-8:
            current.pop(symbol, None)

    buy_requests = []
    for symbol, target_value in target_values.items():
        current_value = current.get(symbol, 0.0)
        if target_value > current_value:
            buy_requests.append((symbol, target_value - current_value))
    total_buy_request = sum(value for _, value in buy_requests)
    available_for_buys = max(cash, 0.0)
    if total_buy_request > 0 and available_for_buys > 0:
        scale = min(1.0, available_for_buys / total_buy_request)
        for symbol, requested in buy_requests:
            trade_value = requested * scale
            if trade_value <= 0:
                continue
            current[symbol] = current.get(symbol, 0.0) + trade_value
            cash -= trade_value
            turnover += trade_value
            cost += estimate_buy_cost(symbol, trade_value, config)

    cash = max(cash - cost, 0.0)
    return cash, current, {
        "turnover": turnover / equity if equity > 0 else 0.0,
        "cost": cost / equity if equity > 0 else 0.0,
    }


def _tail_realized_returns(realized_returns: list[float], lookback: int) -> pd.Series:
    values = realized_returns[-int(lookback):] if int(lookback or 0) > 0 else realized_returns
    return pd.Series([float(value) for value in values], dtype=float)


def _portfolio_vol_target_scale(realized_returns: list[float], config: dict) -> float:
    if not safe_bool(config.get("portfolio-vol-target-enabled"), False):
        return 1.0
    trailing = _tail_realized_returns(realized_returns, int(config.get("portfolio-vol-target-lookback", 20)))
    if len(trailing) < max(1, int(config.get("portfolio-vol-target-min-observations", 10))):
        return 1.0
    std = float(trailing.std(ddof=0))
    if not math.isfinite(std) or std <= 0.0:
        return 1.0
    raw_scale = float(config.get("portfolio-vol-target-daily-vol", 0.011)) / std
    return _clamp(raw_scale, float(config.get("portfolio-vol-target-floor-scale", 0.45)), float(config.get("portfolio-vol-target-cap-scale", 1.0)))


def _portfolio_quarter_kelly_scale(realized_returns: list[float], risk_label: str, config: dict) -> float:
    if not safe_bool(config.get("portfolio-quarter-kelly-enabled"), False):
        return 1.0
    trailing = _tail_realized_returns(realized_returns, int(config.get("portfolio-quarter-kelly-lookback", 30)))
    if len(trailing) < max(1, int(config.get("portfolio-quarter-kelly-min-observations", 12))):
        return 1.0
    mean = float(trailing.mean())
    variance = float(trailing.var(ddof=0))
    if not math.isfinite(mean) or not math.isfinite(variance):
        return 1.0
    if variance <= 0:
        return float(config.get("portfolio-quarter-kelly-floor-scale", 0.20)) if mean <= 0 else 1.0
    raw_scale = max(0.0, float(config.get("portfolio-quarter-kelly-fraction", 0.20)) * mean / variance)
    regime_multiplier = {
        "caution": float(config.get("portfolio-quarter-kelly-medium-regime-multiplier", 0.75)),
        "defensive": float(config.get("portfolio-quarter-kelly-high-regime-multiplier", 0.50)),
    }.get(str(risk_label or "normal"), 1.0)
    return _clamp(
        raw_scale * regime_multiplier,
        float(config.get("portfolio-quarter-kelly-floor-scale", 0.20)),
        float(config.get("portfolio-quarter-kelly-cap-scale", 0.85)),
    )


def _get_regime_exposure(regime_score: float, config: dict) -> float:
    floor = _clamp(float(config.get("min-regime-exposure", 0.00)), 0.0, float(config.get("risk-off-exposure", 0.25)))
    risk_off = float(config.get("risk-off-exposure", 0.25))
    risk_on = float(config.get("target-portfolio-exposure", 0.95))
    if regime_score >= 0.25:
        normalized = _clamp((regime_score - 0.25) / 0.55, 0.0, 1.0)
        return _lerp(risk_off, risk_on, normalized)
    defensive_normalized = _clamp((regime_score + 0.65) / 0.90, 0.0, 1.0)
    return _lerp(floor, risk_off, defensive_normalized)


def _get_strategy_drawdown_cap(strategy_drawdown: float) -> float:
    if strategy_drawdown <= -0.18:
        return 0.45
    if strategy_drawdown <= -0.12:
        return 0.60
    if strategy_drawdown <= -0.08:
        return 0.75
    return 1.0


def _get_benchmark_stress_cap(benchmark_row: pd.Series | None) -> float:
    if benchmark_row is None or benchmark_row.empty:
        return 1.0
    drawdown = safe_float(benchmark_row.get("benchmark_drawdown_252"), 0.0)
    volatility = safe_float(benchmark_row.get("benchmark_volatility_20"), 0.025)
    if drawdown <= -0.20 or volatility >= 0.045 * 100.0:
        return 0.45
    if drawdown <= -0.12 or volatility >= 0.035 * 100.0:
        return 0.65
    if drawdown <= -0.08 or volatility >= 0.028 * 100.0:
        return 0.80
    return 1.0


def _apply_exposure_path_control(previous_exposure: float, target_exposure: float, config: dict) -> float:
    previous = max(0.0, float(previous_exposure))
    target = max(0.0, float(target_exposure))
    if target >= previous:
        return min(target, previous + float(config.get("max-exposure-step-up", 0.15)))
    return max(target, previous - float(config.get("max-exposure-step-down", 0.35)))


def _evaluate_risk_state(
    benchmark_row: pd.Series | None,
    strategy_drawdown: float,
    last_target_exposure: float,
    realized_returns: list[float],
    recovery_mode: bool,
    config: dict,
) -> dict:
    base_label, base_target = classify_regime(benchmark_row, config)
    if benchmark_row is None or benchmark_row.empty:
        return {
            "risk_state": base_label,
            "target_exposure": float(base_target),
            "recovery_mode": recovery_mode,
            "regime_score": 0.0,
            "vol_target_scale": 1.0,
            "quarter_kelly_scale": 1.0,
        }

    close = safe_float(benchmark_row.get("close"), 0.0)
    regime_score = (
        _score_trend(close, safe_float(benchmark_row.get("benchmark_ma20"), 0.0), 0.12)
        + _score_trend(close, safe_float(benchmark_row.get("benchmark_ma60"), 0.0), 0.18)
        + _score_trend(close, safe_float(benchmark_row.get("benchmark_ma120"), 0.0), 0.24)
        + _score_centered(safe_float(benchmark_row.get("benchmark_momentum_20"), 0.0), 0.00, 0.08, 0.12)
        + _score_centered(safe_float(benchmark_row.get("benchmark_momentum_60"), 0.0), -0.02, 0.12, 0.18)
        + _score_centered(0.025 - safe_float(benchmark_row.get("benchmark_volatility_20"), 0.025) / 100.0, 0.0, 0.02, 0.18)
        + _score_centered(safe_float(benchmark_row.get("benchmark_drawdown_252"), 0.0), -0.08, 0.12, 0.20)
    )
    raw_target = min(float(base_target), _get_regime_exposure(regime_score, config))
    risk_cap = min(_get_strategy_drawdown_cap(strategy_drawdown), _get_benchmark_stress_cap(benchmark_row)) * float(config.get("target-portfolio-exposure", 0.95))
    if not recovery_mode and strategy_drawdown <= float(config.get("recovery-drawdown-enter", -0.12)):
        recovery_mode = True
    elif recovery_mode and strategy_drawdown >= float(config.get("recovery-drawdown-exit", -0.04)) and regime_score >= 0.20:
        recovery_mode = False

    vol_target_scale = _portfolio_vol_target_scale(realized_returns, config)
    preliminary_label = "normal" if raw_target >= 0.80 * float(config.get("target-portfolio-exposure", 0.95)) else ("caution" if raw_target >= 0.45 else "defensive")
    quarter_kelly_scale = _portfolio_quarter_kelly_scale(realized_returns, preliminary_label, config)
    capped_exposure = min(raw_target * vol_target_scale * quarter_kelly_scale, risk_cap)
    if recovery_mode:
        capped_exposure = min(capped_exposure, float(config.get("recovery-exposure-cap", 0.65)))
    target_exposure = _apply_exposure_path_control(last_target_exposure, capped_exposure, config)
    risk_state = "normal" if target_exposure >= 0.80 * float(config.get("target-portfolio-exposure", 0.95)) else ("caution" if target_exposure >= 0.45 else "defensive")
    return {
        "risk_state": risk_state,
        "target_exposure": target_exposure,
        "recovery_mode": recovery_mode,
        "regime_score": regime_score,
        "vol_target_scale": vol_target_scale,
        "quarter_kelly_scale": quarter_kelly_scale,
    }


def _current_position_weights(positions: dict[str, float], equity: float) -> dict[str, float]:
    if equity <= 0.0 or not positions:
        return {}
    return {
        symbol: value / equity
        for symbol, value in positions.items()
        if value > 1e-8
    }


def _normalize_target_weights(target_weights: dict[str, float], target_exposure: float, max_single_weight: float) -> dict[str, float]:
    cleaned = {str(symbol): max(0.0, float(weight)) for symbol, weight in target_weights.items() if float(weight) > 1e-8}
    if not cleaned or target_exposure <= 0.0:
        return {}
    total = sum(cleaned.values())
    scaled = {symbol: value / total * float(target_exposure) for symbol, value in cleaned.items()}
    cap = max(0.0, float(max_single_weight))
    if cap > 0.0:
        for _ in range(len(scaled) + 2):
            over = {symbol: weight for symbol, weight in scaled.items() if weight > cap + 1e-10}
            if not over:
                break
            excess = sum(weight - cap for weight in over.values())
            for symbol in over:
                scaled[symbol] = cap
            under = {symbol: weight for symbol, weight in scaled.items() if weight < cap - 1e-10}
            under_total = sum(under.values())
            if excess <= 1e-10 or under_total <= 1e-10:
                break
            for symbol, weight in under.items():
                room = cap - weight
                increment = min(room, excess * (weight / under_total))
                scaled[symbol] += increment
    return {symbol: weight for symbol, weight in scaled.items() if weight > 1e-6}


def _weight_turnover(current_weights: dict[str, float], target_weights: dict[str, float]) -> float:
    symbols = set(current_weights) | set(target_weights)
    return 0.5 * sum(abs(float(current_weights.get(symbol, 0.0)) - float(target_weights.get(symbol, 0.0))) for symbol in symbols)


def _build_holding_details_from_weights(weights: dict[str, float], group: pd.DataFrame, selected_sleeves: list[str], risk_state: dict) -> list[dict]:
    if not weights:
        return []
    lookup = group.sort_values("symbol_score", ascending=False).drop_duplicates("ts_code").set_index("ts_code", drop=False) if not group.empty else pd.DataFrame()
    details = []
    for symbol, target_weight in sorted(weights.items(), key=lambda item: item[1], reverse=True):
        if not lookup.empty and symbol in lookup.index:
            row = lookup.loc[symbol]
            details.append(
                {
                    "symbol": symbol,
                    "trading_mode": str(row.get("trading_mode") or ""),
                    "target_weight": float(target_weight),
                    "sleeve_weight": float(target_weight),
                    "sleeve_score": float(row.get("symbol_score") or 0.0),
                    "symbol_score": float(row.get("symbol_score") or 0.0),
                }
            )
        else:
            details.append(
                {
                    "symbol": symbol,
                    "trading_mode": selected_sleeves[0] if selected_sleeves else "",
                    "target_weight": float(target_weight),
                    "sleeve_weight": float(target_weight),
                    "sleeve_score": 0.0,
                    "symbol_score": 0.0,
                }
            )
    return details


def _theme_selection_bonus(frame: pd.DataFrame, risk_label: str, config: dict) -> pd.Series:
    broad = _numeric(frame.get("broad_theme_score", 0.0)).fillna(0.0)
    defensive = _numeric(frame.get("defensive_theme_score", 0.0)).fillna(0.0)
    growth = _numeric(frame.get("growth_theme_score", 0.0)).fillna(0.0)
    if risk_label == "normal":
        return broad * float(config.get("normal-broad-theme-bonus", 0.10)) - defensive * float(config.get("normal-defensive-theme-penalty", 0.04))
    if risk_label == "caution":
        return defensive * float(config.get("caution-defensive-theme-bonus", 0.08)) - growth * float(config.get("caution-growth-theme-penalty", 0.06))
    return defensive * float(config.get("defensive-defensive-theme-bonus", 0.22)) - growth * float(config.get("defensive-growth-theme-penalty", 0.12))


def _filter_regime_candidates(sleeve_frame: pd.DataFrame, risk_label: str, config: dict) -> pd.DataFrame:
    if sleeve_frame.empty:
        return sleeve_frame

    working = sleeve_frame.copy()
    momentum20 = _numeric(working.get("momentum_20"))
    momentum60 = _numeric(working.get("momentum_medium")).fillna(_numeric(working.get("momentum_60"))).fillna(momentum20)
    trend60 = _numeric(working.get("trend_distance_medium")).fillna(_numeric(working.get("trend_distance_60"))).fillna(_numeric(working.get("trend_distance_20")))
    drawdown60 = _numeric(working.get("drawdown_60"))
    broad = _numeric(working.get("broad_theme_score", 0.0)).fillna(0.0)
    defensive = _numeric(working.get("defensive_theme_score", 0.0)).fillna(0.0)

    min_momentum20 = float(config.get("symbol-min-momentum20", -0.02))
    min_momentum60 = float(config.get("symbol-min-momentum60", -0.04))
    min_trend60 = float(config.get("symbol-min-trend-distance60", -0.05))
    max_drawdown60 = float(config.get("symbol-max-drawdown60", -0.18))

    if risk_label == "normal":
        filtered = working[
            (momentum20 >= min_momentum20)
            & (momentum60 >= min_momentum60)
            & (trend60 >= min_trend60)
            & (drawdown60.fillna(0.0) >= max_drawdown60)
        ].copy()
    elif risk_label == "caution":
        filtered = working[
            ((momentum20 >= min_momentum20 - 0.03) & (trend60 >= min_trend60 - 0.03))
            | (defensive > 0)
            | (broad > 0)
        ].copy()
        filtered_drawdown = _numeric(filtered.get("drawdown_60")).fillna(0.0)
        filtered = filtered[filtered_drawdown >= max_drawdown60 - 0.05].copy()
    else:
        filtered = working[
            ((defensive > 0) | (broad > 0))
            & (momentum20 >= min_momentum20 - 0.01)
            & (drawdown60.fillna(0.0) >= max_drawdown60 - 0.02)
        ].copy()

    if not filtered.empty:
        return filtered

    if risk_label == "normal":
        relaxed = working[
            (momentum20 >= min_momentum20 - 0.05)
            & (momentum60 >= min_momentum60 - 0.04)
            & (drawdown60.fillna(0.0) >= max_drawdown60 - 0.08)
        ].copy()
        if not relaxed.empty:
            return relaxed
    elif risk_label == "caution":
        relaxed = working[
            (momentum20 >= min_momentum20 - 0.08)
            & (drawdown60.fillna(0.0) >= max_drawdown60 - 0.08)
        ].copy()
        if not relaxed.empty:
            return relaxed

    fallback = working.sort_values(["symbol_score", "liquidity_metric"], ascending=False).head(max(1, int(config.get("layer1-top-k-per-sleeve", 8) or 8)))
    return fallback.copy()


def _build_candidate_target(
    group: pd.DataFrame,
    current_weights: dict[str, float],
    risk_state: dict,
    config: dict,
) -> dict:
    current_symbols = set(current_weights.keys())
    sleeves, sleeve_weights = build_sleeve_allocations(group, config, risk_label=str(risk_state["risk_state"]))
    if not sleeve_weights or float(risk_state["target_exposure"]) <= 0.0:
        return {"target_weights": {}, "holding_details": [], "selected_sleeves": []}

    persistence_bonus = float(config.get("symbol-persistence-bonus", 0.18) or 0.0)
    rank_decay = max(0.1, float(config.get("symbol-rank-decay", 0.75) or 0.75))
    raw_weights: dict[str, float] = {}
    details: dict[str, dict] = {}
    selected_sleeves: list[str] = []
    for sleeve, sleeve_share in sleeve_weights.items():
        sleeve_frame = group[group["trading_mode"] == sleeve].copy()
        if sleeve_frame.empty:
            continue
        filtered = _filter_regime_candidates(sleeve_frame, str(risk_state["risk_state"]), config)
        filtered["selection_score"] = (
            _numeric(filtered["symbol_score"]).fillna(0.0)
            + filtered["ts_code"].isin(current_symbols).astype(float) * persistence_bonus
            + _theme_selection_bonus(filtered, str(risk_state["risk_state"]), config)
        )
        top_k = int(config["t0-top-k"] if sleeve == "T0" else config["t1-top-k"])
        candidates = filtered.sort_values(["selection_score", "symbol_score"], ascending=False).head(max(1, top_k)).copy()
        if candidates.empty:
            continue
        selected_sleeves.append(str(sleeve))
        sleeve_weight = float(sleeve_share) * float(risk_state["target_exposure"])
        sleeve_score = float(sleeves.loc[sleeves["trading_mode"] == sleeve, "sleeve_score"].iloc[0])
        rank_weights = [rank_decay ** index for index in range(len(candidates.index))]
        rank_sum = sum(rank_weights)
        for rank_index, (_, row) in enumerate(candidates.iterrows()):
            symbol = str(row["ts_code"])
            target_weight = sleeve_weight * rank_weights[rank_index] / rank_sum if rank_sum > 0 else 0.0
            raw_weights[symbol] = raw_weights.get(symbol, 0.0) + target_weight
            details[symbol] = {
                "symbol": symbol,
                "trading_mode": str(row.get("trading_mode") or sleeve),
                "target_weight": target_weight,
                "sleeve_weight": sleeve_weight,
                "sleeve_score": sleeve_score,
                "symbol_score": float(row.get("symbol_score") or 0.0),
            }

    normalized = _normalize_target_weights(raw_weights, float(risk_state["target_exposure"]), float(config.get("max-single-weight", 0.22)))
    holding_details = []
    for symbol, target_weight in sorted(normalized.items(), key=lambda item: item[1], reverse=True):
        detail = dict(details.get(symbol, {"symbol": symbol, "trading_mode": "", "sleeve_weight": target_weight, "sleeve_score": 0.0, "symbol_score": 0.0}))
        detail["target_weight"] = float(target_weight)
        holding_details.append(detail)
    return {
        "target_weights": normalized,
        "holding_details": holding_details,
        "selected_sleeves": selected_sleeves,
    }


def plan_variant(
    variant: str,
    scored: pd.DataFrame,
    benchmark: pd.DataFrame,
    live_trade_date: str | None,
    config: dict,
) -> tuple[dict[str, dict], pd.DataFrame, pd.DataFrame, dict]:
    specs_by_date: dict[str, dict] = {}
    pending_specs: dict[str, dict] = {}
    positions: dict[str, float] = {}
    cash = float(config["initial-capital"])
    previous_equity = float(config["initial-capital"])
    peak_equity = float(config["initial-capital"])
    realized_returns: list[float] = []
    daily_rows = []
    rebalance_rows = []
    scored_by_date = {}
    if not scored.empty and "trade_date" in scored.columns:
        scored_by_date = {trade_date: group.copy().reset_index(drop=True) for trade_date, group in scored.groupby("trade_date", sort=True)}
    panel_by_date = scored_by_date
    benchmark_nav = (1.0 + benchmark["benchmark_return"].fillna(0.0)).cumprod()
    benchmark_nav_lookup = dict(zip(benchmark["trade_date"].tolist(), benchmark_nav.tolist()))
    benchmark_by_date = benchmark.set_index("trade_date", drop=False)
    trading_dates = benchmark["trade_date"].astype(str).tolist()
    next_trade_date = {trading_dates[index]: trading_dates[index + 1] for index in range(len(trading_dates) - 1)}
    live_trade_date = str(live_trade_date or "").zfill(8) if live_trade_date else None
    recovery_mode = False
    last_target_exposure = float(config.get("target-portfolio-exposure", 0.95))
    last_risk_state = "normal"
    last_rebalance_index = -10_000

    for index, trade_date in enumerate(trading_dates):
        snapshot = panel_by_date.get(trade_date)
        if snapshot is not None and positions:
            return_lookup = snapshot.set_index("ts_code")["return_1d"].to_dict()
            for symbol in list(positions.keys()):
                positions[symbol] *= 1.0 + float(return_lookup.get(symbol, 0.0) or 0.0)

        executed_spec = pending_specs.get(trade_date)
        trade_meta = {"turnover": 0.0, "cost": 0.0}
        if executed_spec is not None:
            cash, positions, trade_meta = rebalance_state(
                cash,
                positions,
                executed_spec["target_weights"],
                snapshot if snapshot is not None else pd.DataFrame(columns=scored.columns),
                config,
            )
            last_target_exposure = float(executed_spec["target_exposure"])
            last_risk_state = str(executed_spec["risk_state"])
            last_rebalance_index = index
            rebalance_rows.append(
                {
                    "variant": variant,
                    "signal_date": executed_spec["signal_date"],
                    "execution_date": trade_date,
                    "plan_timestamp": executed_spec["plan_timestamp"],
                    "regime": executed_spec["regime"],
                    "risk_state": executed_spec["risk_state"],
                    "regime_score": executed_spec["regime_score"],
                    "recovery_mode": int(executed_spec["recovery_mode"]),
                    "target_exposure": executed_spec["target_exposure"],
                    "vol_target_scale": executed_spec["vol_target_scale"],
                    "quarter_kelly_scale": executed_spec["quarter_kelly_scale"],
                    "holding_count": len(executed_spec["target_weights"]),
                    "turnover": trade_meta["turnover"],
                    "cost": trade_meta["cost"],
                    "decision_reason": executed_spec["decision_reason"],
                    "selected_sleeves": ";".join(executed_spec["selected_sleeves"]),
                    "selected_symbols": ";".join(sorted(executed_spec["target_weights"].keys())),
                }
            )

        equity = cash + sum(positions.values())
        daily_return = equity / previous_equity - 1.0 if previous_equity > 0 else 0.0
        previous_equity = equity
        peak_equity = max(peak_equity, equity)
        strategy_drawdown = equity / peak_equity - 1.0 if peak_equity > 0 else 0.0
        realized_returns.append(daily_return)
        current_weights = _current_position_weights(positions, equity)
        daily_rows.append(
            {
                "variant": variant,
                "trade_date": trade_date,
                "equity": equity,
                "nav": equity / float(config["initial-capital"]) if float(config["initial-capital"]) > 0 else 0.0,
                "cash": cash,
                "holdings_count": len(current_weights),
                "current_exposure": float(sum(current_weights.values())),
                "max_single_weight": max(current_weights.values()) if current_weights else 0.0,
                "benchmark_nav": float(benchmark_nav_lookup.get(trade_date, 1.0)),
                "daily_return": daily_return,
                "strategy_drawdown": strategy_drawdown,
            }
        )

        execution_date = next_trade_date.get(trade_date)
        if live_trade_date and trade_date == live_trade_date:
            execution_date = trade_date
        if not execution_date or trade_date not in scored_by_date:
            continue

        group = scored_by_date[trade_date]
        benchmark_row = benchmark_by_date.loc[trade_date] if trade_date in benchmark_by_date.index else None
        risk_state = _evaluate_risk_state(
            benchmark_row=benchmark_row,
            strategy_drawdown=strategy_drawdown,
            last_target_exposure=last_target_exposure,
            realized_returns=realized_returns,
            recovery_mode=recovery_mode,
            config=config,
        )
        recovery_mode = bool(risk_state["recovery_mode"])
        candidate = _build_candidate_target(group, current_weights, risk_state, config)
        current_scaled = _normalize_target_weights(current_weights, float(risk_state["target_exposure"]), float(config.get("max-single-weight", 0.22)))
        target_weights = dict(candidate["target_weights"])
        if not target_weights and current_scaled:
            target_weights = dict(current_scaled)
            candidate["holding_details"] = _build_holding_details_from_weights(current_scaled, group, candidate.get("selected_sleeves", []), risk_state)

        candidate_turnover = _weight_turnover(current_scaled, target_weights)
        exposure_change = abs(sum(target_weights.values()) - sum(current_scaled.values()))
        days_since_rebalance = index - last_rebalance_index
        should_refresh_symbols = (
            not current_scaled
            or days_since_rebalance >= int(config.get("rebalance-interval-days", 5))
            or str(risk_state["risk_state"]) != str(last_risk_state)
            or candidate_turnover >= float(config.get("rebalance-turnover-threshold", 0.18))
            or exposure_change >= float(config.get("rebalance-exposure-change-threshold", 0.10))
        )

        decision_reason = "rebalance"
        selected_sleeves = list(candidate.get("selected_sleeves", []))
        holding_details = list(candidate.get("holding_details", []))
        if not should_refresh_symbols and current_scaled:
            target_weights = dict(current_scaled)
            selected_sleeves = sorted({
                str(row.get("trading_mode") or "")
                for _, row in group[group["ts_code"].isin(target_weights.keys())].iterrows()
                if str(row.get("trading_mode") or "")
            })
            holding_details = _build_holding_details_from_weights(target_weights, group, selected_sleeves, risk_state)
            decision_reason = "carry_forward"
        elif not target_weights and current_scaled:
            target_weights = dict(current_scaled)
            selected_sleeves = sorted({
                str(row.get("trading_mode") or "")
                for _, row in group[group["ts_code"].isin(target_weights.keys())].iterrows()
                if str(row.get("trading_mode") or "")
            })
            holding_details = _build_holding_details_from_weights(target_weights, group, selected_sleeves, risk_state)
            decision_reason = "risk_scale"

        if not target_weights:
            continue

        plan_timestamp = datetime.now().astimezone().isoformat() if execution_date == trade_date else f"{trade_date}T15:00:00"
        spec = {
            "variant": variant,
            "signal_date": str(trade_date),
            "execution_date": str(execution_date),
            "plan_timestamp": plan_timestamp,
            "regime": str(risk_state["risk_state"]),
            "risk_state": str(risk_state["risk_state"]),
            "regime_score": float(risk_state["regime_score"]),
            "target_exposure": float(sum(target_weights.values())),
            "vol_target_scale": float(risk_state["vol_target_scale"]),
            "quarter_kelly_scale": float(risk_state["quarter_kelly_scale"]),
            "recovery_mode": bool(risk_state["recovery_mode"]),
            "selected_sleeves": selected_sleeves,
            "target_weights": target_weights,
            "holding_details": holding_details,
            "decision_reason": decision_reason,
        }
        specs_by_date[str(execution_date)] = spec
        pending_specs[str(execution_date)] = spec

    daily = pd.DataFrame(daily_rows)
    rebalances = pd.DataFrame(rebalance_rows)
    if daily.empty:
        return {}, daily, rebalances, {}

    daily["drawdown"] = daily["nav"] / daily["nav"].cummax() - 1.0
    daily["benchmark_drawdown"] = daily["benchmark_nav"] / daily["benchmark_nav"].cummax() - 1.0
    total_return = float(daily["nav"].iloc[-1] - 1.0)
    benchmark_total_return = float(daily["benchmark_nav"].iloc[-1] - 1.0)
    summary = {
        "variant": variant,
        "start_date": str(daily["trade_date"].iloc[0]),
        "end_date": str(daily["trade_date"].iloc[-1]),
        "trade_days": int(len(daily.index)),
        "rebalance_count": int(len(rebalances.index)),
        "total_return": total_return,
        "benchmark_total_return": benchmark_total_return,
        "excess_return": total_return - benchmark_total_return,
        "annualized_return": annualized_return(total_return, int(len(daily.index))),
        "benchmark_annualized_return": annualized_return(benchmark_total_return, int(len(daily.index))),
        "max_drawdown": compute_max_drawdown(daily["nav"]),
        "benchmark_max_drawdown": compute_max_drawdown(daily["benchmark_nav"]),
        "sharpe_ratio": sharpe_ratio(daily["daily_return"]),
        "average_turnover": float(rebalances["turnover"].mean()) if not rebalances.empty else 0.0,
        "average_holdings_count": float(daily["holdings_count"].mean()),
        "average_max_single_weight": float(daily["max_single_weight"].mean()),
        "average_exposure": float(daily["current_exposure"].mean()),
    }
    return specs_by_date, daily, rebalances, summary


def export_plan_files(config: dict, specs_by_variant: dict[str, dict], benchmark: pd.DataFrame) -> dict[str, str]:
    plan_root = Path(config["plan-directory"])
    plan_root.mkdir(parents=True, exist_ok=True)
    plan_paths = {}
    for variant, plans in specs_by_variant.items():
        rows = []
        for execution_date, spec in sorted(plans.items()):
            for detail in spec["holding_details"]:
                rows.append(
                    {
                        "variant": variant,
                        "signal_date": spec["signal_date"],
                        "execution_date": execution_date,
                        "plan_timestamp": spec["plan_timestamp"],
                        "symbol": detail["symbol"],
                        "target_weight": detail["target_weight"],
                        "trading_mode": detail["trading_mode"],
                        "sleeve": detail["trading_mode"],
                        "sleeve_weight": detail["sleeve_weight"],
                        "sleeve_score": detail["sleeve_score"],
                        "symbol_score": detail["symbol_score"],
                        "regime": spec["regime"],
                        "target_exposure": spec["target_exposure"],
                        "selected_sleeves": ";".join(spec["selected_sleeves"]),
                        "risk_state": spec.get("risk_state", spec["regime"]),
                        "regime_score": spec.get("regime_score", 0.0),
                        "vol_target_scale": spec.get("vol_target_scale", 1.0),
                        "quarter_kelly_scale": spec.get("quarter_kelly_scale", 1.0),
                        "recovery_mode": int(bool(spec.get("recovery_mode", False))),
                        "decision_reason": spec.get("decision_reason", "rebalance"),
                    }
                )
        frame = pd.DataFrame(rows)
        path = plan_root / f"{variant}.csv"
        frame.to_csv(path, index=False, float_format="%.10f")
        plan_paths[variant] = str(path)

    benchmark_path = Path(config["benchmark-file"])
    benchmark_path.parent.mkdir(parents=True, exist_ok=True)
    benchmark[["trade_date", "close", "pct_chg", "benchmark_return"]].to_csv(
        benchmark_path,
        index=False,
        float_format="%.10f",
    )
    plan_paths["benchmark"] = str(benchmark_path)
    return plan_paths


def build_summary_text(summary_by_variant: dict[str, dict], config: dict, panel: pd.DataFrame, metadata: pd.DataFrame, live_trade_date: str | None) -> str:
    lines = [
        "A-Share ETF Dual Rotation Summary",
        f"Date Range: {config['start-date']} -> {config['end-date']}",
        f"Variant: {config['variant-name']}",
        f"ETF Universe: {len(metadata.index)}",
        f"Scored Rows: {len(panel.index)}",
        f"T0/T1 Counts: {metadata['trading_mode'].value_counts().to_dict() if not metadata.empty else {}}",
        f"Live Trade Date: {live_trade_date or '-'}",
        "",
    ]
    for variant in config["variants"]:
        summary = summary_by_variant.get(variant)
        if not summary:
            continue
        lines.extend(
            [
                f"{variant}:",
                f"  total_return={summary['total_return']:.2%}",
                f"  benchmark_total_return={summary['benchmark_total_return']:.2%}",
                f"  excess_return={summary['excess_return']:.2%}",
                f"  annualized_return={summary['annualized_return']:.2%}",
                f"  max_drawdown={summary['max_drawdown']:.2%}",
                f"  sharpe_ratio={summary['sharpe_ratio']:.2f}",
                f"  rebalance_count={summary['rebalance_count']}",
                f"  average_turnover={summary['average_turnover']:.2%}",
                f"  average_holdings_count={summary['average_holdings_count']:.2f}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def run_pipeline(config: dict) -> dict:
    panel, metadata, benchmark, live_trade_date = build_feature_panel(config)
    if panel.empty:
        raise RuntimeError("ETF dual rotation pipeline produced no eligible feature rows.")

    scored = compute_symbol_scores(panel, config)
    specs_by_variant: dict[str, dict] = {}
    all_daily_frames = []
    all_rebalance_frames = []
    summary_by_variant = {}
    for variant in config["variants"]:
        specs, daily, rebalances, summary = plan_variant(variant, scored, benchmark, live_trade_date, config)
        specs_by_variant[variant] = specs
        if not daily.empty:
            all_daily_frames.append(daily)
        if not rebalances.empty:
            all_rebalance_frames.append(rebalances)
        if summary:
            summary_by_variant[variant] = summary
    plan_paths = export_plan_files(config, specs_by_variant, benchmark)

    daily_output = pd.concat(all_daily_frames, ignore_index=True) if all_daily_frames else pd.DataFrame()
    rebalance_output = pd.concat(all_rebalance_frames, ignore_index=True) if all_rebalance_frames else pd.DataFrame()

    daily_path = Path(config["daily-nav-file"])
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    daily_output.to_csv(daily_path, index=False, float_format="%.10f")

    rebalance_path = Path(config["rebalance-file"])
    rebalance_path.parent.mkdir(parents=True, exist_ok=True)
    rebalance_output.to_csv(rebalance_path, index=False, float_format="%.10f")

    report = {
        "config": {
            "start_date": config["start-date"],
            "end_date": config["end-date"],
            "benchmark_symbol": config["benchmark-symbol"],
            "variant_name": config["variant-name"],
            "target_portfolio_exposure": config["target-portfolio-exposure"],
            "max_symbols_per_day": config["max-symbols-per-day"],
            "t0_top_k": config["t0-top-k"],
            "t1_top_k": config["t1-top-k"],
            "liquidity_quantile": config["liquidity-quantile"],
            "rebalance_interval_days": config["rebalance-interval-days"],
            "max_single_weight": config["max-single-weight"],
        },
        "universe": {
            "etf_count": int(len(metadata.index)),
            "t0_count": int((metadata["trading_mode"] == "T0").sum()) if not metadata.empty else 0,
            "t1_count": int((metadata["trading_mode"] == "T1").sum()) if not metadata.empty else 0,
            "live_trade_date": live_trade_date,
        },
        "plan_files": plan_paths,
        "summary_by_variant": summary_by_variant,
        "summary_text": build_summary_text(summary_by_variant, config, scored, metadata, live_trade_date),
    }

    summary_path = Path(config["summary-file"])
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report["summary_text"], flush=True)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build A-share ETF dual-layer rotation plans and research outputs")
    parser.add_argument("--config")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--summary-file")
    parser.add_argument("--plan-directory")
    parser.add_argument("--benchmark-file")
    parser.add_argument("--daily-nav-file")
    parser.add_argument("--rebalance-file")
    parser.add_argument("--live-price-snapshot-file")
    parser.add_argument("--live-trade-date")
    parser.add_argument("--append-live-trade-date", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    overrides = {
        "start-date": args.start_date,
        "end-date": args.end_date,
        "summary-file": args.summary_file,
        "plan-directory": args.plan_directory,
        "benchmark-file": args.benchmark_file,
        "daily-nav-file": args.daily_nav_file,
        "rebalance-file": args.rebalance_file,
        "live-price-snapshot-file": args.live_price_snapshot_file,
        "live-trade-date": args.live_trade_date,
        "append-live-trade-date": True if args.append_live_trade_date else None,
    }
    report = run_pipeline(load_pipeline_config(args.config, overrides))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
