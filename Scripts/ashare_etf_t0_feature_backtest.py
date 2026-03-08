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
BASE_SIGNAL_FIELDS = [
    "signal_momentum_20",
    "signal_momentum_5",
    "signal_liquidity_5",
    "signal_close_location",
    "signal_volatility_10",
    "signal_gap_abs",
]
EXTENDED_SIGNAL_FIELDS = [
    "signal_nav_premium_1",
    "signal_nav_premium_z20",
    "signal_share_change_5",
    "signal_size_change_5",
    "signal_excess_gap",
    "signal_excess_intraday",
    "signal_tracking_error_10",
    "signal_index_momentum_5",
]
FEATURE_FIELD_LINES = [
    "- momentum_20: close / close[-20] - 1",
    "- momentum_5: close / close[-5] - 1",
    "- liquidity_5: rolling mean(amount, 5)",
    "- close_location: (close - low) / (high - low)",
    "- volatility_10: rolling std(pct_chg, 10)",
    "- gap_abs: abs(open / pre_close - 1)",
    "- nav_premium_1: close / unit_nav - 1",
    "- nav_premium_z20: rolling zscore(nav_premium_1, 20)",
    "- share_change_5: total_share / total_share[-5] - 1",
    "- size_change_5: total_size / total_size[-5] - 1",
    "- excess_gap: etf gap_return - index gap_return",
    "- excess_intraday: etf trade_return - index trade_return",
    "- tracking_error_10: rolling std(etf_close_ret - index_close_ret, 10)",
    "- index_momentum_5: index close / close[-5] - 1",
]


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
        "portfolio-quarter-kelly-medium-regime-multiplier": 0.75,
        "portfolio-quarter-kelly-high-regime-multiplier": 0.5,
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


def _safe_residual(series: pd.Series, reference: pd.Series) -> pd.Series:
    values = _numeric(series)
    baseline = _numeric(reference)
    valid = values.notna() & baseline.notna()
    residual = pd.Series(0.0, index=series.index)
    if valid.sum() <= 1:
        return residual

    y = values[valid]
    x = baseline[valid]
    x_centered = x - x.mean()
    y_centered = y - y.mean()
    denominator = float((x_centered ** 2).sum())
    if math.isclose(denominator, 0.0):
        residual.loc[valid] = y_centered
        return residual

    beta = float((x_centered * y_centered).sum() / denominator)
    residual.loc[valid] = y_centered - beta * x_centered
    if float(residual.abs().max()) <= 1e-12:
        return pd.Series(0.0, index=series.index)
    return residual


def _classify_risk_regime_bucket(group: pd.DataFrame, config: dict | None = None) -> str:
    config = config or {}
    market_signal_momentum5_mean = float(_numeric(group.get("signal_momentum_5", pd.Series(dtype=float))).mean()) if "signal_momentum_5" in group.columns else 0.0
    market_signal_volatility10_mean = float(_numeric(group.get("signal_volatility_10", pd.Series(dtype=float))).mean()) if "signal_volatility_10" in group.columns else 0.0

    high_momentum_threshold = config.get("risk-regime-momentum-threshold")
    high_volatility_threshold = config.get("risk-regime-volatility-threshold")
    if (
        high_momentum_threshold is not None
        and high_volatility_threshold is not None
        and market_signal_momentum5_mean <= float(high_momentum_threshold)
        and market_signal_volatility10_mean >= float(high_volatility_threshold)
    ):
        return "high"

    medium_momentum_threshold = config.get("risk-regime-medium-momentum-threshold")
    medium_volatility_threshold = config.get("risk-regime-medium-volatility-threshold")
    if (
        medium_momentum_threshold is not None
        and medium_volatility_threshold is not None
        and market_signal_momentum5_mean <= float(medium_momentum_threshold)
        and market_signal_volatility10_mean >= float(medium_volatility_threshold)
    ):
        return "medium"

    return "normal"


def _conditional_nav_premium_z20_scale(risk_regime_bucket: str, config: dict | None = None) -> float:
    config = config or {}
    key = {
        "normal": "conditional-signal-nav-premium-z20-normal-scale",
        "medium": "conditional-signal-nav-premium-z20-medium-scale",
        "high": "conditional-signal-nav-premium-z20-high-scale",
    }.get(str(risk_regime_bucket or "normal"), "conditional-signal-nav-premium-z20-normal-scale")
    return float(config.get(key, 0.0) or 0.0)


def _apply_conditional_nav_premium_z20(
    group: pd.DataFrame,
    base_score: pd.Series,
    risk_regime_bucket: str,
    config: dict | None = None,
) -> pd.Series:
    config = config or {}
    contribution = pd.Series(0.0, index=group.index)
    weight = float(config.get("conditional-signal-nav-premium-z20-weight", 0.0) or 0.0)
    if math.isclose(weight, 0.0) or "signal_nav_premium_z20" not in group.columns:
        return contribution

    scale = _conditional_nav_premium_z20_scale(risk_regime_bucket, config)
    if math.isclose(scale, 0.0):
        return contribution

    overlay_signal = _safe_zscore(group["signal_nav_premium_z20"]).fillna(0.0)
    if bool(config.get("conditional-signal-nav-premium-z20-orthogonalize", False)):
        overlay_signal = _safe_residual(overlay_signal, base_score)
        overlay_signal = _safe_zscore(overlay_signal).fillna(0.0)

    return overlay_signal * weight * scale


def _safe_ratio_minus_one(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    numer = _numeric(numerator)
    denom = _numeric(denominator).replace(0, float("nan"))
    return numer / denom - 1


def _window_return(series: pd.Series, periods: int) -> pd.Series:
    numeric = _numeric(series).replace(0, float("nan"))
    return numeric / numeric.shift(periods).replace(0, float("nan")) - 1


def _rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    numeric = _numeric(series)
    rolling_mean = numeric.rolling(window).mean()
    rolling_std = numeric.rolling(window).std(ddof=0).replace(0, float("nan"))
    return (numeric - rolling_mean) / rolling_std


def _normalize_reference_frame(
    frame: pd.DataFrame | None,
    date_field: str,
    numeric_fields: list[str],
    rename_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    if frame is None or frame.empty or date_field not in frame.columns:
        return pd.DataFrame()

    data = frame.copy()
    data[date_field] = data[date_field].astype(str).str.zfill(8)
    for field in numeric_fields:
        if field in data.columns:
            data[field] = _numeric(data[field])

    keep = [date_field, *[field for field in numeric_fields if field in data.columns]]
    data = data[keep].drop_duplicates(subset=[date_field], keep="last")
    if rename_map:
        data = data.rename(columns=rename_map)
    return data.sort_values("trade_date").reset_index(drop=True)


def build_etf_metadata_lookup(layer: TushareDataLayer) -> dict[str, dict]:
    if "etf_basic" not in layer.catalog:
        return {}

    frame = layer.load_dataset("etf_basic")
    if frame.empty or "ts_code" not in frame.columns:
        return {}

    metadata = {}
    for _, row in frame.iterrows():
        ts_code = str(row.get("ts_code") or "").strip()
        if not ts_code:
            continue
        metadata[ts_code] = {
            "index_code": row.get("index_code"),
            "index_name": row.get("index_name"),
            "mgt_fee": row.get("mgt_fee"),
            "etf_type": row.get("etf_type"),
        }
    return metadata


def prepare_symbol_frame(
    symbol: str,
    frame: pd.DataFrame,
    nav_frame: pd.DataFrame | None = None,
    share_size_frame: pd.DataFrame | None = None,
    index_frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    data = frame.copy()
    data["trade_date"] = data["trade_date"].astype(str).str.zfill(8)
    data = data.sort_values("trade_date").reset_index(drop=True)

    for column in ["pre_close", "open", "high", "low", "close", "pct_chg", "amount", "vol"]:
        if column in data.columns:
            data[column] = _numeric(data[column])

    data["symbol"] = symbol
    data["trade_return"] = _safe_ratio_minus_one(data["close"], data["open"])
    data["gap_return"] = _safe_ratio_minus_one(data["open"], data["pre_close"])
    price_range = (_numeric(data["high"]) - _numeric(data["low"])).replace(0, float("nan"))
    data["close_location"] = ((_numeric(data["close"]) - _numeric(data["low"])) / price_range).clip(0, 1).fillna(0.5)
    data["range_pct"] = _safe_ratio_minus_one(data["high"], data["pre_close"]) - _safe_ratio_minus_one(data["low"], data["pre_close"])
    data["momentum_5"] = _window_return(data["close"], 5)
    data["momentum_20"] = _window_return(data["close"], 20)
    data["volatility_10"] = _numeric(data["pct_chg"]).rolling(10).std(ddof=0)
    data["liquidity_5"] = _numeric(data["amount"]).rolling(5).mean()
    data["gap_abs"] = _numeric(data["gap_return"]).abs()

    nav = _normalize_reference_frame(
        nav_frame,
        date_field="nav_date",
        numeric_fields=["unit_nav", "adj_nav"],
        rename_map={"nav_date": "trade_date"},
    )
    if not nav.empty:
        data = data.merge(nav, on="trade_date", how="left")
    else:
        data["unit_nav"] = None
        data["adj_nav"] = None

    shares = _normalize_reference_frame(
        share_size_frame,
        date_field="trade_date",
        numeric_fields=["total_share", "total_size"],
    )
    if not shares.empty:
        data = data.merge(shares, on="trade_date", how="left")
    else:
        data["total_share"] = None
        data["total_size"] = None

    index_daily = _normalize_reference_frame(
        index_frame,
        date_field="trade_date",
        numeric_fields=["pre_close", "open", "close"],
        rename_map={
            "pre_close": "index_pre_close",
            "open": "index_open",
            "close": "index_close",
        },
    )
    if not index_daily.empty:
        data = data.merge(index_daily, on="trade_date", how="left")
    else:
        data["index_pre_close"] = None
        data["index_open"] = None
        data["index_close"] = None

    data["nav_premium_1"] = _safe_ratio_minus_one(data["close"], data["unit_nav"])
    data["nav_premium_z20"] = _rolling_zscore(data["nav_premium_1"], 20)
    data["share_change_5"] = _window_return(data["total_share"], 5)
    data["size_change_5"] = _window_return(data["total_size"], 5)
    data["index_gap_return"] = _safe_ratio_minus_one(data["index_open"], data["index_pre_close"])
    data["index_trade_return"] = _safe_ratio_minus_one(data["index_close"], data["index_open"])
    data["index_close_return_1"] = _safe_ratio_minus_one(data["index_close"], data["index_pre_close"])
    data["index_momentum_5"] = _window_return(data["index_close"], 5)
    data["excess_gap"] = _numeric(data["gap_return"]) - _numeric(data["index_gap_return"])
    data["excess_intraday"] = _numeric(data["trade_return"]) - _numeric(data["index_trade_return"])
    close_return_1 = _safe_ratio_minus_one(data["close"], data["pre_close"])
    data["tracking_error_10"] = (close_return_1 - _numeric(data["index_close_return_1"])).rolling(10).std(ddof=0)

    shifted_features = [
        "momentum_20",
        "momentum_5",
        "liquidity_5",
        "close_location",
        "volatility_10",
        "gap_abs",
        "nav_premium_1",
        "nav_premium_z20",
        "share_change_5",
        "size_change_5",
        "excess_gap",
        "excess_intraday",
        "tracking_error_10",
        "index_momentum_5",
    ]
    for feature in shifted_features:
        data[f"signal_{feature}"] = _numeric(data[feature]).shift(1)

    return data.astype(object).where(pd.notna(data), None)


def build_symbol_feature_frame(
    layer: TushareDataLayer,
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    metadata_lookup: dict[str, dict] | None = None,
) -> pd.DataFrame:
    frame = layer.load_dataset(
        "fund_daily",
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        fields=["pre_close", "open", "high", "low", "close", "pct_chg", "amount", "vol"],
    )
    if frame.empty:
        return frame

    metadata_lookup = metadata_lookup or {}
    metadata = metadata_lookup.get(symbol, {})
    index_code = metadata.get("index_code")

    nav_frame = pd.DataFrame()
    if "fund_nav" in layer.catalog:
        nav_frame = layer.load_dataset(
            "fund_nav",
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            fields=["unit_nav", "adj_nav"],
        )

    share_size_frame = pd.DataFrame()
    if "etf_share_size" in layer.catalog:
        share_size_frame = layer.load_dataset(
            "etf_share_size",
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            fields=["total_share", "total_size"],
        )

    index_frame = pd.DataFrame()
    if index_code and "index_daily" in layer.catalog:
        index_frame = layer.load_dataset(
            "index_daily",
            symbol=str(index_code),
            start_date=start_date,
            end_date=end_date,
            fields=["pre_close", "open", "close"],
        )

    return prepare_symbol_frame(
        symbol,
        frame,
        nav_frame=nav_frame,
        share_size_frame=share_size_frame,
        index_frame=index_frame,
    )


def compute_cross_section_scores(panel: pd.DataFrame, config: dict | None = None) -> pd.DataFrame:
    required = ["trade_date", "symbol", "trade_return", *BASE_SIGNAL_FIELDS]
    scored = panel.copy()
    scored = scored.dropna(subset=required).reset_index(drop=True)
    if scored.empty:
        return scored

    config = config or {}
    score_frames = []
    for trade_date, group in scored.groupby("trade_date", sort=True):
        group = group.copy()
        base_total = pd.Series(0.0, index=group.index)
        for field, weight in FEATURE_WEIGHTS.items():
            if field not in group.columns:
                continue
            base_total = base_total + _safe_zscore(group[field]).fillna(0.0) * weight

        risk_regime_bucket = _classify_risk_regime_bucket(group, config)
        overlay_nav_premium_z20 = _apply_conditional_nav_premium_z20(group, base_total, risk_regime_bucket, config)
        group["score_base"] = base_total
        group["score_overlay_nav_premium_z20"] = overlay_nav_premium_z20
        group["score_overlay_nav_premium_z20_scale"] = _conditional_nav_premium_z20_scale(risk_regime_bucket, config)
        group["score_risk_regime_bucket"] = risk_regime_bucket
        group["score"] = base_total + overlay_nav_premium_z20
        score_frames.append(group)

    if not score_frames:
        return pd.DataFrame(columns=list(scored.columns) + ["score"])

    return pd.concat(score_frames, ignore_index=True)


def _apply_liquidity_whitelist(group: pd.DataFrame, liquidity_quantile: float) -> pd.DataFrame:
    quantile = float(liquidity_quantile or 0.0)
    if quantile <= 0 or "signal_liquidity_5" not in group.columns:
        return group

    liquidity = _numeric(group["signal_liquidity_5"])
    if liquidity.dropna().empty:
        return group

    cutoff = float(liquidity.quantile(min(max(quantile, 0.0), 1.0)))
    filtered = group[liquidity >= cutoff].copy()
    return filtered if not filtered.empty else group


def _tail_realized_returns(realized_returns: list[float], lookback: int) -> pd.Series:
    if not realized_returns:
        return pd.Series(dtype=float)

    values = realized_returns[-int(lookback):] if int(lookback or 0) > 0 else realized_returns
    return pd.Series([float(value) for value in values], dtype=float)


def _clamp(value: float, lower: float, upper: float) -> float:
    lower_bound = min(float(lower), float(upper))
    upper_bound = max(float(lower), float(upper))
    return max(lower_bound, min(float(value), upper_bound))


def _portfolio_vol_target_scale(
    realized_returns: list[float],
    enabled: bool,
    daily_vol_target: float,
    lookback: int,
    min_observations: int,
    floor_scale: float,
    cap_scale: float,
) -> float:
    if not enabled:
        return 1.0

    trailing = _tail_realized_returns(realized_returns, lookback)
    if len(trailing) < max(1, int(min_observations or 1)):
        return 1.0

    std = float(trailing.std(ddof=0))
    if not math.isfinite(std) or std <= 0:
        return 1.0

    raw_scale = float(daily_vol_target) / std
    return _clamp(raw_scale, floor_scale, cap_scale)


def _portfolio_quarter_kelly_scale(
    realized_returns: list[float],
    risk_regime_bucket: str,
    enabled: bool,
    lookback: int,
    min_observations: int,
    floor_scale: float,
    cap_scale: float,
    medium_regime_multiplier: float,
    high_regime_multiplier: float,
) -> float:
    if not enabled:
        return 1.0

    trailing = _tail_realized_returns(realized_returns, lookback)
    if len(trailing) < max(1, int(min_observations or 1)):
        return 1.0

    mean = float(trailing.mean())
    variance = float(trailing.var(ddof=0))
    if not math.isfinite(mean) or not math.isfinite(variance):
        return 1.0
    if variance <= 0:
        return float(floor_scale) if mean <= 0 else 1.0

    raw_scale = max(0.0, 0.25 * mean / variance)
    regime_multiplier = {
        "medium": float(medium_regime_multiplier),
        "high": float(high_regime_multiplier),
    }.get(str(risk_regime_bucket or "normal"), 1.0)
    return _clamp(raw_scale * regime_multiplier, floor_scale, cap_scale)


def backtest_from_scores(
    scored: pd.DataFrame,
    top_n: int = 3,
    fee_rate: float = 0.0006,
    min_score_spread: float = 0.0,
    max_average_gap_abs: float | None = None,
    risk_regime_filter_enabled: bool = False,
    risk_regime_medium_momentum_threshold: float | None = None,
    risk_regime_medium_volatility_threshold: float | None = None,
    risk_regime_medium_exposure_scale: float = 1.0,
    risk_regime_medium_top_n: int | None = None,
    risk_regime_medium_score_spread_add: float = 0.0,
    risk_regime_medium_liquidity_quantile: float = 0.0,
    risk_regime_momentum_threshold: float | None = None,
    risk_regime_volatility_threshold: float | None = None,
    risk_regime_high_exposure_scale: float = 0.0,
    risk_regime_high_top_n: int | None = None,
    risk_regime_high_score_spread_add: float = 0.0,
    risk_regime_high_liquidity_quantile: float = 0.0,
    portfolio_vol_target_enabled: bool = False,
    portfolio_vol_target_daily_vol: float = 0.012,
    portfolio_vol_target_lookback: int = 20,
    portfolio_vol_target_min_observations: int = 10,
    portfolio_vol_target_floor_scale: float = 0.5,
    portfolio_vol_target_cap_scale: float = 1.0,
    portfolio_quarter_kelly_enabled: bool = False,
    portfolio_quarter_kelly_lookback: int = 20,
    portfolio_quarter_kelly_min_observations: int = 10,
    portfolio_quarter_kelly_floor_scale: float = 0.25,
    portfolio_quarter_kelly_cap_scale: float = 1.0,
    portfolio_quarter_kelly_medium_regime_multiplier: float = 0.75,
    portfolio_quarter_kelly_high_regime_multiplier: float = 0.5,
) -> tuple[pd.DataFrame, dict]:
    daily_rows = []
    selection_counter: Counter[str] = Counter()
    equity = 1.0
    scored_trade_days = 0
    skipped_low_conviction_days = 0
    skipped_gap_risk_days = 0
    medium_risk_regime_days = 0
    high_risk_regime_days = 0
    exposure_scales = []
    selected_counts = []
    portfolio_risk_overlay_scales = []
    portfolio_vol_target_scales = []
    portfolio_quarter_kelly_scales = []
    realized_returns: list[float] = []

    for trade_date, group in scored.groupby("trade_date", sort=True):
        scored_trade_days += 1
        score_series = _numeric(group["score"])
        if score_series.empty:
            continue

        market_signal_momentum5_mean = float(_numeric(group["signal_momentum_5"]).mean()) if "signal_momentum_5" in group.columns else 0.0
        market_signal_volatility10_mean = float(_numeric(group["signal_volatility_10"]).mean()) if "signal_volatility_10" in group.columns else 0.0
        exposure_scale = 1.0
        risk_regime_bucket = "normal"
        effective_top_n = int(top_n)
        effective_min_score_spread = float(min_score_spread)
        liquidity_quantile = 0.0
        if risk_regime_filter_enabled:
            if (
                risk_regime_momentum_threshold is not None
                and risk_regime_volatility_threshold is not None
                and market_signal_momentum5_mean <= float(risk_regime_momentum_threshold)
                and market_signal_volatility10_mean >= float(risk_regime_volatility_threshold)
            ):
                exposure_scale = float(risk_regime_high_exposure_scale)
                risk_regime_bucket = "high"
                high_risk_regime_days += 1
                if risk_regime_high_top_n is not None and int(risk_regime_high_top_n) > 0:
                    effective_top_n = min(int(top_n), int(risk_regime_high_top_n))
                effective_min_score_spread = float(min_score_spread) + float(risk_regime_high_score_spread_add or 0.0)
                liquidity_quantile = float(risk_regime_high_liquidity_quantile or 0.0)
            elif (
                risk_regime_medium_momentum_threshold is not None
                and risk_regime_medium_volatility_threshold is not None
                and market_signal_momentum5_mean <= float(risk_regime_medium_momentum_threshold)
                and market_signal_volatility10_mean >= float(risk_regime_medium_volatility_threshold)
            ):
                exposure_scale = float(risk_regime_medium_exposure_scale)
                risk_regime_bucket = "medium"
                medium_risk_regime_days += 1
                if risk_regime_medium_top_n is not None and int(risk_regime_medium_top_n) > 0:
                    effective_top_n = min(int(top_n), int(risk_regime_medium_top_n))
                effective_min_score_spread = float(min_score_spread) + float(risk_regime_medium_score_spread_add or 0.0)
                liquidity_quantile = float(risk_regime_medium_liquidity_quantile or 0.0)

        score_spread = float(score_series.max() - score_series.median())
        if effective_min_score_spread > 0 and score_spread < effective_min_score_spread:
            skipped_low_conviction_days += 1
            continue

        working_group = _apply_liquidity_whitelist(group, liquidity_quantile)
        picks = working_group.sort_values("score", ascending=False).head(max(1, effective_top_n)).reset_index(drop=True)
        if picks.empty:
            continue

        average_gap_abs = float(_numeric(picks["signal_gap_abs"]).mean()) if "signal_gap_abs" in picks.columns else 0.0
        if max_average_gap_abs is not None and average_gap_abs > max_average_gap_abs:
            skipped_gap_risk_days += 1
            continue

        vol_target_scale = _portfolio_vol_target_scale(
            realized_returns,
            enabled=bool(portfolio_vol_target_enabled),
            daily_vol_target=float(portfolio_vol_target_daily_vol),
            lookback=int(portfolio_vol_target_lookback),
            min_observations=int(portfolio_vol_target_min_observations),
            floor_scale=float(portfolio_vol_target_floor_scale),
            cap_scale=float(portfolio_vol_target_cap_scale),
        )
        quarter_kelly_scale = _portfolio_quarter_kelly_scale(
            realized_returns,
            risk_regime_bucket=risk_regime_bucket,
            enabled=bool(portfolio_quarter_kelly_enabled),
            lookback=int(portfolio_quarter_kelly_lookback),
            min_observations=int(portfolio_quarter_kelly_min_observations),
            floor_scale=float(portfolio_quarter_kelly_floor_scale),
            cap_scale=float(portfolio_quarter_kelly_cap_scale),
            medium_regime_multiplier=float(portfolio_quarter_kelly_medium_regime_multiplier),
            high_regime_multiplier=float(portfolio_quarter_kelly_high_regime_multiplier),
        )
        portfolio_risk_overlay_scale = min(1.0, float(vol_target_scale) * float(quarter_kelly_scale))
        exposure_scale = min(1.0, float(exposure_scale) * portfolio_risk_overlay_scale)

        if exposure_scale <= 0:
            continue

        gross_return = float(_numeric(picks["trade_return"]).mean())
        net_return = (gross_return - fee_rate) * exposure_scale
        equity *= 1 + net_return
        symbols = picks["symbol"].tolist()
        selection_counter.update(symbols)
        exposure_scales.append(exposure_scale)
        selected_counts.append(len(symbols))
        portfolio_risk_overlay_scales.append(portfolio_risk_overlay_scale)
        portfolio_vol_target_scales.append(vol_target_scale)
        portfolio_quarter_kelly_scales.append(quarter_kelly_scale)
        realized_returns.append(net_return)

        daily_rows.append({
            "trade_date": trade_date,
            "selected_symbols": ",".join(symbols),
            "selected_count": int(len(symbols)),
            "gross_return": gross_return,
            "net_return": net_return,
            "equity": equity,
            "score_mean": float(_numeric(picks["score"]).mean()),
            "score_spread": score_spread,
            "average_gap_abs": average_gap_abs,
            "market_signal_momentum5_mean": market_signal_momentum5_mean,
            "market_signal_volatility10_mean": market_signal_volatility10_mean,
            "risk_regime_bucket": risk_regime_bucket,
            "exposure_scale": exposure_scale,
            "effective_top_n": int(max(1, effective_top_n)),
            "effective_min_score_spread": float(effective_min_score_spread),
            "liquidity_quantile": float(liquidity_quantile),
            "portfolio_risk_overlay_scale": float(portfolio_risk_overlay_scale),
            "portfolio_vol_target_scale": float(vol_target_scale),
            "portfolio_quarter_kelly_scale": float(quarter_kelly_scale),
        })

    daily = pd.DataFrame(daily_rows)
    summary = summarize_backtest(daily, selection_counter)
    summary.update({
        "scored_trade_days": int(scored_trade_days),
        "skipped_low_conviction_days": int(skipped_low_conviction_days),
        "skipped_gap_risk_days": int(skipped_gap_risk_days),
        "medium_risk_regime_days": int(medium_risk_regime_days),
        "high_risk_regime_days": int(high_risk_regime_days),
        "selected_trade_days": int(len(daily)),
        "selection_rate": float(len(daily) / scored_trade_days) if scored_trade_days > 0 else 0.0,
        "average_exposure_scale": float(sum(exposure_scales) / len(exposure_scales)) if exposure_scales else 0.0,
        "average_selected_count": float(sum(selected_counts) / len(selected_counts)) if selected_counts else 0.0,
        "average_portfolio_risk_overlay_scale": float(sum(portfolio_risk_overlay_scales) / len(portfolio_risk_overlay_scales)) if portfolio_risk_overlay_scales else 0.0,
        "average_portfolio_vol_target_scale": float(sum(portfolio_vol_target_scales) / len(portfolio_vol_target_scales)) if portfolio_vol_target_scales else 0.0,
        "average_portfolio_quarter_kelly_scale": float(sum(portfolio_quarter_kelly_scales) / len(portfolio_quarter_kelly_scales)) if portfolio_quarter_kelly_scales else 0.0,
        "min_score_spread": float(min_score_spread),
        "max_average_gap_abs": float(max_average_gap_abs) if max_average_gap_abs is not None else None,
        "risk_regime_filter_enabled": bool(risk_regime_filter_enabled),
        "risk_regime_medium_momentum_threshold": float(risk_regime_medium_momentum_threshold) if risk_regime_medium_momentum_threshold is not None else None,
        "risk_regime_medium_volatility_threshold": float(risk_regime_medium_volatility_threshold) if risk_regime_medium_volatility_threshold is not None else None,
        "risk_regime_medium_exposure_scale": float(risk_regime_medium_exposure_scale),
        "risk_regime_medium_top_n": int(risk_regime_medium_top_n) if risk_regime_medium_top_n is not None else None,
        "risk_regime_medium_score_spread_add": float(risk_regime_medium_score_spread_add or 0.0),
        "risk_regime_medium_liquidity_quantile": float(risk_regime_medium_liquidity_quantile or 0.0),
        "risk_regime_momentum_threshold": float(risk_regime_momentum_threshold) if risk_regime_momentum_threshold is not None else None,
        "risk_regime_volatility_threshold": float(risk_regime_volatility_threshold) if risk_regime_volatility_threshold is not None else None,
        "risk_regime_high_exposure_scale": float(risk_regime_high_exposure_scale),
        "risk_regime_high_top_n": int(risk_regime_high_top_n) if risk_regime_high_top_n is not None else None,
        "risk_regime_high_score_spread_add": float(risk_regime_high_score_spread_add or 0.0),
        "risk_regime_high_liquidity_quantile": float(risk_regime_high_liquidity_quantile or 0.0),
        "portfolio_vol_target_enabled": bool(portfolio_vol_target_enabled),
        "portfolio_vol_target_daily_vol": float(portfolio_vol_target_daily_vol),
        "portfolio_vol_target_lookback": int(portfolio_vol_target_lookback),
        "portfolio_vol_target_min_observations": int(portfolio_vol_target_min_observations),
        "portfolio_vol_target_floor_scale": float(portfolio_vol_target_floor_scale),
        "portfolio_vol_target_cap_scale": float(portfolio_vol_target_cap_scale),
        "portfolio_quarter_kelly_enabled": bool(portfolio_quarter_kelly_enabled),
        "portfolio_quarter_kelly_lookback": int(portfolio_quarter_kelly_lookback),
        "portfolio_quarter_kelly_min_observations": int(portfolio_quarter_kelly_min_observations),
        "portfolio_quarter_kelly_floor_scale": float(portfolio_quarter_kelly_floor_scale),
        "portfolio_quarter_kelly_cap_scale": float(portfolio_quarter_kelly_cap_scale),
        "portfolio_quarter_kelly_medium_regime_multiplier": float(portfolio_quarter_kelly_medium_regime_multiplier),
        "portfolio_quarter_kelly_high_regime_multiplier": float(portfolio_quarter_kelly_high_regime_multiplier),
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
            f"quarter_kelly lookback={int(config.get('portfolio-quarter-kelly-lookback', 20) or 20)}, floor/cap={float(config.get('portfolio-quarter-kelly-floor-scale', 0.25) or 0.0):.2f}/{float(config.get('portfolio-quarter-kelly-cap-scale', 1.0) or 0.0):.2f}, regime mult={float(config.get('portfolio-quarter-kelly-medium-regime-multiplier', 0.75) or 0.0):.2f}/{float(config.get('portfolio-quarter-kelly-high-regime-multiplier', 0.5) or 0.0):.2f})"
            if config.get("portfolio-vol-target-enabled") or config.get("portfolio-quarter-kelly-enabled")
            else "Portfolio Risk Overlay: disabled"
        ),
        "Feature Fields:",
        *FEATURE_FIELD_LINES,
        "Results:",
        f"- Trade Days: {summary['trade_days']}",
        f"- Scored Trade Days: {summary['scored_trade_days']}",
        f"- Selected Trade Days: {summary['selected_trade_days']}",
        f"- Selection Rate: {summary['selection_rate']:.2%}",
        f"- Average Exposure Scale: {summary['average_exposure_scale']:.2%}",
        f"- Average Portfolio Risk Overlay Scale: {summary['average_portfolio_risk_overlay_scale']:.2%}",
        f"- Average Vol Target Scale: {summary['average_portfolio_vol_target_scale']:.2%}",
        f"- Average Quarter-Kelly Scale: {summary['average_portfolio_quarter_kelly_scale']:.2%}",
        f"- Average Selected Count: {summary['average_selected_count']:.2f}",
        f"- Skipped Low Conviction Days: {summary['skipped_low_conviction_days']}",
        f"- Skipped Gap Risk Days: {summary['skipped_gap_risk_days']}",
        f"- Medium Risk Regime Days: {summary['medium_risk_regime_days']}",
        f"- High Risk Regime Days: {summary['high_risk_regime_days']}",
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
        portfolio_quarter_kelly_medium_regime_multiplier=float(config.get("portfolio-quarter-kelly-medium-regime-multiplier", 0.75) or 0.75),
        portfolio_quarter_kelly_high_regime_multiplier=float(config.get("portfolio-quarter-kelly-high-regime-multiplier", 0.5) or 0.5),
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
    parser.add_argument("--portfolio-quarter-kelly-medium-regime-multiplier", type=float)
    parser.add_argument("--portfolio-quarter-kelly-high-regime-multiplier", type=float)
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
        "portfolio-quarter-kelly-medium-regime-multiplier": args.portfolio_quarter_kelly_medium_regime_multiplier,
        "portfolio-quarter-kelly-high-regime-multiplier": args.portfolio_quarter_kelly_high_regime_multiplier,
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
    summary = run_backtest(config)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
