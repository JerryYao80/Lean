#!/usr/bin/env python3
"""把 tushare fut_daily AU.SHF parquet 转成 LEAN 可读 CSV: Data/future/shf/daily/AU.SHF.csv
列: date,open,high,low,close,volume,open_interest。因子只读不交易。"""
import os, pandas as pd

SRC = "/home/project/tushare-downloader/tushare_data_v2/fut_daily/ts_code=AU.SHF/data.parquet"
OUT = os.path.join(os.path.dirname(__file__), "..", "Data", "future", "shf", "daily", "AU.SHF.csv")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
df = pd.read_parquet(SRC)
# tushare trade_date 是 yyyymmdd 字符串/整型, 转成 LEAN 可读的 yyyy-MM-dd
df["date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d")
cols = ["date", "open", "high", "low", "close", "vol"]
if "oi" in df.columns: cols.append("oi")
out = df[cols].copy()
rename = {"vol": "volume"}
if "oi" in df.columns: rename["oi"] = "open_interest"
out = out.rename(columns=rename)
out = out.sort_values("date")
out.to_csv(OUT, index=False)
print(f"AU.SHF: {len(out)} rows -> {OUT}, {out['date'].min()} -> {out['date'].max()}")
