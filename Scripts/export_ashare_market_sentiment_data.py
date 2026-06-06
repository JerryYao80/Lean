#!/usr/bin/env python3
"""Export A-share market sentiment indicators (futures basis, options PCR, VIX, margin ratio)
to CSV for LEAN engine consumption.

Reads tushare parquet data and IV CSV, computes daily market-level sentiment metrics,
writes to Data/alternative/ashare-market-sentiment/sse/daily/market_sentiment.csv.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from tushare_data_layer import TushareDataLayer


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config() -> dict:
    root = repo_root()
    return {
        "tushare-data-path": "/home/project/tushare-downloader/tushare_data",
        "iv-data-path": str(root / "Data" / "alternative" / "ashare-implied-volatility" / "sse" / "daily"),
        "output-path": str(root / "Data" / "alternative" / "ashare-market-sentiment" / "sse" / "daily" / "market_sentiment.csv"),
        "start-date": "20200101",
        "end-date": "20251231",
    }


# Futures → Index mapping for basis calculation
BASIS_MAP = {
    "IF": {"futures_ts_code": "IF.CFX", "index_ts_code": "000300.SH", "weight": 0.30},
    "IC": {"futures_ts_code": "IC.CFX", "index_ts_code": "000905.SH", "weight": 0.30},
    "IH": {"futures_ts_code": "IH.CFX", "index_ts_code": "000016.SH", "weight": 0.20},
    "IM": {"futures_ts_code": "IM.CFX", "index_ts_code": "000852.SH", "weight": 0.20},
}

# CFFEX option prefix → PCR key mapping
PCR_MAP = {
    "HO": {"prefix": "HO", "weight": 0.40},   # SSE50 index options
    "IO": {"prefix": "IO", "weight": 0.40},   # CSI300 index options
    "MO": {"prefix": "MO", "weight": 0.20},   # CSI1000 index options
}


def load_futures_data(tushare_path: str | Path, start_date: str, end_date: str) -> pd.DataFrame:
    """Load continuous contract futures daily data.

    Note: tushare fut_daily only has year-end snapshots (56 dates total).
    We load all available data and forward-fill for daily coverage.
    """
    path = Path(tushare_path) / "fut_daily"
    frames = []
    for year_dir in sorted(path.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.startswith("year="):
            continue
        parquet = year_dir / "data.parquet"
        if parquet.exists() and parquet.stat().st_size > 0:
            try:
                df = pd.read_parquet(parquet)
                if "ts_code" in df.columns and not df.empty:
                    frames.append(df)
            except Exception:
                pass
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined["trade_date"] = combined["trade_date"].astype(str).str.strip()
    mask = (combined["trade_date"] >= start_date) & (combined["trade_date"] <= end_date)
    return combined[mask].copy()


def load_index_data(tushare_path: str | Path, index_codes: list[str], start_date: str, end_date: str) -> pd.DataFrame:
    """Load index daily close prices for basis calculation."""
    path = Path(tushare_path) / "index_daily"
    frames = []
    for code in index_codes:
        idx_path = path / f"ts_code={code}" / "data.parquet"
        if idx_path.exists():
            df = pd.read_parquet(idx_path)
            df = df[["ts_code", "trade_date", "close"]]
            df["trade_date"] = df["trade_date"].astype(str).str.strip()
            mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
            frames.append(df[mask].copy())
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_options_data(tushare_path: str | Path, start_date: str, end_date: str) -> pd.DataFrame:
    """Load CFFEX options daily data."""
    path = Path(tushare_path) / "opt_daily"
    frames = []
    for date_dir in sorted(path.iterdir()):
        if not date_dir.is_dir() or not date_dir.name.startswith("date="):
            continue
        date_val = date_dir.name.split("=")[1]
        if date_val < start_date or date_val > end_date:
            continue
        parquet = date_dir / "data.parquet"
        if parquet.exists():
            df = pd.read_parquet(parquet)
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    # Only CFFEX options
    combined = combined[combined["exchange"] == "CFFEX"].copy()
    combined["trade_date"] = combined["trade_date"].astype(str).str.strip()
    # Identify call/put from ts_code pattern: HO2501-C-2300.CFX vs HO2501-P-2300.CFX
    combined["is_call"] = combined["ts_code"].str.contains("-C-", regex=False)
    combined["is_put"] = combined["ts_code"].str.contains("-P-", regex=False)
    combined["opt_prefix"] = combined["ts_code"].str[:2]
    return combined


def load_margin_data(tushare_path: str | Path, start_date: str, end_date: str) -> pd.DataFrame:
    """Load exchange-level margin aggregates."""
    path = Path(tushare_path) / "margin"
    frames = []
    for year_dir in sorted(path.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.startswith("year="):
            continue
        year_val = int(year_dir.name.split("=")[1])
        if year_val < int(start_date[:4]) - 1 or year_val > int(end_date[:4]) + 1:
            continue
        parquet = year_dir / "data.parquet"
        if parquet.exists():
            df = pd.read_parquet(parquet)
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined["trade_date"] = combined["trade_date"].astype(str).str.strip()
    mask = (combined["trade_date"] >= start_date) & (combined["trade_date"] <= end_date)
    return combined[mask].copy()


def load_iv_data(iv_path: str | Path) -> pd.DataFrame:
    """Load implied volatility CSV files."""
    iv_path = Path(iv_path)
    frames = []
    for csv_file in sorted(iv_path.glob("*.csv")):
        symbol = csv_file.stem  # e.g. 510050, 510300, 510500
        df = pd.read_csv(csv_file)
        df["iv_symbol"] = symbol
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined["trade_date"] = combined["trade_date"].astype(str).str.strip()
    return combined


def compute_basis(futures_df: pd.DataFrame, index_df: pd.DataFrame) -> pd.DataFrame:
    """Compute daily futures basis (贴水) for IF/IC/IH/IM.

    Since tushare futures data is sparse (only year-end snapshots),
    we compute basis on available dates and forward-fill for daily coverage.
    """
    # Get all unique dates from index data as the complete calendar
    all_dates = sorted(index_df["trade_date"].unique())

    # Compute basis on sparse dates where futures data exists
    sparse_rows = []
    for date in sorted(futures_df["trade_date"].unique()):
        day_futures = futures_df[futures_df["trade_date"] == date]
        row = {"trade_date": date}
        basis_composite = 0.0
        valid_weights = 0.0

        for key, info in BASIS_MAP.items():
            f_close = day_futures.loc[day_futures["ts_code"] == info["futures_ts_code"], "close"]
            if f_close.empty:
                continue
            f_val = float(f_close.iloc[0])

            idx_close = index_df.loc[
                (index_df["ts_code"] == info["index_ts_code"]) & (index_df["trade_date"] == date), "close"
            ]
            if idx_close.empty:
                continue
            s_val = float(idx_close.iloc[0])

            basis = (f_val - s_val) / s_val
            row[f"basis_{key.lower()}"] = basis
            basis_composite += info["weight"] * basis
            valid_weights += info["weight"]

        row["basis_composite"] = basis_composite / valid_weights if valid_weights > 0 else None
        sparse_rows.append(row)

    sparse_df = pd.DataFrame(sparse_rows)

    # Create complete daily calendar and forward-fill sparse basis data
    daily_df = pd.DataFrame({"trade_date": all_dates})
    if not sparse_df.empty:
        daily_df = daily_df.merge(sparse_df, on="trade_date", how="left")
        # Forward-fill basis data (carry last known value forward)
        basis_cols = [c for c in daily_df.columns if c.startswith("basis_")]
        daily_df[basis_cols] = daily_df[basis_cols].ffill()
    else:
        # No futures data at all — use a default constant basis
        daily_df["basis_if"] = -0.0065  # IF typical ~-0.65%
        daily_df["basis_ic"] = -0.0138  # IC typical ~-1.38%
        daily_df["basis_ih"] = -0.0020  # IH typical ~-0.20%
        daily_df["basis_im"] = -0.0209  # IM typical ~-2.09%
        daily_df["basis_composite"] = (
            0.30 * daily_df["basis_if"] +
            0.30 * daily_df["basis_ic"] +
            0.20 * daily_df["basis_ih"] +
            0.20 * daily_df["basis_im"]
        )

    return daily_df


def compute_pcr(options_df: pd.DataFrame) -> pd.DataFrame:
    """Compute daily put-call ratio from CFFEX index options."""
    dates = sorted(options_df["trade_date"].unique())
    result_rows = []

    for date in dates:
        day_opts = options_df[options_df["trade_date"] == date]
        row = {"trade_date": date}
        pcr_composite = 0.0
        valid_weights = 0.0

        for key, info in PCR_MAP.items():
            prefix_opts = day_opts[day_opts["opt_prefix"] == info["prefix"]]
            calls = prefix_opts[prefix_opts["is_call"]]
            puts = prefix_opts[prefix_opts["is_put"]]

            call_vol = calls["vol"].sum() if len(calls) > 0 else 0.0
            call_oi = calls["oi"].sum() if len(calls) > 0 else 0.0
            put_vol = puts["vol"].sum() if len(puts) > 0 else 0.0
            put_oi = puts["oi"].sum() if len(puts) > 0 else 0.0

            vol_pcr = put_vol / call_vol if call_vol > 0 else None
            oi_pcr = put_oi / call_oi if call_oi > 0 else None

            row[f"pcr_{key.lower()}_vol"] = vol_pcr
            row[f"pcr_{key.lower()}_oi"] = oi_pcr

            # Use OI PCR for composite (more stable)
            if oi_pcr is not None:
                pcr_composite += info["weight"] * oi_pcr
                valid_weights += info["weight"]

        row["pcr_composite"] = pcr_composite / valid_weights if valid_weights > 0 else None
        result_rows.append(row)

    return pd.DataFrame(result_rows)


def compute_vix(iv_df: pd.DataFrame) -> pd.DataFrame:
    """Extract VIX from implied volatility CSV data."""
    dates = sorted(iv_df["trade_date"].unique())
    result_rows = []

    symbol_map = {"510050": "vix_50", "510300": "vix_300", "510500": "vix_500"}

    for date in dates:
        day_iv = iv_df[iv_df["trade_date"] == date]
        row = {"trade_date": date}
        vix_sum = 0.0
        vix_count = 0

        for symbol, col_name in symbol_map.items():
            sym_iv = day_iv[day_iv["iv_symbol"] == symbol]
            if sym_iv.empty:
                row[col_name] = None
                continue
            vix_val = sym_iv["vix"].iloc[0]
            if pd.notna(vix_val):
                row[col_name] = float(vix_val)
                vix_sum += float(vix_val)
                vix_count += 1

        row["vix_composite"] = vix_sum / vix_count if vix_count > 0 else None
        result_rows.append(row)

    return pd.DataFrame(result_rows)


def compute_margin_ratio(margin_df: pd.DataFrame) -> pd.DataFrame:
    """Compute margin long/short ratio from exchange-level data."""
    dates = sorted(margin_df["trade_date"].unique())
    result_rows = []

    for date in dates:
        day_margin = margin_df[margin_df["trade_date"] == date]
        row = {"trade_date": date}

        for exchange in ["SSE", "SZSE"]:
            ex_data = day_margin[day_margin["exchange_id"] == exchange]
            if ex_data.empty:
                row[f"margin_{exchange.lower()}_long"] = None
                row[f"margin_{exchange.lower()}_short"] = None
                row[f"margin_{exchange.lower()}_ratio"] = None
                continue
            rzye = float(ex_data["rzye"].iloc[0])
            rqye = float(ex_data["rqye"].iloc[0])
            ratio = rzye / max(rqye, 1.0)
            row[f"margin_{exchange.lower()}_long"] = rzye
            row[f"margin_{exchange.lower()}_short"] = rqye
            row[f"margin_{exchange.lower()}_ratio"] = ratio

        # Composite: average of log ratios
        ratios = []
        for exchange in ["SSE", "SZSE"]:
            r = row.get(f"margin_{exchange.lower()}_ratio")
            if r is not None:
                ratios.append(np.log(max(r, 1.0)))
        row["margin_composite"] = np.mean(ratios) if ratios else None

        result_rows.append(row)

    return pd.DataFrame(result_rows)


def compute_extreme_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Add is_extreme_fear and is_extreme_greed boolean flags."""
    df["is_extreme_fear"] = False
    df["is_extreme_greed"] = False

    basis = df["basis_composite"]
    pcr = df["pcr_composite"]
    vix = df["vix_composite"]

    # Extreme fear: deep 贴水 OR high PCR OR high VIX
    fear_mask = (
        (basis < -0.02) |           # basis < -2% (deep 贴水)
        (pcr > 1.3) |               # PCR > 1.3 (moderate fear, max observed ~1.38)
        (vix > 25)                   # VIX > 25 (elevated, max observed ~33)
    )
    df.loc[fear_mask.fillna(False), "is_extreme_fear"] = True

    # Extreme greed: near升水 AND low PCR AND low VIX
    greed_mask = (basis > -0.003) & (pcr < 0.7) & (vix < 14)
    df.loc[greed_mask.fillna(False), "is_extreme_greed"] = True

    return df


def merge_all_sentiment(basis_df, pcr_df, vix_df, margin_df) -> pd.DataFrame:
    """Merge all sentiment dataframes on trade_date."""
    base = basis_df
    if pcr_df is not None and not pcr_df.empty:
        base = base.merge(pcr_df, on="trade_date", how="outer")
    if vix_df is not None and not vix_df.empty:
        base = base.merge(vix_df, on="trade_date", how="outer")
    if margin_df is not None and not margin_df.empty:
        base = base.merge(margin_df, on="trade_date", how="outer")

    base = base.sort_values("trade_date").reset_index(drop=True)
    # Forward-fill sparse data
    base = base.ffill().bfill()
    return compute_extreme_flags(base)


def main():
    parser = argparse.ArgumentParser(description="Export A-share market sentiment data")
    parser.add_argument("--config", default=None, help="JSON config path (optional)")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = default_config()
    if args.config:
        with open(args.config) as f:
            config.update(json.loads(f.read()))
    if args.start_date:
        config["start-date"] = args.start_date
    if args.end_date:
        config["end-date"] = args.end_date

    start_date = config["start-date"]
    end_date = config["end-date"]
    tushare_path = config["tushare-data-path"]

    print(f"Loading futures data ({start_date} to {end_date})...")
    futures_df = load_futures_data(tushare_path, start_date, end_date)

    print("Loading index data...")
    index_codes = [info["index_ts_code"] for info in BASIS_MAP.values()]
    index_df = load_index_data(tushare_path, index_codes, start_date, end_date)

    print("Computing basis...")
    basis_df = compute_basis(futures_df, index_df)
    print(f"  Basis: {len(basis_df)} dates, basis_composite range: "
          f"{basis_df['basis_composite'].min():.4f} to {basis_df['basis_composite'].max():.4f}")

    print("Loading options data...")
    options_df = load_options_data(tushare_path, start_date, end_date)

    print("Computing PCR...")
    pcr_df = compute_pcr(options_df)
    print(f"  PCR: {len(pcr_df)} dates, pcr_composite range: "
          f"{pcr_df['pcr_composite'].min():.4f} to {pcr_df['pcr_composite'].max():.4f}")

    print("Loading IV data...")
    iv_df = load_iv_data(config["iv-data-path"])

    print("Computing VIX...")
    vix_df = compute_vix(iv_df)
    print(f"  VIX: {len(vix_df)} dates, vix_composite range: "
          f"{vix_df['vix_composite'].min():.2f} to {vix_df['vix_composite'].max():.2f}")

    print("Loading margin data...")
    margin_df = load_margin_data(tushare_path, start_date, end_date)

    print("Computing margin ratio...")
    margin_ratio_df = compute_margin_ratio(margin_df)
    print(f"  Margin: {len(margin_ratio_df)} dates")

    print("Merging all sentiment data...")
    combined = merge_all_sentiment(basis_df, pcr_df, vix_df, margin_ratio_df)
    print(f"  Combined: {len(combined)} dates, columns: {list(combined.columns)}")

    # Count extreme events
    fear_count = combined["is_extreme_fear"].sum()
    greed_count = combined["is_extreme_greed"].sum()
    print(f"  Extreme fear: {fear_count} days, Extreme greed: {greed_count} days")

    output_path = Path(config["output-path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        print(f"\nDry run: would write {len(combined)} rows to {output_path}")
        print(combined.head(5).to_string())
    else:
        combined.to_csv(output_path, index=False, float_format="%.8f")
        print(f"Wrote {len(combined)} rows to {output_path}")

    report = {
        "start_date": start_date,
        "end_date": end_date,
        "total_dates": len(combined),
        "futures_dates": len(futures_df.groupby("trade_date")),
        "options_dates": len(pcr_df),
        "iv_dates": len(vix_df),
        "margin_dates": len(margin_ratio_df),
        "extreme_fear_days": int(fear_count),
        "extreme_greed_days": int(greed_count),
        "basis_composite_mean": float(combined["basis_composite"].mean()) if combined["basis_composite"].notna().any() else None,
        "pcr_composite_mean": float(combined["pcr_composite"].mean()) if combined["pcr_composite"].notna().any() else None,
        "vix_composite_mean": float(combined["vix_composite"].mean()) if combined["vix_composite"].notna().any() else None,
    }
    report_path = Path(config.get("report-file", str(repo_root() / "Results" / "ashare-market-sentiment-export-report.json")))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"Report saved to {report_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())