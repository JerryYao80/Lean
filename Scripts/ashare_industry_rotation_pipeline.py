#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from tushare_data_layer import TushareDataLayer

PATH_KEYS = {
    "tushare-data-path",
    "dataset-catalog",
    "plan-directory",
    "benchmark-file",
    "daily-nav-file",
    "rebalance-file",
    "summary-file",
    "live-price-snapshot-file",
}
DEFAULT_INCLUDED_MARKETS = ("主板", "创业板", "科创板")
DEFAULT_VARIANTS = ("rotation_all", "rotation_leaders", "rotation_resonance")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config() -> dict:
    root = repo_root()
    plan_root = root / "Data" / "alternative" / "ashare-industry-rotation"
    return {
        "tushare-data-path": "/home/project/tushare-downloader/tushare_data_v2",
        "dataset-catalog": str(root / "Launcher" / "config" / "config-ashare-dataset-catalog.json"),
        "start-date": "20200101",
        "end-date": "20251231",
        "benchmark-symbol": "000300.SH",
        "initial-capital": 1000000.0,
        "included-markets": list(DEFAULT_INCLUDED_MARKETS),
        "min-listed-days": 120,
        "min-price": 5.0,
        "exclude-st": True,
        "top-industries": 2,
        "representatives-per-industry": 10,
        "leaders-per-industry": 10,
        "resonance-per-industry": 10,
        "sharpe-lookback": 60,
        "sharpe-min-periods": 40,
        "davol-short-window": 5,
        "davol-long-window": 20,
        "davol-min-periods": 15,
        "resonance-lookback": 20,
        "resonance-top-days": 5,
        "resonance-positive-only": True,
        "industry-score-weights": {
            "trend": 0.45,
            "crowding": 0.20,
            "prosperity": 0.25,
            "foreign": 0.10,
        },
        "forecast-staleness-days": 365,
        "foreign-snapshot-max-age-days": 550,
        "slippage-rate": 0.0002,
        "commission-rate": 0.0003,
        "transfer-fee-rate": 0.00001,
        "stamp-duty-rate": 0.0005,
        "variants": list(DEFAULT_VARIANTS),
        "plan-directory": str(plan_root),
        "benchmark-file": str(plan_root / "benchmark" / "000300.SH.csv"),
        "daily-nav-file": str(root / "Results" / "ashare-industry-rotation-daily.csv"),
        "rebalance-file": str(root / "Results" / "ashare-industry-rotation-rebalances.csv"),
        "summary-file": str(root / "Results" / "ashare-industry-rotation-summary.json"),
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

    config = resolve_config_paths(config, repo_root())
    config["initial-capital"] = safe_float(config.get("initial-capital"), 1000000.0)
    config["min-listed-days"] = safe_int(config.get("min-listed-days"), 120)
    config["min-price"] = safe_float(config.get("min-price"), 5.0)
    config["top-industries"] = safe_int(config.get("top-industries"), 2)
    config["representatives-per-industry"] = safe_int(config.get("representatives-per-industry"), 10)
    config["leaders-per-industry"] = safe_int(config.get("leaders-per-industry"), 10)
    config["resonance-per-industry"] = safe_int(config.get("resonance-per-industry"), 10)
    config["sharpe-lookback"] = safe_int(config.get("sharpe-lookback"), 60)
    config["sharpe-min-periods"] = safe_int(config.get("sharpe-min-periods"), 40)
    config["davol-short-window"] = safe_int(config.get("davol-short-window"), 5)
    config["davol-long-window"] = safe_int(config.get("davol-long-window"), 20)
    config["davol-min-periods"] = safe_int(config.get("davol-min-periods"), 15)
    config["resonance-lookback"] = safe_int(config.get("resonance-lookback"), 20)
    config["resonance-top-days"] = safe_int(config.get("resonance-top-days"), 5)
    config["forecast-staleness-days"] = safe_int(config.get("forecast-staleness-days"), 365)
    config["foreign-snapshot-max-age-days"] = safe_int(config.get("foreign-snapshot-max-age-days"), 550)
    config["slippage-rate"] = safe_float(config.get("slippage-rate"), 0.0002)
    config["commission-rate"] = safe_float(config.get("commission-rate"), 0.0003)
    config["transfer-fee-rate"] = safe_float(config.get("transfer-fee-rate"), 0.00001)
    config["stamp-duty-rate"] = safe_float(config.get("stamp-duty-rate"), 0.0005)
    config["exclude-st"] = str(config.get("exclude-st", True)).strip().lower() in {"1", "true", "yes", "on"}
    config["resonance-positive-only"] = str(config.get("resonance-positive-only", True)).strip().lower() in {"1", "true", "yes", "on"}
    config["append-live-trade-date"] = str(config.get("append-live-trade-date", False)).strip().lower() in {"1", "true", "yes", "on"}
    config["included-markets"] = [str(value) for value in (config.get("included-markets") or DEFAULT_INCLUDED_MARKETS)]
    config["variants"] = [str(value) for value in (config.get("variants") or DEFAULT_VARIANTS)]
    weights = dict(default_config()["industry-score-weights"])
    weights.update(config.get("industry-score-weights") or {})
    config["industry-score-weights"] = {
        "trend": safe_float(weights.get("trend"), 0.45),
        "crowding": safe_float(weights.get("crowding"), 0.20),
        "prosperity": safe_float(weights.get("prosperity"), 0.25),
        "foreign": safe_float(weights.get("foreign"), 0.10),
    }
    return config


def build_dataset_catalog(config: dict) -> dict:
    raw = json.loads(Path(config["dataset-catalog"]).read_text(encoding="utf-8"))
    datasets = dict(raw["datasets"])
    datasets.setdefault(
        "fina_indicator",
        {
            "path": "fina_indicator/ts_code={symbol}/data.parquet",
            "date_field": "ann_date",
            "symbol_field": "ts_code",
        },
    )
    datasets.setdefault(
        "forecast",
        {
            "path": "forecast/ts_code={symbol}/data.parquet",
            "date_field": "ann_date",
            "symbol_field": "ts_code",
        },
    )
    return {"datasets": datasets}


def _zscore(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() < 3:
        return pd.Series(np.nan, index=series.index)
    std = values.std(ddof=0)
    if pd.isna(std) or std == 0:
        return pd.Series(0.0, index=series.index)
    return (values - values.mean()) / std


def annualized_return(total_return: float, trade_days: int) -> float:
    if trade_days <= 0:
        return 0.0
    if total_return <= -1.0:
        return -1.0
    return (1.0 + total_return) ** (252.0 / float(trade_days)) - 1.0


def compute_max_drawdown(nav: pd.Series) -> float:
    if nav.empty:
        return 0.0
    running_max = nav.cummax()
    drawdown = nav / running_max - 1.0
    return float(drawdown.min())


def sharpe_ratio(returns: pd.Series) -> float:
    values = pd.to_numeric(returns, errors="coerce").dropna()
    if len(values) < 2:
        return 0.0
    std = values.std(ddof=0)
    if std == 0 or pd.isna(std):
        return 0.0
    return float(values.mean() / std * math.sqrt(252.0))


def load_benchmark_frame(layer: TushareDataLayer, config: dict, start_date: str | None = None) -> pd.DataFrame:
    frame = layer.load_dataset(
        "index_daily",
        symbol=config["benchmark-symbol"],
        start_date=start_date or config["start-date"],
        end_date=config["end-date"],
        fields=["close", "pct_chg"],
    )
    if frame.empty:
        raise RuntimeError(f"Benchmark {config['benchmark-symbol']} has no data in the requested range.")

    data = frame.copy()
    data["trade_date"] = data["trade_date"].astype(str).str.zfill(8)
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    data["pct_chg"] = pd.to_numeric(data["pct_chg"], errors="coerce")
    data["benchmark_return"] = data["pct_chg"].fillna(0.0) / 100.0
    return data.sort_values("trade_date").reset_index(drop=True)


def load_stock_basic(layer: TushareDataLayer, config: dict) -> pd.DataFrame:
    frame = layer.load_dataset("stock_basic")
    if frame.empty:
        raise RuntimeError("stock_basic dataset is empty.")

    data = frame.copy()
    data["ts_code"] = data["ts_code"].astype(str)
    data["market"] = data.get("market", "").astype(str)
    data["list_date"] = data.get("list_date", "").astype(str).str.zfill(8)
    data["name"] = data.get("name", "").astype(str)
    data = data[data["market"].isin(config["included-markets"])].copy()
    return data[["ts_code", "market", "list_date", "name"]].drop_duplicates("ts_code")


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
    frame = frame[~frame["ts_code"].map(TushareRtDailyClient.is_etf_code)].copy()
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
    extended = pd.concat([benchmark, pd.DataFrame([latest])], ignore_index=True)
    return extended.sort_values("trade_date").drop_duplicates("trade_date", keep="last").reset_index(drop=True)


def append_live_snapshot_to_panel(raw_panel: pd.DataFrame, stock_basic: pd.DataFrame, live_snapshot: pd.DataFrame, live_trade_date: str | None) -> pd.DataFrame:
    if raw_panel.empty or live_snapshot.empty or not live_trade_date:
        return raw_panel

    stock_lookup = stock_basic.drop_duplicates("ts_code").set_index("ts_code")
    latest_history = (
        raw_panel.sort_values(["ts_code", "trade_date"])
        .groupby("ts_code", as_index=False)
        .tail(1)
        .set_index("ts_code")
    )
    rows: list[dict] = []
    for quote in live_snapshot.itertuples(index=False):
        ts_code = str(quote.ts_code)
        if ts_code not in stock_lookup.index:
            continue
        historical = latest_history.loc[ts_code] if ts_code in latest_history.index else None
        stock_name = stock_lookup.at[ts_code, "name"] if "name" in stock_lookup.columns else ""
        if pd.isna(stock_name):
            stock_name = ""
        close = pd.to_numeric(getattr(quote, "close", None), errors="coerce")
        if pd.isna(close):
            close = pd.to_numeric(getattr(quote, "price", None), errors="coerce")
        if pd.isna(close):
            continue

        hist_close = None if historical is None else pd.to_numeric(historical.get("close"), errors="coerce")
        pre_close = pd.to_numeric(getattr(quote, "pre_close", None), errors="coerce")
        if pd.isna(pre_close):
            pre_close = hist_close
        pct_change = pd.to_numeric(getattr(quote, "pct_chg", None), errors="coerce")
        if pd.isna(pct_change) and pre_close not in (None, 0) and not pd.isna(pre_close):
            pct_change = (float(close) / float(pre_close) - 1.0) * 100.0
        float_mv = None if historical is None else pd.to_numeric(historical.get("float_mv"), errors="coerce")
        if not pd.isna(float_mv) and hist_close not in (None, 0) and not pd.isna(hist_close):
            float_mv = float(float_mv) * float(close) / float(hist_close)

        rows.append({
            "ts_code": ts_code,
            "trade_date": str(live_trade_date).zfill(8),
            "name": str(historical.get("name") if historical is not None and not pd.isna(historical.get("name")) else stock_name),
            "pct_change": pct_change,
            "close": close,
            "pre_close": pre_close,
            "vol": pd.to_numeric(getattr(quote, "vol", None), errors="coerce"),
            "amount": pd.to_numeric(getattr(quote, "amount", None), errors="coerce"),
            "turn_over": None if historical is None else pd.to_numeric(historical.get("turn_over"), errors="coerce"),
            "industry": None if historical is None else historical.get("industry"),
            "float_mv": float_mv,
        })

    if not rows:
        return raw_panel

    overlay = pd.DataFrame(rows)
    trade_date = str(live_trade_date).zfill(8)
    merged = raw_panel[raw_panel["trade_date"].astype(str) != trade_date].copy()
    merged = pd.concat([merged, overlay], ignore_index=True)
    merged["trade_date"] = merged["trade_date"].astype(str).str.zfill(8)
    return merged.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)


def read_partitioned_parquet(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=columns)
    try:
        frame = pd.read_parquet(path, columns=columns)
    except Exception:
        return pd.DataFrame(columns=columns)
    return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(columns=columns)


def load_bak_daily_panel(config: dict, trade_dates: list[str]) -> pd.DataFrame:
    root = Path(config["tushare-data-path"]) / "bak_daily"
    columns = [
        "ts_code",
        "trade_date",
        "name",
        "pct_change",
        "close",
        "pre_close",
        "vol",
        "amount",
        "turn_over",
        "industry",
        "float_mv",
    ]
    frames: list[pd.DataFrame] = []
    total = len(trade_dates)
    for index, trade_date in enumerate(trade_dates, start=1):
        if index == 1 or index == total or index % 60 == 0:
            print(f"[industry rotation][bak_daily] loading {index}/{total} trade_date={trade_date}", flush=True)
        path = root / f"date={trade_date}" / "data.parquet"
        frame = read_partitioned_parquet(path, columns)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        raise RuntimeError("No bak_daily snapshots were loaded.")

    panel = pd.concat(frames, ignore_index=True)
    panel["trade_date"] = panel["trade_date"].astype(str).str.zfill(8)
    panel["ts_code"] = panel["ts_code"].astype(str)
    panel["name"] = panel["name"].astype(str)
    panel["industry"] = panel["industry"].astype(str)
    for field in ["pct_change", "close", "pre_close", "vol", "amount", "turn_over", "float_mv"]:
        panel[field] = pd.to_numeric(panel[field], errors="coerce")
    return panel


def load_foreign_preference_snapshots(config: dict) -> list[tuple[str, pd.Series]]:
    root = Path(config["tushare-data-path"]) / "hk_hold"
    snapshots: list[tuple[str, pd.Series]] = []
    for path in sorted(root.glob("year=*/data.parquet")):
        try:
            frame = pd.read_parquet(path, columns=["trade_date", "ts_code", "ratio"])
        except Exception:
            continue
        if frame.empty:
            continue
        frame["trade_date"] = frame["trade_date"].astype(str).str.zfill(8)
        frame["ts_code"] = frame["ts_code"].astype(str)
        frame["ratio"] = pd.to_numeric(frame["ratio"], errors="coerce")
        latest_trade_date = frame["trade_date"].max()
        latest = frame[frame["trade_date"] == latest_trade_date].copy()
        latest = latest.dropna(subset=["ts_code"])
        if latest.empty:
            continue
        latest = latest.sort_values(["ts_code"]).groupby("ts_code", as_index=False)["ratio"].last()
        snapshots.append((latest_trade_date, latest.set_index("ts_code")["ratio"]))
    return snapshots


def resolve_foreign_ratio(
    snapshots: list[tuple[str, pd.Series]],
    signal_date: str,
    ts_code: str,
    max_age_days: int,
) -> float | None:
    signal_timestamp = pd.Timestamp(signal_date)
    selected_series: pd.Series | None = None
    selected_date: str | None = None
    for snapshot_date, series in snapshots:
        if snapshot_date <= signal_date:
            selected_date = snapshot_date
            selected_series = series
        else:
            break
    if selected_series is None or selected_date is None:
        return None
    age_days = (signal_timestamp - pd.Timestamp(selected_date)).days
    if age_days > max_age_days:
        return None
    value = selected_series.get(ts_code)
    if isinstance(value, pd.Series):
        numeric_values = pd.to_numeric(value, errors="coerce").dropna()
        if numeric_values.empty:
            return None
        return float(numeric_values.iloc[-1])
    return None if pd.isna(value) else float(value)


def load_growth_caches(config: dict) -> tuple[TushareDataLayer, dict[str, dict[str, pd.DataFrame]]]:
    layer = TushareDataLayer(config["tushare-data-path"], build_dataset_catalog(config))
    return layer, {}


def load_symbol_growth_history(
    layer: TushareDataLayer,
    cache: dict[str, dict[str, pd.DataFrame]],
    ts_code: str,
) -> dict[str, pd.DataFrame]:
    cached = cache.get(ts_code)
    if cached is not None:
        return cached

    forecast = layer.load_dataset(
        "forecast",
        symbol=ts_code,
        fields=["ann_date", "end_date", "p_change_min", "p_change_max"],
    )
    if not forecast.empty:
        forecast = forecast.copy()
        forecast["ann_date"] = forecast["ann_date"].astype(str).str.zfill(8)
        forecast["forecast_growth"] = pd.to_numeric(forecast["p_change_min"], errors="coerce")
        fallback = pd.to_numeric(forecast["p_change_max"], errors="coerce")
        both = forecast["forecast_growth"].notna() & fallback.notna()
        forecast.loc[both, "forecast_growth"] = (forecast.loc[both, "forecast_growth"] + fallback.loc[both]) / 2.0
        forecast.loc[forecast["forecast_growth"].isna(), "forecast_growth"] = fallback
        forecast = forecast.dropna(subset=["forecast_growth"]).sort_values("ann_date")

    fina = layer.load_dataset(
        "fina_indicator",
        symbol=ts_code,
        fields=["ann_date", "end_date", "dt_netprofit_yoy", "q_sales_yoy", "q_op_qoq"],
    )
    if not fina.empty:
        fina = fina.copy()
        fina["ann_date"] = fina["ann_date"].astype(str).str.zfill(8)
        for field in ["dt_netprofit_yoy", "q_sales_yoy", "q_op_qoq"]:
            fina[field] = pd.to_numeric(fina[field], errors="coerce")
        fina["growth_proxy"] = fina["dt_netprofit_yoy"]
        fina.loc[fina["growth_proxy"].isna(), "growth_proxy"] = fina["q_sales_yoy"]
        fina.loc[fina["growth_proxy"].isna(), "growth_proxy"] = fina["q_op_qoq"]
        fina = fina.dropna(subset=["growth_proxy"]).sort_values("ann_date")

    cached = {
        "forecast": forecast if not forecast.empty else pd.DataFrame(columns=["ann_date", "forecast_growth"]),
        "fina": fina if not fina.empty else pd.DataFrame(columns=["ann_date", "growth_proxy"]),
    }
    cache[ts_code] = cached
    return cached


def resolve_growth_proxy(
    layer: TushareDataLayer,
    cache: dict[str, dict[str, pd.DataFrame]],
    ts_code: str,
    signal_date: str,
    forecast_staleness_days: int,
) -> tuple[float | None, str]:
    histories = load_symbol_growth_history(layer, cache, ts_code)
    signal_timestamp = pd.Timestamp(signal_date)

    forecast = histories["forecast"]
    if not forecast.empty:
        visible = forecast[forecast["ann_date"] <= signal_date]
        if not visible.empty:
            latest = visible.iloc[-1]
            age_days = (signal_timestamp - pd.Timestamp(str(latest["ann_date"]))).days
            if age_days <= forecast_staleness_days:
                return float(latest["forecast_growth"]), "forecast"

    fina = histories["fina"]
    if not fina.empty:
        visible = fina[fina["ann_date"] <= signal_date]
        if not visible.empty:
            latest = visible.iloc[-1]
            return float(latest["growth_proxy"]), "fina_indicator"

    return None, "missing"


def prepare_daily_panel(config: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    layer = TushareDataLayer(config["tushare-data-path"], config["dataset-catalog"])
    warmup_calendar_days = max(
        500,
        config["sharpe-lookback"] * 3,
        config["resonance-lookback"] * 6,
        config["min-listed-days"] * 3,
    )
    benchmark_start = (pd.Timestamp(config["start-date"]) - pd.Timedelta(days=warmup_calendar_days)).strftime("%Y%m%d")
    benchmark_full = load_benchmark_frame(layer, config, start_date=benchmark_start)
    live_trade_date, live_snapshot = load_live_snapshot_frame(config)
    if config.get("append-live-trade-date"):
        benchmark_full = extend_benchmark_with_live_trade_date(benchmark_full, live_trade_date or str(config.get("live-trade-date") or ""))
    requested_start_date = str(config["start-date"])
    effective_start_date = requested_start_date
    available_trade_dates = benchmark_full["trade_date"].tolist()
    if requested_start_date not in set(available_trade_dates):
        candidates = [trade_date for trade_date in available_trade_dates if trade_date >= requested_start_date]
        if not candidates:
            raise RuntimeError(
                f"Configured start-date {requested_start_date} is later than the last benchmark trading day {available_trade_dates[-1]}."
            )
        effective_start_date = candidates[0]
    config["effective-start-date"] = effective_start_date
    benchmark = benchmark_full[benchmark_full["trade_date"] >= effective_start_date].copy().reset_index(drop=True)
    stock_basic = load_stock_basic(layer, config)
    full_trade_dates = benchmark_full["trade_date"].tolist()
    raw_panel = load_bak_daily_panel(config, full_trade_dates)
    raw_panel = append_live_snapshot_to_panel(raw_panel, stock_basic, live_snapshot, live_trade_date or str(config.get("live-trade-date") or ""))

    panel = raw_panel.merge(stock_basic, on="ts_code", how="inner", suffixes=("", "_basic"))
    panel["trade_timestamp"] = pd.to_datetime(panel["trade_date"], format="%Y%m%d")
    panel["list_timestamp"] = pd.to_datetime(panel["list_date"], format="%Y%m%d", errors="coerce")
    panel["listed_days"] = (panel["trade_timestamp"] - panel["list_timestamp"]).dt.days
    panel["is_st"] = panel["name"].str.upper().str.contains("ST", na=False)
    panel["return"] = panel["pct_change"] / 100.0
    panel["industry"] = panel["industry"].replace({"": np.nan, "None": np.nan})
    panel = panel.dropna(subset=["industry", "close"]).copy()

    panel = panel.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    grouped = panel.groupby("ts_code", sort=False)
    ret_mean = grouped["return"].rolling(config["sharpe-lookback"], min_periods=config["sharpe-min-periods"]).mean()
    ret_std = grouped["return"].rolling(config["sharpe-lookback"], min_periods=config["sharpe-min-periods"]).std(ddof=0)
    turn_short = grouped["turn_over"].rolling(config["davol-short-window"], min_periods=config["davol-short-window"]).mean()
    turn_long = grouped["turn_over"].rolling(config["davol-long-window"], min_periods=config["davol-min-periods"]).mean()
    panel["sharpe_ratio_60"] = (ret_mean.reset_index(level=0, drop=True) / ret_std.reset_index(level=0, drop=True)) * math.sqrt(252.0)
    panel["davol20"] = turn_short.reset_index(level=0, drop=True) / turn_long.reset_index(level=0, drop=True) - 1.0

    return panel, benchmark, stock_basic


def group_trade_dates_by_month(trade_dates: list[str]) -> list[str]:
    frame = pd.DataFrame({"trade_date": trade_dates})
    return frame.groupby(frame["trade_date"].str[:6])["trade_date"].last().tolist()


def price_limit_pct(market: str, is_st: bool) -> float:
    if is_st:
        return 0.05
    if market in {"创业板", "科创板"}:
        return 0.20
    return 0.10


def compute_resonance_score(
    history: pd.DataFrame,
    industry_return_lookup: pd.Series,
    lookback: int,
    top_days: int,
    positive_only: bool,
) -> tuple[float | None, list[str]]:
    if history.empty:
        return None, []

    window = history.tail(lookback).copy()
    if window.empty:
        return None, []
    window["strength"] = pd.to_numeric(window["return"], errors="coerce").fillna(0.0) * pd.to_numeric(window["vol"], errors="coerce").fillna(0.0)
    if positive_only:
        window = window[window["return"] > 0.0].copy()
    if window.empty:
        return None, []

    strongest = window.nlargest(top_days, "strength").copy()
    weights = np.array([0.5 ** index for index in range(len(strongest))], dtype=float)
    score = 0.0
    used = []
    for index, row in enumerate(strongest.itertuples(index=False)):
        industry_return = industry_return_lookup.get((str(row.trade_date), str(row.industry)))
        if pd.isna(industry_return):
            continue
        score += float(weights[index]) * float(industry_return)
        used.append(str(row.trade_date))
    return (score if used else None), used


def build_signal_specs(config: dict) -> tuple[dict[str, dict], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    panel, benchmark, stock_basic = prepare_daily_panel(config)
    growth_layer, growth_cache = load_growth_caches(config)
    foreign_snapshots = load_foreign_preference_snapshots(config)
    effective_start_date = str(config.get("effective-start-date") or config["start-date"])
    monthly_signal_dates = [trade_date for trade_date in group_trade_dates_by_month(benchmark["trade_date"].tolist()) if trade_date >= effective_start_date]
    benchmark_date_lookup = {trade_date: benchmark.iloc[index + 1]["trade_date"] for index, trade_date in enumerate(benchmark["trade_date"].tolist()[:-1])}
    industry_returns = panel.groupby(["trade_date", "industry"], sort=True)["return"].mean()

    history_source = panel[["ts_code", "trade_date", "return", "vol", "industry"]].copy()
    grouped_history = history_source.groupby("ts_code", sort=False)
    history_cache: dict[str, pd.DataFrame] = {}
    daily_lookup = {
        trade_date: group.copy().reset_index(drop=True)
        for trade_date, group in panel.groupby("trade_date", sort=True)
    }

    def get_history(ts_code: str) -> pd.DataFrame:
        cached = history_cache.get(ts_code)
        if cached is not None:
            return cached
        group = grouped_history.get_group(ts_code).copy().reset_index(drop=True)
        history_cache[ts_code] = group
        return group

    specs_by_variant = {variant: {} for variant in config["variants"]}
    rebalance_rows = []
    total_signals = len(monthly_signal_dates)
    for index, signal_date in enumerate(monthly_signal_dates, start=1):
        if index == 1 or index == total_signals or index % 12 == 0:
            print(f"[industry rotation][signal] building {index}/{total_signals} signal_date={signal_date}", flush=True)

        snapshot = daily_lookup.get(signal_date)
        execution_date = benchmark_date_lookup.get(signal_date)
        if snapshot is None or execution_date is None:
            continue

        eligible = snapshot.copy()
        eligible = eligible[
            (eligible["close"] >= config["min-price"])
            & (eligible["listed_days"] >= config["min-listed-days"])
            & eligible["industry"].notna()
        ].copy()
        if config["exclude-st"]:
            eligible = eligible[~eligible["is_st"]].copy()
        if eligible.empty:
            continue

        representatives = (
            eligible.sort_values(["industry", "float_mv"], ascending=[True, False])
            .groupby("industry", sort=True)
            .head(config["representatives-per-industry"])
            .copy()
        )
        if representatives.empty:
            continue

        representatives["growth_proxy"] = np.nan
        representatives["growth_source"] = "missing"
        representatives["foreign_ratio"] = np.nan
        for row_index, row in representatives.iterrows():
            growth_value, growth_source = resolve_growth_proxy(
                growth_layer,
                growth_cache,
                str(row["ts_code"]),
                signal_date,
                config["forecast-staleness-days"],
            )
            foreign_ratio = resolve_foreign_ratio(
                foreign_snapshots,
                signal_date,
                str(row["ts_code"]),
                config["foreign-snapshot-max-age-days"],
            )
            representatives.at[row_index, "growth_proxy"] = growth_value
            representatives.at[row_index, "growth_source"] = growth_source
            representatives.at[row_index, "foreign_ratio"] = foreign_ratio

        industry_frame = representatives.groupby("industry", sort=True).agg(
            representative_count=("ts_code", "count"),
            sharpe_ratio_60=("sharpe_ratio_60", "mean"),
            davol20=("davol20", "mean"),
            prosperity_proxy=("growth_proxy", "mean"),
            foreign_ratio=("foreign_ratio", "mean"),
        ).reset_index()
        industry_frame = industry_frame[industry_frame["representative_count"] >= max(3, config["representatives-per-industry"] // 2)].copy()
        if industry_frame.empty:
            continue

        weights = config["industry-score-weights"]
        industry_frame["z_trend"] = _zscore(industry_frame["sharpe_ratio_60"])
        industry_frame["z_crowding"] = _zscore(industry_frame["davol20"])
        industry_frame["z_prosperity"] = _zscore(industry_frame["prosperity_proxy"])
        industry_frame["z_foreign"] = _zscore(industry_frame["foreign_ratio"])
        industry_frame["industry_score"] = (
            weights["trend"] * industry_frame["z_trend"].fillna(0.0)
            - weights["crowding"] * industry_frame["z_crowding"].fillna(0.0)
            + weights["prosperity"] * industry_frame["z_prosperity"].fillna(0.0)
            + weights["foreign"] * industry_frame["z_foreign"].fillna(0.0)
        )
        industry_frame = industry_frame.sort_values("industry_score", ascending=False).reset_index(drop=True)
        selected_industries = industry_frame.head(config["top-industries"]).copy()
        if selected_industries.empty:
            continue
        selected_names = selected_industries["industry"].astype(str).tolist()

        eligible_selected = eligible[eligible["industry"].isin(selected_names)].copy()
        if eligible_selected.empty:
            continue

        leaders = (
            eligible_selected.sort_values(["industry", "float_mv"], ascending=[True, False])
            .groupby("industry", sort=True)
            .head(config["leaders-per-industry"])
            .copy()
        )
        resonance_rows = []
        resonance_candidates = eligible_selected.sort_values(["industry", "float_mv"], ascending=[True, False]).copy()
        for candidate in resonance_candidates.itertuples(index=False):
            history = get_history(str(candidate.ts_code))
            history = history[history["trade_date"] <= signal_date]
            score, top_trade_dates = compute_resonance_score(
                history,
                industry_returns,
                config["resonance-lookback"],
                config["resonance-top-days"],
                config["resonance-positive-only"],
            )
            if score is None:
                continue
            resonance_rows.append(
                {
                    "ts_code": str(candidate.ts_code),
                    "industry": str(candidate.industry),
                    "resonance_score": score,
                    "top_trade_dates": ";".join(top_trade_dates),
                    "float_mv": float(candidate.float_mv) if not pd.isna(candidate.float_mv) else np.nan,
                }
            )
        resonance_frame = pd.DataFrame(resonance_rows)
        if not resonance_frame.empty:
            resonance_frame = resonance_frame.merge(
                selected_industries[["industry", "industry_score"]],
                on="industry",
                how="left",
            )
            resonance_frame = resonance_frame.sort_values(
                ["industry", "resonance_score", "float_mv"],
                ascending=[True, False, False],
            )
            resonance_selected = resonance_frame.groupby("industry", sort=True).head(config["resonance-per-industry"]).copy()
        else:
            resonance_selected = pd.DataFrame(columns=["ts_code", "industry", "resonance_score", "top_trade_dates", "industry_score"])

        variant_frames = {
            "rotation_all": eligible_selected[["ts_code", "industry"]].copy(),
            "rotation_leaders": leaders[["ts_code", "industry", "float_mv"]].copy(),
            "rotation_resonance": resonance_selected.copy(),
        }
        for variant, frame in variant_frames.items():
            if variant not in specs_by_variant:
                continue
            if frame.empty:
                continue
            symbols = frame["ts_code"].astype(str).tolist()
            weight = 1.0 / float(len(symbols))
            specs_by_variant[variant][execution_date] = {
                "variant": variant,
                "signal_date": signal_date,
                "execution_date": execution_date,
                "selected_industries": selected_names,
                "industry_score_map": {
                    row["industry"]: float(row["industry_score"])
                    for _, row in selected_industries[["industry", "industry_score"]].iterrows()
                },
                "target_weights": {symbol: weight for symbol in symbols},
                "holding_details": frame.to_dict(orient="records"),
            }

        rebalance_rows.append(
            {
                "signal_date": signal_date,
                "execution_date": execution_date,
                "selected_industries": ";".join(selected_names),
                "industry_scores": ";".join(
                    f"{row['industry']}={float(row['industry_score']):.4f}"
                    for _, row in selected_industries[["industry", "industry_score"]].iterrows()
                ),
                "industry_count": len(selected_names),
                "rotation_all_count": int(len(eligible_selected.index)),
                "rotation_leaders_count": int(len(leaders.index)),
                "rotation_resonance_count": int(len(resonance_selected.index)),
            }
        )

    return specs_by_variant, benchmark, pd.DataFrame(rebalance_rows), panel


def rebalance_state(
    cash: float,
    positions: dict[str, float],
    target_weights: dict[str, float],
    snapshot: pd.DataFrame,
    config: dict,
) -> tuple[float, dict[str, float], dict]:
    snapshot_lookup = snapshot.set_index("ts_code") if not snapshot.empty else pd.DataFrame().set_index(pd.Index([], name="ts_code"))
    equity = cash + sum(positions.values())
    if equity <= 0.0:
        return cash, positions, {
            "turnover": 0.0,
            "buy_turnover": 0.0,
            "sell_turnover": 0.0,
            "cost": 0.0,
            "blocked_buys": "",
            "blocked_sells": "",
        }

    buy_cost_rate = config["commission-rate"] + config["transfer-fee-rate"] + config["slippage-rate"]
    sell_cost_rate = config["commission-rate"] + config["transfer-fee-rate"] + config["stamp-duty-rate"] + config["slippage-rate"]
    target_values = {symbol: equity * weight for symbol, weight in target_weights.items()}
    current_values = dict(positions)
    blocked_buys: list[str] = []
    blocked_sells: list[str] = []
    sell_turnover = 0.0
    buy_requests: list[tuple[str, float]] = []

    for symbol in sorted(set(current_values) | set(target_values)):
        current_value = current_values.get(symbol, 0.0)
        desired_value = target_values.get(symbol, 0.0)
        delta = desired_value - current_value
        row = snapshot_lookup.loc[symbol] if symbol in snapshot_lookup.index else None
        if delta < -1e-12:
            if row is None or pd.isna(row.get("close")):
                blocked_sells.append(symbol)
                continue
            limit_pct = price_limit_pct(str(row.get("market")), bool(row.get("is_st")))
            if float(row.get("pct_change") or 0.0) <= -(limit_pct * 100.0) + 1e-9:
                blocked_sells.append(symbol)
                continue
            positions[symbol] = max(0.0, desired_value)
            sell_turnover += current_value - positions[symbol]
        elif delta > 1e-12:
            if row is None or pd.isna(row.get("close")):
                blocked_buys.append(symbol)
                continue
            limit_pct = price_limit_pct(str(row.get("market")), bool(row.get("is_st")))
            if float(row.get("pct_change") or 0.0) >= (limit_pct * 100.0) - 1e-9:
                blocked_buys.append(symbol)
                continue
            buy_requests.append((symbol, delta))

    cash += sell_turnover * (1.0 - sell_cost_rate)
    buy_budget = max(cash / (1.0 + buy_cost_rate), 0.0)
    total_requested = sum(requested for _, requested in buy_requests)
    buy_turnover = 0.0
    if total_requested > 0 and buy_budget > 0:
        for symbol, requested in buy_requests:
            trade_value = buy_budget * (requested / total_requested)
            current_value = positions.get(symbol, 0.0)
            positions[symbol] = current_value + trade_value
            buy_turnover += trade_value
        cash -= buy_turnover * (1.0 + buy_cost_rate)

    positions = {symbol: value for symbol, value in positions.items() if value > 1e-8}
    cost = sell_turnover * sell_cost_rate + buy_turnover * buy_cost_rate
    turnover = (sell_turnover + buy_turnover) / equity if equity > 0 else 0.0
    return cash, positions, {
        "turnover": turnover,
        "buy_turnover": buy_turnover / equity if equity > 0 else 0.0,
        "sell_turnover": sell_turnover / equity if equity > 0 else 0.0,
        "cost": cost / equity if equity > 0 else 0.0,
        "blocked_buys": ";".join(blocked_buys),
        "blocked_sells": ";".join(blocked_sells),
    }


def simulate_variant(
    variant: str,
    plan_specs: dict[str, dict],
    panel: pd.DataFrame,
    benchmark: pd.DataFrame,
    config: dict,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    positions: dict[str, float] = {}
    cash = float(config["initial-capital"])
    daily_rows = []
    rebalance_rows = []
    panel_by_date = {trade_date: group.copy().reset_index(drop=True) for trade_date, group in panel.groupby("trade_date", sort=True)}
    benchmark_nav = (1.0 + benchmark["benchmark_return"].fillna(0.0)).cumprod()
    benchmark_lookup = benchmark.set_index("trade_date")
    benchmark_nav_lookup = dict(zip(benchmark["trade_date"].tolist(), benchmark_nav.tolist()))

    for trade_date in benchmark["trade_date"].tolist():
        snapshot = panel_by_date.get(trade_date)
        if snapshot is not None and positions:
            returns = snapshot.set_index("ts_code")["return"]
            for symbol in list(positions.keys()):
                daily_return = float(returns.get(symbol, 0.0) or 0.0)
                positions[symbol] *= (1.0 + daily_return)

        if trade_date in plan_specs:
            signal = plan_specs[trade_date]
            cash, positions, trade_meta = rebalance_state(
                cash,
                positions,
                signal["target_weights"],
                snapshot if snapshot is not None else pd.DataFrame(columns=panel.columns),
                config,
            )
            holdings_total = cash + sum(positions.values())
            weight_map = {
                symbol: value / holdings_total
                for symbol, value in positions.items()
                if holdings_total > 0 and value > 0
            }
            industry_weights = defaultdict(float)
            if snapshot is not None and weight_map:
                industry_map = snapshot.set_index("ts_code")["industry"].to_dict()
                for symbol, weight in weight_map.items():
                    industry = industry_map.get(symbol)
                    if industry:
                        industry_weights[str(industry)] += float(weight)
            rebalance_rows.append(
                {
                    "variant": variant,
                    "signal_date": signal["signal_date"],
                    "execution_date": trade_date,
                    "selected_industries": ";".join(signal["selected_industries"]),
                    "holding_count": len(signal["target_weights"]),
                    "turnover": trade_meta["turnover"],
                    "buy_turnover": trade_meta["buy_turnover"],
                    "sell_turnover": trade_meta["sell_turnover"],
                    "cost": trade_meta["cost"],
                    "blocked_buys": trade_meta["blocked_buys"],
                    "blocked_sells": trade_meta["blocked_sells"],
                    "industry_exposure": ";".join(
                        f"{industry}={weight:.4f}"
                        for industry, weight in sorted(industry_weights.items())
                    ),
                }
            )

        equity = cash + sum(positions.values())
        weights = [value / equity for value in positions.values()] if equity > 0 and positions else []
        daily_rows.append(
            {
                "variant": variant,
                "trade_date": trade_date,
                "equity": equity,
                "nav": equity / float(config["initial-capital"]) if config["initial-capital"] else 0.0,
                "cash": cash,
                "holdings_count": len(positions),
                "max_single_weight": max(weights) if weights else 0.0,
                "hhi": sum(weight * weight for weight in weights) if weights else 0.0,
                "benchmark_nav": float(benchmark_nav_lookup.get(trade_date, 1.0)),
                "benchmark_return": float(benchmark_lookup.at[trade_date, "benchmark_return"]) if trade_date in benchmark_lookup.index else 0.0,
            }
        )

    daily = pd.DataFrame(daily_rows)
    rebalances = pd.DataFrame(rebalance_rows)
    if daily.empty:
        return daily, rebalances, {}

    daily["daily_return"] = daily["nav"].pct_change().fillna(daily["nav"] - 1.0)
    daily["drawdown"] = daily["nav"] / daily["nav"].cummax() - 1.0
    daily["benchmark_drawdown"] = daily["benchmark_nav"] / daily["benchmark_nav"].cummax() - 1.0
    total_return = float(daily["nav"].iloc[-1] - 1.0)
    benchmark_total_return = float(daily["benchmark_nav"].iloc[-1] - 1.0)
    summary = {
        "variant": variant,
        "start_date": daily["trade_date"].iloc[0],
        "end_date": daily["trade_date"].iloc[-1],
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
        "average_hhi": float(daily["hhi"].mean()),
    }
    return daily, rebalances, summary


def export_plan_files(config: dict, specs_by_variant: dict[str, dict], benchmark: pd.DataFrame) -> dict[str, str]:
    plan_root = Path(config["plan-directory"])
    plan_root.mkdir(parents=True, exist_ok=True)
    plan_paths = {}
    for variant, plans in specs_by_variant.items():
        rows = []
        for execution_date, spec in sorted(plans.items()):
            detail_map = {
                str(row.get("ts_code")): row
                for row in (spec.get("holding_details") or [])
                if str(row.get("ts_code") or "").strip()
            }
            for symbol, weight in sorted(spec["target_weights"].items()):
                detail = detail_map.get(symbol, {})
                rows.append(
                    {
                        "variant": variant,
                        "signal_date": spec["signal_date"],
                        "execution_date": execution_date,
                        "symbol": symbol,
                        "target_weight": weight,
                        "industry": detail.get("industry"),
                        "industry_score": spec["industry_score_map"].get(str(detail.get("industry") or "")),
                        "resonance_score": detail.get("resonance_score"),
                        "float_mv": detail.get("float_mv"),
                        "top_trade_dates": detail.get("top_trade_dates"),
                        "selected_industries": ";".join(spec["selected_industries"]),
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


def build_summary_text(summary_by_variant: dict[str, dict]) -> str:
    lines = [
        "A-Share Industry Rotation Summary",
        "",
    ]
    order = ["rotation_all", "rotation_leaders", "rotation_resonance"]
    for variant in order:
        summary = summary_by_variant.get(variant)
        if not summary:
            continue
        lines.extend(
            [
                f"{variant}:",
                f"  total_return={summary['total_return']:.2%}",
                f"  excess_return={summary['excess_return']:.2%}",
                f"  annualized_return={summary['annualized_return']:.2%}",
                f"  max_drawdown={summary['max_drawdown']:.2%}",
                f"  sharpe_ratio={summary['sharpe_ratio']:.2f}",
                f"  average_turnover={summary['average_turnover']:.2%}",
                f"  average_holdings_count={summary['average_holdings_count']:.2f}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def run_pipeline(config: dict) -> dict:
    specs_by_variant, benchmark, rebalance_signal_report, panel = build_signal_specs(config)

    plan_paths = export_plan_files(config, specs_by_variant, benchmark)
    all_daily_frames = []
    all_rebalance_frames = [rebalance_signal_report.assign(variant="signal_generation")] if not rebalance_signal_report.empty else []
    summary_by_variant = {}
    for variant in config["variants"]:
        daily, rebalances, summary = simulate_variant(variant, specs_by_variant.get(variant, {}), panel, benchmark, config)
        if not daily.empty:
            all_daily_frames.append(daily)
        if not rebalances.empty:
            all_rebalance_frames.append(rebalances)
        if summary:
            summary_by_variant[variant] = summary

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
            "effective_start_date": config.get("effective-start-date", config["start-date"]),
            "end_date": config["end-date"],
            "benchmark_symbol": config["benchmark-symbol"],
            "included_markets": config["included-markets"],
            "top_industries": config["top-industries"],
            "representatives_per_industry": config["representatives-per-industry"],
            "leaders_per_industry": config["leaders-per-industry"],
            "resonance_per_industry": config["resonance-per-industry"],
            "weights": config["industry-score-weights"],
        },
        "limitations": {
            "foreign_preference": "hk_hold in local data is only available as sparse annual snapshots; the strategy uses the latest visible snapshot ratio instead of a true 20-day average.",
            "prosperity_proxy": "long_term_predicted_earnings_growth is unavailable in local Tushare parquet, so the strategy uses a priority chain of forecast midpoint growth, then fina_indicator growth fields announced on or before the signal date.",
            "industry_classification": "industry membership is taken from bak_daily.industry on the signal date, not from today's static stock_basic.industry field.",
        },
        "plan_files": plan_paths,
        "summary_by_variant": summary_by_variant,
        "summary_text": build_summary_text(summary_by_variant),
    }

    summary_path = Path(config["summary-file"])
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report["summary_text"], flush=True)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build A-share industry rotation research outputs and LEAN rebalance plans")
    parser.add_argument("--config")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--summary-file")
    parser.add_argument("--plan-directory")
    parser.add_argument("--benchmark-file")
    parser.add_argument("--daily-nav-file")
    parser.add_argument("--rebalance-file")
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
    }
    report = run_pipeline(load_pipeline_config(args.config, overrides))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
