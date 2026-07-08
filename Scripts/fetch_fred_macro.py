#!/usr/bin/env python3
"""下载 FRED VIX(VIXCLS) 与 10Y TIPS 实际利率(DFII10)到 Data/macro/fred/。
挂 cron 每日 06:00 刷新。tushare 仍是价格主体,FRED 补宏观。"""
import os, sys, requests, pandas as pd

FRED_KEY = os.environ.get("FRED_API_KEY")
if not FRED_KEY:
    print("ERROR: FRED_API_KEY not set", file=sys.stderr); sys.exit(1)
SERIES = {"VIX": "VIXCLS", "DFII10": "DFII10"}
OUT = os.path.join(os.path.dirname(__file__), "..", "Data", "macro", "fred")
os.makedirs(OUT, exist_ok=True)
for name, sid in SERIES.items():
    url = (f"https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={sid}&api_key={FRED_KEY}&file_type=json&observation_start=2015-01-01")
    r = requests.get(url, timeout=60).json()
    df = pd.DataFrame(r["observations"])[["date", "value"]]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna()
    df.to_csv(os.path.join(OUT, f"{name.lower()}.csv"), index=False)
    print(f"{name}: {len(df)} rows, {df['date'].min()} -> {df['date'].max()}")
