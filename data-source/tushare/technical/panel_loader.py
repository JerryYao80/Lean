# data-source/tushare/technical/panel_loader.py
"""Load stk_factor_pro parquet into a TechnicalPanel (wide date x ts_code).

stk_factor_pro (261 cols) is downloaded per-ts_code by backfill_technical.py to
tushare_data_v2/stk_factor_pro/ts_code=<code>/data.parquet. This loader pivots
a curated subset of indicator columns to wide form for the build_day group pass.

Only qfq (前复权) variants are used for cross-sectional comparability (bfq/hfq
are for absolute-price technical analysis; qfq normalizes splits/dividends).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_TS_PATH = os.environ.get(
    "TUSHARE_DATA_PATH", "/home/project/tushare-downloader/tushare_data_v2"
)

# Curated indicator columns (qfq variants for cross-sectional comparability).
# Each maps to a factor_id written to result/factor-zoo/<id>/.
INDICATOR_COLS = {
    "macd_qfq": "tech_macd",
    "macd_dif_qfq": "tech_macd_dif",
    "macd_dea_qfq": "tech_macd_dea",
    "rsi_qfq_6": "tech_rsi_6",
    "rsi_qfq_12": "tech_rsi_12",
    "rsi_qfq_24": "tech_rsi_24",
    "kdj_k_qfq": "tech_kdj_k",
    "kdj_d_qfq": "tech_kdj_d",
    "kdj_qfq": "tech_kdj_j",
    "boll_upper_qfq": "tech_boll_upper",
    "boll_mid_qfq": "tech_boll_mid",
    "boll_lower_qfq": "tech_boll_lower",
    "bias1_qfq": "tech_bias1",
    "bias2_qfq": "tech_bias2",
    "bias3_qfq": "tech_bias3",
    "cci_qfq": "tech_cci",
    "wr_qfq": "tech_wr",
    "mfi_qfq": "tech_mfi",
    "mtm_qfq": "tech_mtm",
    "roc_qfq": "tech_roc",
    "obv_qfq": "tech_obv",
    "psy_qfq": "tech_psy",
    "trix_qfq": "tech_trix",
    "dpo_qfq": "tech_dpo",
    "cr_qfq": "tech_cr",
    "emv_qfq": "tech_emv",
    "mass_qfq": "tech_mass",
    "asi_qfq": "tech_asi",
    "bbi_qfq": "tech_bbi",
    "atr_qfq": "tech_atr",
    "vr_qfq": "tech_vr",
    "dmi_adx_qfq": "tech_dmi_adx",
    "dmi_pdi_qfq": "tech_dmi_pdi",
    "dmi_mdi_qfq": "tech_dmi_mdi",
    "expma_12_qfq": "tech_expma_12",
    "expma_50_qfq": "tech_expma_50",
    "ktn_upper_qfq": "tech_ktn_upper",
    "ktn_mid_qfq": "tech_ktn_mid",
    "ktn_down_qfq": "tech_ktn_down",
    "taq_up_qfq": "tech_taq_up",
    "taq_mid_qfq": "tech_taq_mid",
    "taq_down_qfq": "tech_taq_down",
    "dfma_dif_qfq": "tech_dfma_dif",
    "dfma_difma_qfq": "tech_dfma_difma",
    "xsii_td1_qfq": "tech_xsii_td1",
    "xsii_td2_qfq": "tech_xsii_td2",
    "xsii_td3_qfq": "tech_xsii_td3",
    "xsii_td4_qfq": "tech_xsii_td4",
}

# All factor_ids produced (used by catalog/tests).
FACTOR_IDS: list[str] = list(INDICATOR_COLS.values())


@dataclass
class TechnicalPanel:
    """Wide date x ts_code DataFrames, one per indicator factor_id."""
    wide: dict[str, pd.DataFrame]   # factor_id -> wide df (index=trade_date, cols=ts_code)
    asof: str                        # the build trade_date (compact YYYYMMDD)
    dates: list[str]


def _read_partition(data_root: str, ts_code: str) -> pd.DataFrame:
    p = Path(data_root) / "stk_factor_pro" / f"ts_code={ts_code}" / "data.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


def load_panel(ts_codes: list[str], asof: str, data_root: str | None = None,
               lookback_days: int = 60) -> TechnicalPanel | None:
    """Load stk_factor_pro for ts_codes, pivot each indicator to wide form.

    asof is compact YYYYMMDD; only rows with trade_date <= asof are kept,
    tail(lookback_days) per indicator (lookback not strictly needed since the
    indicator is already computed by tushare, but keeps memory bounded).
    """
    data_root = data_root or DEFAULT_TS_PATH
    if not ts_codes:
        return None

    # Concatenate per-ts_code long frames.
    frames: list[pd.DataFrame] = []
    for code in ts_codes:
        df = _read_partition(data_root, code)
        if df.empty:
            continue
        df = df.copy()
        df["trade_date"] = df["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
        df = df[df["trade_date"] <= asof]
        frames.append(df)
    if not frames:
        return None
    long = pd.concat(frames, ignore_index=True)
    long = long[long["trade_date"] <= asof].sort_values("trade_date")

    wide: dict[str, pd.DataFrame] = {}
    all_dates: list[str] = []
    for col, fid in INDICATOR_COLS.items():
        if col not in long.columns:
            wide[fid] = pd.DataFrame()
            continue
        sub = long[["trade_date", "ts_code", col]].copy()
        sub[col] = pd.to_numeric(sub[col], errors="coerce")
        w = sub.pivot(index="trade_date", columns="ts_code", values=col)
        w = w.tail(lookback_days)
        wide[fid] = w
        if not all_dates:
            all_dates = list(w.index)

    return TechnicalPanel(wide=wide, asof=asof, dates=all_dates)
