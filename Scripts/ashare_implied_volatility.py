"""A-share implied volatility calculator using Tushare options data.

Computes Black-Scholes implied volatility for 50ETF/300ETF options and a
VIX-like model-free implied volatility index.  Writes results to InfluxDB
for Grafana visualization.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib import error, parse, request
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.stats import norm

CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_TUSHARE_DATA_PATH = "/home/project/tushare-downloader/tushare_data_v2"
DEFAULT_INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
DEFAULT_INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "lean")
DEFAULT_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "quant")
DEFAULT_INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN")

IV_MEASUREMENT = "lean_ashare_iv"
VIX_MEASUREMENT = "lean_ashare_vix"
SKEW_MEASUREMENT = "lean_ashare_iv_skew"

# Map opt_code (underlying) to fund_daily ts_code
UNDERLYING_MAP = {
    "OP510050.SH": "510050.SH",
    "OP510300.SH": "510300.SH",
    "OP510500.SH": "510500.SH",
}

FRIENDLY_NAMES = {
    "OP510050.SH": "50ETF",
    "OP510300.SH": "300ETF",
    "OP510500.SH": "500ETF",
}

TRADING_DAYS_PER_YEAR = 242


# ---------------------------------------------------------------------------
# Black-Scholes helpers
# ---------------------------------------------------------------------------

def bs_d1(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    return (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))


def bs_d2(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    return bs_d1(S, K, T, r, sigma, q) - sigma * math.sqrt(T)


def bs_call_price(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    if T <= 0:
        return max(S - K, 0.0)
    d1 = bs_d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)
    return S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    if T <= 0:
        return max(K - S, 0.0)
    d1 = bs_d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp(-q * T) * norm.cdf(-d1)


def bs_vega(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = bs_d1(S, K, T, r, sigma, q)
    return S * math.exp(-q * T) * norm.pdf(d1) * math.sqrt(T)


def implied_vol_newton(
    option_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    call_put: str,
    q: float = 0.0,
    max_iter: int = 50,
    tol: float = 1e-8,
) -> float | None:
    """Compute implied volatility via Newton-Raphson."""
    if T <= 1e-10 or option_price <= 0:
        return None

    intrinsic = max(S - K, 0.0) if call_put == "C" else max(K - S, 0.0)
    if option_price < intrinsic - 0.0001:
        return None

    sigma = 0.3  # initial guess
    for _ in range(max_iter):
        if call_put == "C":
            model_price = bs_call_price(S, K, T, r, sigma, q)
        else:
            model_price = bs_put_price(S, K, T, r, sigma, q)

        diff = model_price - option_price
        if abs(diff) < tol:
            return sigma

        vega = bs_vega(S, K, T, r, sigma, q)
        if vega < 1e-12:
            return None

        sigma -= diff / vega
        if sigma <= 0:
            sigma = 0.001
        if sigma > 5.0:
            return None

    # didn't converge within tolerance but close enough
    if abs(diff) < option_price * 0.01:
        return sigma
    return None


# ---------------------------------------------------------------------------
# VIX-like model-free implied volatility
# ---------------------------------------------------------------------------

def compute_vix_for_term(
    options_df: pd.DataFrame,
    F: float,
    T: float,
    r: float,
) -> float | None:
    """Compute model-free variance for a single term using CBOE VIX methodology.

    σ² = (2/T) Σ [ΔK / K²] exp(R * T) Q(K) - (1/T) [F/K₀ - 1]²

    where:
    - K₀ is the strike immediately below F
    - ΔK is half the distance between adjacent strikes
    - Q(K) is the mid-quote for OTM options at strike K
    """
    if T <= 0 or F <= 0:
        return None

    strikes = sorted(options_df["exercise_price"].unique())
    if len(strikes) < 2:
        return None

    # Find K0: highest strike below F
    k0 = 0.0
    for k in strikes:
        if k <= F:
            k0 = k
    if k0 == 0:
        k0 = strikes[0]

    contribution = 0.0
    for idx, k in enumerate(strikes):
        # Compute ΔK
        if idx == 0:
            delta_k = strikes[1] - strikes[0]
        elif idx == len(strikes) - 1:
            delta_k = strikes[-1] - strikes[-2]
        else:
            delta_k = (strikes[idx + 1] - strikes[idx - 1]) / 2.0

        # Select OTM option price
        if k < k0:
            row = options_df[(options_df["exercise_price"] == k) & (options_df["call_put"] == "P")]
        elif k > k0:
            row = options_df[(options_df["exercise_price"] == k) & (options_df["call_put"] == "C")]
        else:
            # At K0, use average of call and put
            put_row = options_df[(options_df["exercise_price"] == k) & (options_df["call_put"] == "P")]
            call_row = options_df[(options_df["exercise_price"] == k) & (options_df["call_put"] == "C")]
            if not put_row.empty and not call_row.empty:
                put_price = put_row.iloc[0]["settle"]
                call_price = call_row.iloc[0]["settle"]
                q_k = (put_price + call_price) / 2.0
            elif not call_row.empty:
                q_k = call_row.iloc[0]["settle"]
            elif not put_row.empty:
                q_k = put_row.iloc[0]["settle"]
            else:
                continue
            contribution += (delta_k / (k * k)) * math.exp(r * T) * q_k
            continue

        if row.empty:
            continue
        q_k = row.iloc[0]["settle"]
        if q_k is None or pd.isna(q_k) or q_k <= 0:
            continue

        contribution += (delta_k / (k * k)) * math.exp(r * T) * q_k

    variance = (2.0 / T) * contribution - (1.0 / T) * (F / k0 - 1.0) ** 2
    if variance <= 0:
        return None
    return variance


def find_forward_price(options_df: pd.DataFrame, r: float, T: float) -> float | None:
    """Find forward price F using put-call parity at the strike where
    absolute call-put price difference is smallest."""
    call_prices = options_df[options_df["call_put"] == "C"][["exercise_price", "settle"]].rename(columns={"settle": "call_settle"})
    put_prices = options_df[options_df["call_put"] == "P"][["exercise_price", "settle"]].rename(columns={"settle": "put_settle"})

    merged = call_prices.merge(put_prices, on="exercise_price")
    if merged.empty:
        return None

    # Filter for valid prices
    merged = merged[(merged["call_settle"] > 0) & (merged["put_settle"] > 0)]
    if merged.empty:
        return None

    # Find minimum absolute difference (most ATM)
    merged["cp_diff"] = (merged["call_settle"] - merged["put_settle"]).abs()
    atm = merged.loc[merged["cp_diff"].idxmin()]

    # F = K + e^(RT) * (C - P)
    F = atm["exercise_price"] + math.exp(r * T) * (atm["call_settle"] - atm["put_settle"])
    return F if F > 0 else None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def normalize_trade_date(value) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    if not text:
        return ""
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    if re.fullmatch(r"\d{8}", text):
        return text
    parsed = pd.to_datetime(text, errors="coerce")
    if not pd.isna(parsed):
        return parsed.strftime("%Y%m%d")
    digits = re.sub(r"\D", "", text)
    return digits[:8] if len(digits) >= 8 else ""


def trade_date_to_timestamp_ns(trade_date: str) -> int:
    normalized = normalize_trade_date(trade_date)
    local_close = datetime(
        int(normalized[0:4]),
        int(normalized[4:6]),
        int(normalized[6:8]),
        15, 0, 0,
        tzinfo=CHINA_TZ,
    )
    utc_close = local_close.astimezone(timezone.utc)
    return int(utc_close.timestamp() * 1_000_000_000)


def to_float(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def load_opt_basic(tushare_data_path: str) -> pd.DataFrame:
    path = Path(tushare_data_path) / "opt_basic" / "data.parquet"
    if not path.exists():
        raise FileNotFoundError(f"opt_basic not found at {path}")
    df = pd.read_parquet(path)
    df["maturity_date"] = df["maturity_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    df["list_date"] = df["list_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    df["delist_date"] = df["delist_date"].astype(str).str.replace(r"\.0$", "", regex=True)
    return df


def load_opt_daily(tushare_data_path: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    opt_dir = Path(tushare_data_path) / "opt_daily"
    frames = []
    for date_dir in sorted(opt_dir.glob("trade_date=*")):
        date_str = date_dir.name.removeprefix("trade_date=")
        if start_date and date_str < start_date:
            continue
        if end_date and date_str > end_date:
            continue
        parquet = date_dir / "data.parquet"
        if not parquet.exists():
            continue
        df = pd.read_parquet(parquet)
        if df.empty:
            continue
        frames.append(df)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    # Filter to SSE only (we don't have SZSE opt_basic)
    if "exchange" in combined.columns:
        combined = combined[combined["exchange"] == "SSE"]
    return combined


def load_underlying_price(tushare_data_path: str, fund_ts_code: str) -> pd.DataFrame:
    path = Path(tushare_data_path) / "fund_daily" / f"ts_code={fund_ts_code}" / "data.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df["trade_date"] = df["trade_date"].map(normalize_trade_date)
    return df[["trade_date", "close"]].rename(columns={"close": "underlying_close"})


def load_shibor(tushare_data_path: str) -> pd.DataFrame:
    """Load SHIBOR rates for risk-free rate."""
    shibor_dir = Path(tushare_data_path) / "shibor"
    frames = []
    for year_dir in sorted(shibor_dir.glob("year=*")):
        parquet = year_dir / "data.parquet"
        if not parquet.exists():
            continue
        df = pd.read_parquet(parquet)
        if df.empty:
            continue
        frames.append(df)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined["date"] = combined["date"].map(normalize_trade_date)
    return combined


def get_risk_free_rate(shibor_df: pd.DataFrame, trade_date: str, tenor: str = "1y") -> float:
    """Get risk-free rate for a given date. Default to 1y SHIBOR."""
    row = shibor_df[shibor_df["date"] == trade_date]
    if row.empty:
        return 0.02  # fallback
    val = to_float(row.iloc[0].get(tenor))
    if val is None:
        return 0.02
    return val / 100.0  # SHIBOR is in percent


def days_to_maturity(trade_date: str, maturity_date: str) -> int:
    """Calendar days from trade_date to maturity_date."""
    td = datetime.strptime(trade_date, "%Y%m%d")
    md = datetime.strptime(maturity_date, "%Y%m%d")
    return (md - td).days


# ---------------------------------------------------------------------------
# IV computation pipeline
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IvResult:
    underlying: str
    trade_date: str
    atm_iv: float  # ATM implied volatility (annualized)
    iv_call_25delta: float | None  # 25-delta call IV
    iv_put_25delta: float | None  # 25-delta put IV
    skew: float | None  # 25-delta risk reversal
    term_days_near: int  # near-term days to expiry
    term_days_next: int  # next-term days to expiry
    option_count: int  # number of options used


@dataclass(frozen=True)
class VixResult:
    underlying: str
    trade_date: str
    vix: float  # 30-day model-free IV (annualized, percent)
    sigma_near: float  # near-term volatility
    sigma_next: float  # next-term volatility
    t_near: float  # near-term time to expiry (years)
    t_next: float  # next-term time to expiry (years)


def compute_daily_iv(
    trade_date: str,
    opt_basic: pd.DataFrame,
    opt_daily_date: pd.DataFrame,
    underlying_price: float,
    risk_free_rate: float,
    underlying_code: str,
) -> tuple[IvResult | None, VixResult | None]:
    """Compute IV metrics for a single trade date and underlying."""

    # Get contract specs for this underlying
    contracts = opt_basic[opt_basic["opt_code"] == underlying_code].copy()
    if contracts.empty:
        return None, None

    # Filter opt_daily to SSE contracts that are in our basic list
    valid_ts_codes = set(contracts["ts_code"].values)
    daily = opt_daily_date[opt_daily_date["ts_code"].isin(valid_ts_codes)].copy()
    if daily.empty:
        return None, None

    # Merge with basic info
    merged = daily.merge(
        contracts[["ts_code", "call_put", "exercise_price", "maturity_date", "per_unit"]],
        on="ts_code",
        how="left",
    )
    merged = merged[merged["exercise_price"].notna() & merged["maturity_date"].notna()]
    if merged.empty:
        return None, None

    merged["dte"] = merged["maturity_date"].apply(lambda m: days_to_maturity(trade_date, str(m)))
    merged = merged[merged["dte"] > 0]
    if merged.empty:
        return None, None

    # Find near-term (23-37 days) and next-term expirations
    all_dtes = sorted(merged["dte"].unique())
    near_dte = None
    next_dte = None
    for d in all_dtes:
        if 23 <= d <= 37:
            if near_dte is None:
                near_dte = d
            elif next_dte is None and d > near_dte:
                next_dte = d
                break

    # Fallback: use closest two expirations
    if near_dte is None:
        near_dte = all_dtes[0] if len(all_dtes) > 0 else None
    if next_dte is None:
        for d in all_dtes:
            if d > (near_dte or 0):
                next_dte = d
                break

    if near_dte is None:
        return None, None

    # Compute ATM IV from near-term options
    near_options = merged[merged["dte"] == near_dte].copy()
    T_near = near_dte / TRADING_DAYS_PER_YEAR
    atm_iv = compute_atm_iv(near_options, underlying_price, T_near, risk_free_rate)

    # Compute 25-delta IVs for skew
    iv_call_25d, iv_put_25d = compute_25delta_iv(near_options, underlying_price, T_near, risk_free_rate)
    if iv_call_25d is not None and iv_put_25d is not None:
        skew = iv_put_25d - iv_call_25d
    else:
        skew = None

    iv_result = IvResult(
        underlying=FRIENDLY_NAMES.get(underlying_code, underlying_code),
        trade_date=trade_date,
        atm_iv=atm_iv or 0.0,
        iv_call_25delta=iv_call_25d,
        iv_put_25delta=iv_put_25d,
        skew=skew,
        term_days_near=near_dte,
        term_days_next=next_dte or 0,
        option_count=len(near_options),
    )

    # Compute VIX-like index
    vix_result = None
    if next_dte is not None:
        next_options = merged[merged["dte"] == next_dte].copy()
        T_next = next_dte / TRADING_DAYS_PER_YEAR

        # Near-term
        F_near = find_forward_price(near_options, risk_free_rate, T_near)
        var_near = None
        if F_near is not None:
            var_near = compute_vix_for_term(near_options, F_near, T_near, risk_free_rate)

        # Next-term
        F_next = find_forward_price(next_options, risk_free_rate, T_next)
        var_next = None
        if F_next is not None:
            var_next = compute_vix_for_term(next_options, F_next, T_next, risk_free_rate)

        # Interpolate to 30 days
        if var_near is not None and var_next is not None and T_near > 0 and T_next > 0:
            t30 = 30.0 / TRADING_DAYS_PER_YEAR
            w1 = (T_next - t30) / (T_next - T_near)
            w2 = 1.0 - w1
            sigma_30_sq = w1 * var_near * (T_near / t30) + w2 * var_next * (T_next / t30)
            vix_value = math.sqrt(sigma_30_sq) * 100.0  # annualized percent

            vix_result = VixResult(
                underlying=FRIENDLY_NAMES.get(underlying_code, underlying_code),
                trade_date=trade_date,
                vix=vix_value,
                sigma_near=math.sqrt(var_near) * 100.0 if var_near > 0 else 0.0,
                sigma_next=math.sqrt(var_next) * 100.0 if var_next > 0 else 0.0,
                t_near=T_near,
                t_next=T_next,
            )

    return iv_result, vix_result


def compute_atm_iv(
    options: pd.DataFrame,
    S: float,
    T: float,
    r: float,
) -> float | None:
    """Compute ATM implied volatility as median IV of near-the-money options."""
    if options.empty or S <= 0 or T <= 0:
        return None

    # Select options within 5% of ATM
    moneyness = options["exercise_price"] / S
    near_money = options[(moneyness >= 0.95) & (moneyness <= 1.05)]
    if near_money.empty:
        near_money = options

    ivs = []
    for _, row in near_money.iterrows():
        settle = to_float(row.get("settle"))
        if settle is None or settle <= 0:
            continue
        K = to_float(row.get("exercise_price"))
        cp = row.get("call_put", "C")
        if K is None or K <= 0:
            continue

        iv = implied_vol_newton(settle, S, K, T, r, cp)
        if iv is not None and 0.01 < iv < 3.0:
            ivs.append(iv)

    if not ivs:
        return None
    return float(np.median(ivs))


def compute_25delta_iv(
    options: pd.DataFrame,
    S: float,
    T: float,
    r: float,
) -> tuple[float | None, float | None]:
    """Compute 25-delta call and put IVs for skew calculation.

    25-delta call: |N(d1) * e^(-qT)| ≈ 0.25, so N(d1) ≈ 0.25
    25-delta put: |N(-d1) * e^(-qT)| ≈ 0.25, so N(-d1) ≈ 0.25, i.e. d1 ≈ -0.674
    """
    if options.empty:
        return None, None

    # Find options near 25-delta strikes
    # Approximate: 25-delta call strike ≈ S * exp(N^{-1}(0.75)*sigma*sqrt(T) + (r-q-0.5*sigma^2)*T)
    # Use ATM IV as starting estimate for sigma
    atm = compute_atm_iv(options, S, T, r)
    if atm is None:
        return None, None

    # Approximate 25-delta strikes
    inv_norm_75 = norm.ppf(0.75)  # ~0.674
    inv_norm_25 = norm.ppf(0.25)  # ~-0.674

    K_call_25d = S * math.exp(inv_norm_75 * atm * math.sqrt(T) + (r - 0.5 * atm ** 2) * T)
    K_put_25d = S * math.exp(inv_norm_25 * atm * math.sqrt(T) + (r - 0.5 * atm ** 2) * T)

    iv_call = _find_iv_near_strike(options, K_call_25d, "C", S, T, r)
    iv_put = _find_iv_near_strike(options, K_put_25d, "P", S, T, r)

    return iv_call, iv_put


def _find_iv_near_strike(
    options: pd.DataFrame,
    target_K: float,
    call_put: str,
    S: float,
    T: float,
    r: float,
) -> float | None:
    """Find IV for the option closest to target_K."""
    cp_options = options[options["call_put"] == call_put].copy()
    if cp_options.empty:
        return None

    cp_options["k_dist"] = (cp_options["exercise_price"] - target_K).abs()
    closest = cp_options.nsmallest(3, "k_dist")

    ivs = []
    for _, row in closest.iterrows():
        settle = to_float(row.get("settle"))
        if settle is None or settle <= 0:
            continue
        K = to_float(row.get("exercise_price"))
        if K is None or K <= 0:
            continue
        iv = implied_vol_newton(settle, S, K, T, r, call_put)
        if iv is not None and 0.01 < iv < 3.0:
            ivs.append(iv)

    return float(np.median(ivs)) if ivs else None


# ---------------------------------------------------------------------------
# InfluxDB line protocol
# ---------------------------------------------------------------------------

def escape_key(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


def format_field_value(value) -> str:
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    parsed = to_float(value)
    if parsed is None:
        raise ValueError(f"Invalid numeric field value: {value!r}")
    return format(parsed, ".12g")


def iv_result_to_line(iv: IvResult, measurement: str = IV_MEASUREMENT) -> str:
    tags = {"underlying": iv.underlying, "trade_date": iv.trade_date}
    fields = {
        "atm_iv": iv.atm_iv * 100.0,  # annualized percent
        "term_days_near": iv.term_days_near,
        "term_days_next": iv.term_days_next,
        "option_count": iv.option_count,
    }
    if iv.iv_call_25delta is not None:
        fields["iv_call_25delta"] = iv.iv_call_25delta * 100.0
    if iv.iv_put_25delta is not None:
        fields["iv_put_25delta"] = iv.iv_put_25delta * 100.0
    if iv.skew is not None:
        fields["skew"] = iv.skew * 100.0

    tag_set = ",".join(f"{escape_key(k)}={escape_key(str(v))}" for k, v in sorted(tags.items()))
    field_set = ",".join(f"{escape_key(k)}={format_field_value(v)}" for k, v in sorted(fields.items()))
    ts = trade_date_to_timestamp_ns(iv.trade_date)
    return f"{escape_key(measurement)},{tag_set} {field_set} {ts}"


def vix_result_to_line(vix: VixResult, measurement: str = VIX_MEASUREMENT) -> str:
    tags = {"underlying": vix.underlying, "trade_date": vix.trade_date}
    fields = {
        "vix": vix.vix,
        "sigma_near": vix.sigma_near,
        "sigma_next": vix.sigma_next,
        "t_near": vix.t_near,
        "t_next": vix.t_next,
    }
    tag_set = ",".join(f"{escape_key(k)}={escape_key(str(v))}" for k, v in sorted(tags.items()))
    field_set = ",".join(f"{escape_key(k)}={format_field_value(v)}" for k, v in sorted(fields.items()))
    ts = trade_date_to_timestamp_ns(vix.trade_date)
    return f"{escape_key(measurement)},{tag_set} {field_set} {ts}"


def write_lines_to_influx(
    lines: Iterable[str],
    influx_url: str,
    org: str,
    bucket: str,
    token: str,
    timeout_seconds: float = 60.0,
) -> int:
    payload_lines = [line for line in lines if line]
    if not payload_lines:
        return 0

    # Write in batches of 5000
    batch_size = 5000
    total_written = 0

    for i in range(0, len(payload_lines), batch_size):
        batch = payload_lines[i:i + batch_size]
        query = parse.urlencode({"org": org, "bucket": bucket, "precision": "ns"})
        url = f"{influx_url.rstrip('/')}/api/v2/write?{query}"
        payload = ("\n".join(batch) + "\n").encode("utf-8")
        influx_request = request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Token {token}",
                "Content-Type": "text/plain; charset=utf-8",
            },
        )
        try:
            with request.urlopen(influx_request, timeout=timeout_seconds) as response:
                if 200 <= response.status < 300:
                    total_written += len(batch)
                else:
                    detail = response.read().decode("utf-8", errors="replace")
                    raise RuntimeError(f"InfluxDB write failed with HTTP {response.status}: {detail}")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"InfluxDB write failed with HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"InfluxDB write failed: {exc}") from exc

    return total_written


def resolve_influx_token(token: str | None) -> str:
    resolved = str(token or os.environ.get("INFLUXDB_TOKEN") or "").strip()
    if not resolved:
        raise ValueError("InfluxDB token is required. Set INFLUXDB_TOKEN or pass --token.")
    return resolved


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    tushare_data_path: str,
    underlyings: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    influx_url: str = DEFAULT_INFLUX_URL,
    org: str = DEFAULT_INFLUX_ORG,
    bucket: str = DEFAULT_INFLUX_BUCKET,
    token: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Run the full IV computation pipeline."""
    if underlyings is None:
        underlyings = ["OP510050.SH", "OP510300.SH"]

    print(f"Loading opt_basic...")
    opt_basic = load_opt_basic(tushare_data_path)

    print(f"Loading opt_daily ({start_date or 'earliest'} to {end_date or 'latest'})...")
    opt_daily = load_opt_daily(tushare_data_path, start_date, end_date)
    if opt_daily.empty:
        print("No opt_daily data found.")
        return {"error": "No opt_daily data"}

    trade_dates = sorted(opt_daily["trade_date"].unique())
    print(f"Trade dates: {len(trade_dates)} ({trade_dates[0]} to {trade_dates[-1]})")

    print("Loading SHIBOR rates...")
    shibor_df = load_shibor(tushare_data_path)

    iv_results: list[IvResult] = []
    vix_results: list[VixResult] = []

    for underlying_code in underlyings:
        fund_ts_code = UNDERLYING_MAP.get(underlying_code)
        if fund_ts_code is None:
            print(f"  Skipping {underlying_code}: no fund mapping")
            continue

        print(f"\nProcessing {FRIENDLY_NAMES.get(underlying_code, underlying_code)} ({underlying_code})...")

        # Load underlying ETF price
        underlying_prices = load_underlying_price(tushare_data_path, fund_ts_code)
        if underlying_prices.empty:
            print(f"  No price data for {fund_ts_code}")
            continue

        # Filter to dates with options data
        opt_dates_set = set(trade_dates)
        underlying_prices = underlying_prices[underlying_prices["trade_date"].isin(opt_dates_set)]

        for trade_date in trade_dates:
            day_options = opt_daily[opt_daily["trade_date"] == trade_date]
            price_row = underlying_prices[underlying_prices["trade_date"] == trade_date]
            if price_row.empty:
                continue
            S = to_float(price_row.iloc[0]["underlying_close"])
            if S is None or S <= 0:
                continue

            r = get_risk_free_rate(shibor_df, trade_date)
            iv_result, vix_result = compute_daily_iv(
                trade_date, opt_basic, day_options, S, r, underlying_code
            )

            if iv_result is not None:
                iv_results.append(iv_result)
            if vix_result is not None:
                vix_results.append(vix_result)

    print(f"\nComputed {len(iv_results)} IV results, {len(vix_results)} VIX results")

    # Generate line protocol
    iv_lines = [iv_result_to_line(iv) for iv in iv_results]
    vix_lines = [vix_result_to_line(vix) for vix in vix_results]
    all_lines = iv_lines + vix_lines

    if dry_run:
        print(f"Dry run: would write {len(all_lines)} lines to InfluxDB")
        if all_lines:
            print(f"Sample line: {all_lines[0]}")
    else:
        written = write_lines_to_influx(
            all_lines,
            influx_url=influx_url,
            org=org,
            bucket=bucket,
            token=resolve_influx_token(token),
        )
        print(f"Wrote {written} lines to InfluxDB")

    # Summary
    underlyings_summary = Counter(iv.underlying for iv in iv_results)
    dates = sorted({iv.trade_date for iv in iv_results})

    return {
        "iv_count": len(iv_results),
        "vix_count": len(vix_results),
        "underlyings": dict(sorted(underlyings_summary.items())),
        "start_date": dates[0] if dates else None,
        "end_date": dates[-1] if dates else None,
        "date_count": len(dates),
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute A-share implied volatility from Tushare options data and export to InfluxDB."
    )
    parser.add_argument("--tushare-data-path", default=DEFAULT_TUSHARE_DATA_PATH)
    parser.add_argument(
        "--underlyings",
        default="OP510050.SH,OP510300.SH",
        help="Comma-separated underlying codes (default: OP510050.SH,OP510300.SH)",
    )
    parser.add_argument("--start-date", help="Start date YYYYMMDD")
    parser.add_argument("--end-date", help="End date YYYYMMDD")
    parser.add_argument("--influx-url", default=DEFAULT_INFLUX_URL)
    parser.add_argument("--org", default=DEFAULT_INFLUX_ORG)
    parser.add_argument("--bucket", default=DEFAULT_INFLUX_BUCKET)
    parser.add_argument("--token", default=DEFAULT_INFLUX_TOKEN)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    underlyings = [u.strip() for u in args.underlyings.split(",") if u.strip()]

    summary = run_pipeline(
        tushare_data_path=args.tushare_data_path,
        underlyings=underlyings,
        start_date=args.start_date,
        end_date=args.end_date,
        influx_url=args.influx_url,
        org=args.org,
        bucket=args.bucket,
        token=args.token,
        dry_run=args.dry_run,
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
