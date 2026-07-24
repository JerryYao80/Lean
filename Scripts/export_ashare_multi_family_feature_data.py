#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from tushare_data_layer import DatasetSpec, TushareDataLayer

PATH_KEYS = {
    'tushare-data-path',
    'field-mapping-path',
    'feature-data-path',
    'report-file',
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config() -> dict:
    root = repo_root()
    feature_root = root / 'Data' / 'alternative' / 'ashare-multi-family-features'
    return {
        'tushare-data-path': '/home/project/tushare-downloader/tushare_data_v2',
        'field-mapping-path': '/home/project/tushare-downloader/tushare_field_mapping.json',
        'feature-data-path': str(feature_root),
        'report-file': str(root / 'Results' / 'ashare-multi-family-feature-export-report.json'),
        'start-date': '20200101',
        'end-date': '20251231',
        'universe': 'csi300',
        'benchmark-symbol': '000300.SH',
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
        config_path = Path(config_path).resolve()
        loaded = json.loads(config_path.read_text(encoding='utf-8'))
        config.update(resolve_config_paths(loaded, config_path.parent))

    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            config[key] = value

    return resolve_config_paths(config, repo_root())


STRATEGY_FAMILY_REGISTRY = {
    "momentum_reversal": {
        "datasets": ["daily", "daily_basic", "adj_factor"],
        "fields": {
            "daily": ["close", "pct_chg", "vol", "amount", "pre_close"],
            "daily_basic": ["turnover_rate", "total_mv", "circ_mv", "pe", "pb"],
            "adj_factor": ["adj_factor"],
        },
    },
    "value_quality": {
        "datasets": ["daily_basic", "fina_indicator"],
        "fields": {
            "daily_basic": ["pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm", "total_mv", "circ_mv"],
            "fina_indicator": ["roe", "roe_waa", "roa", "grossprofit_margin", "current_ratio", "quick_ratio", "debt_to_assets", "netprofit_margin", "bps", "netprofit_yoy", "dt_netprofit_yoy"],
        },
    },
    "money_flow": {
        "datasets": ["moneyflow", "daily"],
        "fields": {
            "moneyflow": ["net_mf_vol", "net_mf_amount", "buy_sm_vol", "buy_sm_amount", "sell_sm_vol", "sell_sm_amount",
                           "buy_md_vol", "buy_md_amount", "sell_md_vol", "sell_md_amount",
                           "buy_lg_vol", "buy_lg_amount", "sell_lg_vol", "sell_lg_amount",
                           "buy_elg_vol", "buy_elg_amount", "sell_elg_vol", "sell_elg_amount"],
            "daily": ["close", "vol", "amount", "pct_chg"],
        },
    },
    "earnings_surprise": {
        "datasets": ["forecast", "express", "fina_indicator"],
        "fields": {
            "forecast": ["p_change_min", "p_change_max", "type", "net_profit_min", "net_profit_max", "summary", "ann_date"],
            "express": ["diluted_eps", "diluted_roe", "yoy_net_profit", "n_income", "revenue", "operate_profit"],
            "fina_indicator": ["eps", "dt_eps", "basic_eps_yoy", "dt_eps_yoy", "netprofit_yoy", "dt_netprofit_yoy", "roe", "op_yoy", "ebt_yoy", "tr_yoy", "or_yoy"],
        },
    },
    "chip_cost": {
        "datasets": ["cyq_perf", "daily"],
        "fields": {
            "cyq_perf": ["cost_5pct", "cost_15pct", "cost_50pct", "cost_85pct", "cost_95pct", "weight_avg", "winner_rate", "his_low", "his_high"],
            "daily": ["close", "pct_chg", "vol", "amount"],
        },
    },
    "etf_premium": {
        "datasets": ["fund_daily", "fund_nav", "etf_share_size"],
        "fields": {
            "fund_daily": ["close", "pre_close", "pct_chg", "vol", "amount"],
            "fund_nav": ["unit_nav", "accum_nav", "adj_nav", "net_asset", "total_netasset"],
            "etf_share_size": ["total_share", "total_size", "etf_name"],
        },
    },
    "sector_rotation": {
        "datasets": ["sw_daily", "ths_daily", "dc_daily", "index_weight", "daily"],
        "fields": {
            "sw_daily": ["close", "pct_change", "pe", "pb", "vol", "amount", "float_mv", "total_mv"],
            "ths_daily": ["close", "pct_change", "vol", "turnover_rate", "avg_price"],
            "dc_daily": ["close", "pct_change", "vol", "amount", "swing", "turnover_rate", "category"],
            "index_weight": ["index_code", "con_code", "weight"],
            "daily": ["close", "pct_chg", "vol", "amount"],
        },
    },
    "margin_signal": {
        "datasets": ["margin_detail", "daily"],
        "fields": {
            "margin_detail": ["rzye", "rqye", "rzmre", "rqyl", "rzche", "rzrqye", "rqchl", "rqmcl"],
            "daily": ["close", "pct_chg", "vol", "amount"],
        },
    },
    "northbound_flow": {
        "datasets": ["moneyflow_hsgt", "hk_hold", "daily"],
        "fields": {
            "moneyflow_hsgt": ["hgt", "sgt", "north_money", "south_money", "ggt_ss", "ggt_sz"],
            "hk_hold": ["vol", "ratio", "ts_code", "name"],
            "daily": ["close", "pct_chg", "vol", "amount"],
        },
    },
    "multi_factor": {
        "datasets": ["daily_basic", "fina_indicator", "moneyflow"],
        "fields": {
            "daily_basic": ["pe", "pe_ttm", "pb", "ps", "dv_ttm", "turnover_rate", "total_mv", "circ_mv", "volume_ratio"],
            "fina_indicator": ["roe", "roa", "netprofit_yoy", "dt_netprofit_yoy", "grossprofit_margin", "debt_to_assets", "assets_turn", "current_ratio"],
            "moneyflow": ["net_mf_vol", "net_mf_amount", "buy_lg_vol", "buy_lg_amount", "sell_lg_vol", "sell_lg_amount"],
        },
    },
    "analyst_signal": {
        "datasets": ["report_rc", "top_inst", "forecast"],
        "fields": {
            "report_rc": ["rating", "max_price", "min_price", "eps", "pe", "roe", "org_name", "report_title"],
            "top_inst": ["buy", "buy_rate", "sell", "sell_rate", "net_buy", "side", "reason", "exalter"],
            "forecast": ["p_change_min", "p_change_max", "type", "summary", "ann_date"],
        },
    },
    "macro_rate": {
        "datasets": ["cn_cpi", "cn_gdp", "cn_m", "cn_pmi", "shibor", "shibor_lpr", "repo_daily"],
        "fields": {
            "cn_cpi": ["nt_yoy", "nt_mom", "town_yoy", "cnt_yoy"],
            "cn_gdp": ["gdp_yoy", "pi_yoy", "si_yoy", "ti_yoy"],
            "cn_m": ["m0_yoy", "m1_yoy", "m2_yoy", "m2_mom"],
            "cn_pmi": ["PMI020201", "PMI010401", "PMI010000"],
            "shibor": ["on", "1w", "1m", "3m", "6m", "1y"],
            "shibor_lpr": ["1y", "5y"],
            "repo_daily": ["close", "amount", "repo_maturity", "ts_code"],
        },
    },
    "basis_sentiment": {
        "datasets": ["fut_daily", "index_daily"],
        "fields": {
            "fut_daily": ["close", "settle", "oi", "vol"],
            "index_daily": ["close"],
        },
    },
    "options_pcr": {
        "datasets": ["opt_daily"],
        "fields": {
            "opt_daily": ["vol", "oi", "close"],
        },
    },
    "margin_short_ratio": {
        "datasets": ["margin"],
        "fields": {
            "margin": ["rzye", "rqye", "rzmre", "rzche"],
        },
    },
}


def discover_universe_symbols(universe: str, layer: TushareDataLayer | None = None, external_factor_path: str | Path | None = None) -> list[str]:
    """Discover universe symbols from tushare index_weight or existing feature CSVs."""
    if external_factor_path:
        feature_path = Path(external_factor_path)
        symbols = []
        for market in ["sse", "szse"]:
            market_path = feature_path / market / "daily"
            if market_path.exists():
                for csv_file in market_path.glob("*.csv"):
                    ticker = csv_file.stem
                    suffix = "SH" if market == "sse" else "SZ"
                    symbols.append(f"{ticker}.{suffix}")
        if symbols:
            return sorted(symbols)

    if layer:
        index_map = {"csi300": "000300.SH", "csi500": "000905.SH"}
        index_code = index_map.get(universe, "000300.SH")
        try:
            codes = load_index_constituents(layer, index_code)
            if codes:
                return codes
        except KeyError:
            pass

    return ["600519.SH", "000858.SZ", "601318.SH", "600036.SH", "000651.SZ"]


def build_multi_family_catalog() -> dict[str, DatasetSpec]:
    all_datasets = set()
    for family_info in STRATEGY_FAMILY_REGISTRY.values():
        all_datasets.update(family_info["datasets"])

    catalog = {
        "daily": DatasetSpec(name="daily", path="daily/ts_code={symbol}/data.parquet", date_field="trade_date", symbol_field="ts_code"),
        "daily_basic": DatasetSpec(name="daily_basic", path="daily_basic/ts_code={symbol}/data.parquet", date_field="trade_date", symbol_field="ts_code"),
        "adj_factor": DatasetSpec(name="adj_factor", path="adj_factor/ts_code={symbol}/data.parquet", date_field="trade_date", symbol_field="ts_code"),
        "moneyflow": DatasetSpec(name="moneyflow", path="moneyflow/ts_code={symbol}/data.parquet", date_field="trade_date", symbol_field="ts_code"),
        "fina_indicator": DatasetSpec(name="fina_indicator", path="fina_indicator/ts_code={symbol}/data.parquet", date_field="ann_date", symbol_field="ts_code"),
        "forecast": DatasetSpec(name="forecast", path="forecast/ts_code={symbol}/data.parquet", date_field="ann_date", symbol_field="ts_code"),
        "express": DatasetSpec(name="express", path="express/quarter=*/data.parquet", date_field="ann_date", symbol_field="ts_code"),
        "cyq_perf": DatasetSpec(name="cyq_perf", path="cyq_perf/ts_code={symbol}/data.parquet", date_field="trade_date", symbol_field="ts_code"),
        "margin_detail": DatasetSpec(name="margin_detail", path="margin_detail/ts_code={symbol}/data.parquet", date_field="trade_date", symbol_field="ts_code"),
        "moneyflow_hsgt": DatasetSpec(name="moneyflow_hsgt", path="moneyflow_hsgt/year=*/data.parquet", date_field="trade_date"),
        "hk_hold": DatasetSpec(name="hk_hold", path="hk_hold/year=*/data.parquet", date_field="trade_date", symbol_field="ts_code"),
        "report_rc": DatasetSpec(name="report_rc", path="report_rc/quarter=*/data.parquet", date_field="report_date", symbol_field="ts_code"),
        "top_inst": DatasetSpec(name="top_inst", path="top_inst/date=*/data.parquet", date_field="trade_date", symbol_field="ts_code"),
        "index_weight": DatasetSpec(name="index_weight", path="index_weight/trade_date=*/data.parquet", date_field="trade_date"),
        "fund_daily": DatasetSpec(name="fund_daily", path="fund_daily/ts_code={symbol}/data.parquet", date_field="trade_date", symbol_field="ts_code"),
        "fund_nav": DatasetSpec(name="fund_nav", path="fund_nav/ts_code={symbol}/data.parquet", date_field="nav_date", symbol_field="ts_code"),
        "etf_share_size": DatasetSpec(name="etf_share_size", path="etf_share_size/year=*/data.parquet", date_field="trade_date", symbol_field="ts_code"),
        "sw_daily": DatasetSpec(name="sw_daily", path="sw_daily/year=*/data.parquet", date_field="trade_date", symbol_field="ts_code"),
        "ths_daily": DatasetSpec(name="ths_daily", path="ths_daily/year=*/data.parquet", date_field="trade_date", symbol_field="ts_code"),
        "dc_daily": DatasetSpec(name="dc_daily", path="dc_daily/year=*/data.parquet", date_field="trade_date", symbol_field="ts_code"),
        "cn_cpi": DatasetSpec(name="cn_cpi", path="cn_cpi/data.parquet", date_field="month"),
        "cn_gdp": DatasetSpec(name="cn_gdp", path="cn_gdp/data.parquet", date_field="quarter"),
        "cn_m": DatasetSpec(name="cn_m", path="cn_m/data.parquet", date_field="month"),
        "cn_pmi": DatasetSpec(name="cn_pmi", path="cn_pmi/data.parquet", date_field="MONTH"),
        "shibor": DatasetSpec(name="shibor", path="shibor/year=*/data.parquet", date_field="date"),
        "shibor_lpr": DatasetSpec(name="shibor_lpr", path="shibor_lpr/data.parquet", date_field="date"),
        "repo_daily": DatasetSpec(name="repo_daily", path="repo_daily/year=*/data.parquet", date_field="trade_date"),
    }
    return catalog


def load_index_constituents(layer: TushareDataLayer, index_code: str = "000300.SH") -> list[str]:
    df = layer.load_dataset("index_weight")
    if df.empty:
        return []
    subset = df[df["index_code"] == index_code] if "index_code" in df.columns else df
    if "con_code" in subset.columns:
        codes = sorted(subset["con_code"].dropna().astype(str).unique().tolist())
    else:
        codes = []
    return codes


def normalize_date_column(df: pd.DataFrame, col: str) -> pd.DataFrame:
    if col not in df.columns:
        return df
    df = df.copy()
    # If the column is datetime64, convert to string first
    if pd.api.types.is_datetime64_any_dtype(df[col]):
        df[col] = df[col].dt.strftime('%Y-%m-%d')
    s = df[col].astype(str).str.strip()
    # YYYY-MM-DD already normalized (from datetime conversion) — keep as-is
    mask_ymd_dash = s.str.match(r'^\d{4}-\d{2}-\d{2}$')
    if mask_ymd_dash.all():
        return df
    # YYYYMMDD → YYYY-MM-DD
    mask_ymd = (~mask_ymd_dash) & s.str.match(r'^\d{8}$')
    if mask_ymd.any():
        df.loc[mask_ymd, col] = s[mask_ymd].str[:4] + '-' + s[mask_ymd].str[4:6] + '-' + s[mask_ymd].str[6:8]
    # YYYYMM → YYYY-MM-01 (monthly macro)
    mask_ym = (~mask_ymd_dash) & (~mask_ymd) & s.str.match(r'^\d{6}$')
    if mask_ym.any():
        df.loc[mask_ym, col] = s[mask_ym].str[:4] + '-' + s[mask_ym].str[4:6] + '-01'
    # YYYYQN → last day of quarter
    mask_q = (~mask_ymd_dash) & (~mask_ymd) & (~mask_ym) & s.str.match(r'^\d{4}Q\d$')
    if mask_q.any():
        qtr_last = {'Q1': '03-31', 'Q2': '06-30', 'Q3': '09-30', 'Q4': '12-31'}
        df.loc[mask_q, col] = s[mask_q].str[:4] + '-' + s[mask_q].str[4:6].map(qtr_last)
    # Ensure final dtype is string for consistent merges
    df[col] = df[col].astype(str)
    return df


def forward_fill_macro(df: pd.DataFrame, date_col: str) -> pd.DataFrame:
    if df.empty or date_col not in df.columns:
        return df
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
    df = df.dropna(subset=[date_col]).sort_values(date_col).reset_index(drop=True)
    df = df.ffill()
    df[date_col] = df[date_col].dt.strftime('%Y-%m-%d')
    return df


def build_symbol_feature_frame(
    layer: TushareDataLayer,
    ts_code: str,
    start_date: str,
    end_date: str,
    macro_frames: dict[str, pd.DataFrame] | None = None,
    hsgt_frame: pd.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
    sentiment_data_path: str | None = None,
) -> pd.DataFrame:
    # Base: daily + daily_basic + adj_factor
    daily = layer.load_dataset("daily", symbol=ts_code, start_date=start_date, end_date=end_date)
    daily_basic = layer.load_dataset("daily_basic", symbol=ts_code, start_date=start_date, end_date=end_date)
    adj_factor = layer.load_dataset("adj_factor", symbol=ts_code, start_date=start_date, end_date=end_date)

    base = None
    for df in [daily, daily_basic, adj_factor]:
        if df.empty:
            continue
        df = normalize_date_column(df, "trade_date")
        if base is None:
            base = df
        else:
            base = base.merge(df, on="trade_date", how="outer", suffixes=('', '_dup'))
            dup_cols = [c for c in base.columns if c.endswith('_dup')]
            if dup_cols:
                base = base.drop(columns=dup_cols)

    if base is None or base.empty:
        return pd.DataFrame()

    base = base.sort_values("trade_date").reset_index(drop=True)
    # Deduplicate by trade_date
    base = base.drop_duplicates(subset=["trade_date"], keep="last")

    # Money flow
    mf = layer.load_dataset("moneyflow", symbol=ts_code, start_date=start_date, end_date=end_date)
    if not mf.empty:
        mf = normalize_date_column(mf, "trade_date")
        base = base.merge(mf, on="trade_date", how="left", suffixes=('', '_dup'))
        dup_cols = [c for c in base.columns if c.endswith('_dup')]
        if dup_cols:
            base = base.drop(columns=dup_cols)

    # Financial indicators (point-in-time via ann_date)
    fina = layer.load_dataset("fina_indicator", symbol=ts_code, start_date=start_date, end_date=end_date)
    if not fina.empty and "ann_date" in fina.columns:
        fina = normalize_date_column(fina, "ann_date")
        fina = fina.sort_values("ann_date").drop_duplicates(subset=["ann_date"], keep="last")
        fina_fields = STRATEGY_FAMILY_REGISTRY["value_quality"]["fields"].get("fina_indicator", []) + \
                       STRATEGY_FAMILY_REGISTRY["earnings_surprise"]["fields"].get("fina_indicator", []) + \
                       STRATEGY_FAMILY_REGISTRY["multi_factor"]["fields"].get("fina_indicator", [])
        fina_fields = list(dict.fromkeys(fina_fields))
        keep_cols = ["ann_date"] + [c for c in fina_fields if c in fina.columns]
        fina = fina[[c for c in keep_cols if c in fina.columns]]
        fina = fina.rename(columns={"ann_date": "trade_date"})
        fina = fina.ffill()
        base = base.merge(fina, on="trade_date", how="left", suffixes=('', '_fina'))
        dup_cols = [c for c in base.columns if c.endswith('_fina') and c.replace('_fina', '') in base.columns]
        if dup_cols:
            base = base.drop(columns=dup_cols)

    # Chip cost (cyq_perf)
    cyq = layer.load_dataset("cyq_perf", symbol=ts_code, start_date=start_date, end_date=end_date)
    if not cyq.empty:
        cyq = normalize_date_column(cyq, "trade_date")
        cyq_fields = STRATEGY_FAMILY_REGISTRY["chip_cost"]["fields"].get("cyq_perf", [])
        keep_cols = ["trade_date"] + [c for c in cyq_fields if c in cyq.columns]
        cyq = cyq[[c for c in keep_cols if c in cyq.columns]]
        base = base.merge(cyq, on="trade_date", how="left", suffixes=('', '_dup'))
        dup_cols = [c for c in base.columns if c.endswith('_dup')]
        if dup_cols:
            base = base.drop(columns=dup_cols)

    # Margin detail
    margin = layer.load_dataset("margin_detail", symbol=ts_code, start_date=start_date, end_date=end_date)
    if not margin.empty:
        margin = normalize_date_column(margin, "trade_date")
        margin_fields = STRATEGY_FAMILY_REGISTRY["margin_signal"]["fields"].get("margin_detail", [])
        keep_cols = ["trade_date"] + [c for c in margin_fields if c in margin.columns]
        margin = margin[[c for c in keep_cols if c in margin.columns]]
        base = base.merge(margin, on="trade_date", how="left", suffixes=('', '_dup'))
        dup_cols = [c for c in base.columns if c.endswith('_dup')]
        if dup_cols:
            base = base.drop(columns=dup_cols)

    # Northbound: hk_hold
    hk = layer.load_dataset("hk_hold", symbol=ts_code, start_date=start_date, end_date=end_date)
    if not hk.empty:
        hk = normalize_date_column(hk, "trade_date")
        hk_fields = ["vol", "ratio"]
        keep_cols = ["trade_date"] + [c for c in hk_fields if c in hk.columns]
        hk = hk[[c for c in keep_cols if c in hk.columns]]
        if "vol" in hk.columns:
            hk = hk.rename(columns={"vol": "hk_hold_vol"})
        if "ratio" in hk.columns:
            hk = hk.rename(columns={"ratio": "hk_hold_ratio"})
        base = base.merge(hk, on="trade_date", how="left", suffixes=('', '_dup'))
        dup_cols = [c for c in base.columns if c.endswith('_dup')]
        if dup_cols:
            base = base.drop(columns=dup_cols)
        base["hk_hold_vol"] = base.get("hk_hold_vol").ffill() if "hk_hold_vol" in base.columns else None
        base["hk_hold_ratio"] = base.get("hk_hold_ratio").ffill() if "hk_hold_ratio" in base.columns else None

    # Northbound: moneyflow_hsgt (aggregate, same for all stocks)
    if hsgt_frame is not None and not hsgt_frame.empty:
        hsgt = hsgt_frame.copy()
        hsgt = normalize_date_column(hsgt, "trade_date")
        rename_map = {}
        for c in ["hgt", "sgt", "north_money", "south_money", "ggt_ss", "ggt_sz"]:
            if c in hsgt.columns:
                rename_map[c] = f"hsgt_{c}"
        hsgt = hsgt.rename(columns=rename_map)
        base = base.merge(hsgt, on="trade_date", how="left", suffixes=('', '_dup'))
        dup_cols = [c for c in base.columns if c.endswith('_dup')]
        if dup_cols:
            base = base.drop(columns=dup_cols)

    # Forecast (point-in-time via ann_date)
    forecast = layer.load_dataset("forecast", symbol=ts_code, start_date=start_date, end_date=end_date)
    if not forecast.empty and "ann_date" in forecast.columns:
        forecast = normalize_date_column(forecast, "ann_date")
        forecast = forecast.sort_values("ann_date").drop_duplicates(subset=["ann_date"], keep="last")
        fc_fields = ["p_change_min", "p_change_max", "type", "summary"]
        keep_cols = ["ann_date"] + [c for c in fc_fields if c in forecast.columns]
        forecast = forecast[[c for c in keep_cols if c in forecast.columns]]
        forecast = forecast.rename(columns={"ann_date": "trade_date"})
        forecast = forecast.ffill()
        rename_map = {}
        for c in forecast.columns:
            if c != "trade_date":
                rename_map[c] = f"forecast_{c}"
        forecast = forecast.rename(columns=rename_map)
        base = base.merge(forecast, on="trade_date", how="left", suffixes=('', '_dup'))
        dup_cols = [c for c in base.columns if c.endswith('_dup')]
        if dup_cols:
            base = base.drop(columns=dup_cols)

    # Express (point-in-time via ann_date)
    express = layer.load_dataset("express", symbol=ts_code, start_date=start_date, end_date=end_date)
    if not express.empty and "ann_date" in express.columns:
        express = normalize_date_column(express, "ann_date")
        express = express.sort_values("ann_date").drop_duplicates(subset=["ann_date"], keep="last")
        ex_fields = ["diluted_eps", "diluted_roe", "yoy_net_profit", "n_income", "revenue", "operate_profit"]
        keep_cols = ["ann_date"] + [c for c in ex_fields if c in express.columns]
        express = express[[c for c in keep_cols if c in express.columns]]
        express = express.rename(columns={"ann_date": "trade_date"})
        express = express.ffill()
        rename_map = {}
        for c in express.columns:
            if c != "trade_date":
                rename_map[c] = f"express_{c}"
        express = express.rename(columns=rename_map)
        base = base.merge(express, on="trade_date", how="left", suffixes=('', '_dup'))
        dup_cols = [c for c in base.columns if c.endswith('_dup')]
        if dup_cols:
            base = base.drop(columns=dup_cols)

    # Analyst signals: report_rc (most recent snapshot, forward-fill)
    report_rc = layer.load_dataset("report_rc", symbol=ts_code)
    if not report_rc.empty:
        rc_date_col = "report_date" if "report_date" in report_rc.columns else "ann_date"
        if rc_date_col in report_rc.columns:
            report_rc = normalize_date_column(report_rc, rc_date_col)
            report_rc = report_rc.sort_values(rc_date_col).drop_duplicates(subset=[rc_date_col], keep="last")
            rc_fields = ["rating", "max_price", "min_price", "eps", "pe", "roe", "org_name"]
            keep_cols = [rc_date_col] + [c for c in rc_fields if c in report_rc.columns]
            report_rc = report_rc[[c for c in keep_cols if c in report_rc.columns]]
            report_rc = report_rc.rename(columns={rc_date_col: "trade_date"})
            report_rc = report_rc.ffill()
            rename_map = {}
            for c in report_rc.columns:
                if c != "trade_date":
                    rename_map[c] = f"report_rc_{c}"
            report_rc = report_rc.rename(columns=rename_map)
            base = base.merge(report_rc, on="trade_date", how="left", suffixes=('', '_dup'))
            dup_cols = [c for c in base.columns if c.endswith('_dup')]
            if dup_cols:
                base = base.drop(columns=dup_cols)

    # Analyst signals: top_inst (most recent snapshot, forward-fill)
    top_inst = layer.load_dataset("top_inst", symbol=ts_code)
    if not top_inst.empty and "trade_date" in top_inst.columns:
        top_inst = normalize_date_column(top_inst, "trade_date")
        top_inst = top_inst.sort_values("trade_date").drop_duplicates(subset=["trade_date"], keep="last")
        ti_fields = ["buy", "buy_rate", "sell", "sell_rate", "net_buy", "side"]
        keep_cols = ["trade_date"] + [c for c in ti_fields if c in top_inst.columns]
        top_inst = top_inst[[c for c in keep_cols if c in top_inst.columns]]
        top_inst = top_inst.ffill()
        rename_map = {}
        for c in top_inst.columns:
            if c != "trade_date":
                rename_map[c] = f"top_inst_{c}"
        top_inst = top_inst.rename(columns=rename_map)
        base = base.merge(top_inst, on="trade_date", how="left", suffixes=('', '_dup'))
        dup_cols = [c for c in base.columns if c.endswith('_dup')]
        if dup_cols:
            base = base.drop(columns=dup_cols)

    # Macro datasets (forward-filled, same values for all stocks)
    if macro_frames:
        for macro_name, macro_df in macro_frames.items():
            if macro_df.empty:
                continue
            rename_map = {}
            for c in macro_df.columns:
                if c != "trade_date":
                    rename_map[c] = f"{macro_name}_{c}"
            macro_renamed = macro_df.rename(columns=rename_map)
            base = base.merge(macro_renamed, on="trade_date", how="left", suffixes=('', '_dup'))
            dup_cols = [c for c in base.columns if c.endswith('_dup')]
            if dup_cols:
                base = base.drop(columns=dup_cols)

    # Sector code from index_weight
    if sector_map and ts_code in sector_map:
        base["sector_code"] = sector_map[ts_code]
    else:
        base["sector_code"] = None

    # Market sentiment data (V6: basis, PCR, VIX, margin ratio)
    sentiment_csv = Path(sentiment_data_path) if sentiment_data_path else None
    if sentiment_csv is None:
        sentiment_csv = Path(__file__).resolve().parents[1] / "Data" / "alternative" / "ashare-market-sentiment" / "sse" / "daily" / "market_sentiment.csv"
    if sentiment_csv.exists():
        sentiment_df = pd.read_csv(sentiment_csv)
        sentiment_df["trade_date"] = sentiment_df["trade_date"].astype(str).str.strip()
        # Normalize sentiment trade_date to match base format (YYYY-MM-DD)
        sentiment_df["trade_date"] = sentiment_df["trade_date"].str.replace(
            r"^(\d{4})(\d{2})(\d{2})$", r"\1-\2-\3", regex=True
        )
        # Add mkt_ prefix to avoid column name conflicts
        rename_map = {}
        for c in sentiment_df.columns:
            if c != "trade_date":
                rename_map[c] = f"mkt_{c}"
        sentiment_renamed = sentiment_df.rename(columns=rename_map)
        base = base.merge(sentiment_renamed, on="trade_date", how="left", suffixes=('', '_dup'))
        dup_cols = [c for c in base.columns if c.endswith('_dup')]
        if dup_cols:
            base = base.drop(columns=dup_cols)

    # Derived columns
    if "close" in base.columns and "pre_close" in base.columns:
        base["pct_chg"] = base.get("pct_chg", base["close"] / base["pre_close"] - 1)
    if all(c in base.columns for c in ["close", "vol", "amount"]):
        base["flow_ratio"] = base.get("flow_ratio", None)
        base["big_flow_ratio"] = base.get("big_flow_ratio", None)
        if "net_mf_amount" in base.columns and "amount" in base.columns:
            valid = base["amount"].astype(float) > 0
            base.loc[valid, "flow_ratio"] = (
                base.loc[valid, "net_mf_amount"].astype(float) / base.loc[valid, "amount"].astype(float)
            )
        if "buy_lg_amount" in base.columns and "sell_lg_amount" in base.columns and "amount" in base.columns:
            valid = base["amount"].astype(float) > 0
            lg_net = base.get("buy_lg_amount", 0).astype(float) - base.get("sell_lg_amount", 0).astype(float)
            base.loc[valid, "big_flow_ratio"] = lg_net[valid] / base.loc[valid, "amount"].astype(float)

    # Momentum derived columns
    if "close" in base.columns:
        close_series = base["close"].astype(float)
        base["momentum_120_20"] = None
        base["return_5"] = None
        base["volatility_20"] = None
        valid_idx = close_series.dropna().index
        if len(valid_idx) > 120:
            for i in range(120, len(base)):
                if i not in valid_idx:
                    continue
                c120 = close_series.iloc[max(0, i - 120)]
                c20 = close_series.iloc[max(0, i - 20)]
                c5 = close_series.iloc[max(0, i - 5)]
                c_now = close_series.iloc[i]
                if c120 > 0 and c20 > 0 and c5 > 0:
                    base.iloc[i, base.columns.get_loc("momentum_120_20")] = (c_now / c20 - 1) - (c20 / c120 - 1)
                    base.iloc[i, base.columns.get_loc("return_5")] = c_now / c5 - 1
                # Volatility 20-day
                if i >= 20:
                    rets = close_series.iloc[i - 20:i + 1].pct_change().dropna()
                    if len(rets) > 5:
                        base.iloc[i, base.columns.get_loc("volatility_20")] = rets.std()

    # Forward fill sparse columns
    ffill_candidates = ["hk_hold_vol", "hk_hold_ratio"] + \
        [c for c in base.columns if c.startswith(("forecast_", "express_", "report_rc_", "top_inst_"))]
    for c in ffill_candidates:
        if c in base.columns:
            base[c] = base[c].ffill()

    # Drop internal columns
    drop_cols = [c for c in base.columns if c in ("ts_code", "ts_code_dup", "trade_date_dup") or c.endswith(("_dup", "_fina"))]
    if drop_cols:
        base = base.drop(columns=drop_cols, errors='ignore')

    # Ensure trade_date is first column
    cols = ["trade_date"] + [c for c in base.columns if c != "trade_date"]
    base = base[cols]

    return base.sort_values("trade_date").reset_index(drop=True)


def build_sector_map(layer: TushareDataLayer) -> dict[str, str]:
    df = layer.load_dataset("index_weight")
    if df.empty or "con_code" not in df.columns:
        return {}
    sector_map = {}
    for _, row in df.iterrows():
        code = str(row.get("con_code", "")).strip()
        idx = str(row.get("index_code", "")).strip()
        if code:
            sector_map[code] = idx
    return sector_map


def build_macro_frames(layer: TushareDataLayer, start_date: str, end_date: str) -> dict[str, pd.DataFrame]:
    macro_frames = {}
    for ds_name in ["cn_cpi", "cn_gdp", "cn_m", "cn_pmi", "shibor", "shibor_lpr", "repo_daily"]:
        try:
            df = layer.load_dataset(ds_name, start_date=start_date, end_date=end_date)
        except Exception:
            df = pd.DataFrame()
        if df.empty:
            continue
        # Determine date column
        date_col = None
        for c in ["trade_date", "date", "month", "MONTH", "quarter"]:
            if c in df.columns:
                date_col = c
                break
        if date_col is None:
            continue
        df = normalize_date_column(df, date_col)
        # Select only the relevant fields from STRATEGY_FAMILY_REGISTRY
        fields = set()
        for family_info in STRATEGY_FAMILY_REGISTRY.values():
            fam_fields = family_info["fields"].get(ds_name, [])
            fields.update(fam_fields)
        keep_cols = [date_col] + [c for c in fields if c in df.columns]
        df = df[[c for c in keep_cols if c in df.columns]]
        df = df.rename(columns={date_col: "trade_date"})
        df = forward_fill_macro(df, "trade_date")
        macro_frames[ds_name] = df
    return macro_frames


def ts_code_to_feature_parts(ts_code: str) -> tuple[str, str]:
    ticker, suffix = ts_code.split('.')
    return ticker, 'sse' if suffix.upper() == 'SH' else 'szse'


def feature_daily_path(feature_data_path: str | Path, ts_code: str) -> Path:
    ticker, market = ts_code_to_feature_parts(ts_code)
    return Path(feature_data_path) / market / 'daily' / f'{ticker}.csv'


def export_feature_universe(config: dict) -> dict:
    catalog = build_multi_family_catalog()
    layer = TushareDataLayer(config['tushare-data-path'], {"datasets": {name: {"path": spec.path, "date_field": spec.date_field} | ({"symbol_field": spec.symbol_field} if spec.symbol_field else {}) for name, spec in catalog.items()}})
    universe_codes = load_index_constituents(layer, "000300.SH")
    if not universe_codes:
        universe_codes = ["600519.SH", "000858.SZ", "601318.SH", "600036.SH", "000651.SZ"]

    start_date = config['start-date']
    end_date = config['end-date']

    print(f"Loading macro datasets for {start_date}-{end_date}...")
    macro_frames = build_macro_frames(layer, start_date, end_date)

    print("Loading HSGT aggregate data...")
    hsgt_frame = layer.load_dataset("moneyflow_hsgt", start_date=start_date, end_date=end_date)

    print("Building sector map...")
    sector_map = build_sector_map(layer)

    print(f"Processing {len(universe_codes)} symbols...")
    exported_symbols = []
    skipped_symbols = []
    total_rows = 0

    for i, ts_code in enumerate(universe_codes):
        if i > 0 and i % 50 == 0:
            print(f"  {i}/{len(universe_codes)} symbols processed...")

        try:
            frame = build_symbol_feature_frame(
                layer, ts_code, start_date, end_date,
                macro_frames=macro_frames,
                hsgt_frame=hsgt_frame,
                sector_map=sector_map,
                sentiment_data_path=config.get("sentiment-data-path"),
            )
        except Exception as e:
            skipped_symbols.append({"symbol": ts_code, "reason": str(e)})
            continue

        if frame.empty:
            skipped_symbols.append({"symbol": ts_code, "reason": "no_data"})
            continue

        path = feature_daily_path(config['feature-data-path'], ts_code)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Replace NaN with empty string for LEAN ParseNullableDecimal → null
        frame_out = frame.copy()
        for c in frame_out.columns:
            if c == "trade_date":
                continue
            frame_out[c] = frame_out[c].apply(lambda v: '' if pd.isna(v) or v is None else v)

        frame_out.to_csv(path, index=False, float_format='%.10f')
        exported_symbols.append(ts_code)
        total_rows += len(frame)

    report = {
        'start_date': start_date,
        'end_date': end_date,
        'universe': config.get('universe', 'csi300'),
        'exported_symbol_count': len(exported_symbols),
        'skipped_symbol_count': len(skipped_symbols),
        'total_row_count': total_rows,
        'feature_data_path': config['feature-data-path'],
        'exported_symbols': exported_symbols,
        'skipped_symbols': skipped_symbols[:20],
    }

    report_file = config.get('report-file')
    if report_file:
        report_path = Path(report_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Export A-share multi-family feature data for LEAN backtests')
    parser.add_argument('--config')
    parser.add_argument('--start-date')
    parser.add_argument('--end-date')
    parser.add_argument('--feature-data-path')
    parser.add_argument('--universe', choices=['csi300', 'csi500', 'all'], default=None)
    parser.add_argument('--report-file')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    overrides = {
        'start-date': args.start_date,
        'end-date': args.end_date,
        'feature-data-path': args.feature_data_path,
        'universe': args.universe,
        'report-file': args.report_file,
    }
    report = export_feature_universe(load_pipeline_config(args.config, overrides))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())