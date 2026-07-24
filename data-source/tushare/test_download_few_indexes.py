#!/usr/bin/env python
"""
���试下载前3个指数的数据
"""
import sys
from pathlib import Path
sys.path.insert(0, '/home/project/tushare-downloader')

import pandas as pd
from downloader import TushareDownloader
from api_registry import get_api_config

# 初始化下载器
downloader = TushareDownloader()
api_config = get_api_config("index_daily")

# 读取指数列表
index_file = downloader.data_dir / "index_basic" / "data.parquet"
df_index = pd.read_parquet(index_file)

# 只下载前3个指数进行测试
test_codes = df_index['ts_code'].head(3).tolist()
print(f"Testing download for {len(test_codes)} indexes: {test_codes}")
print()

completed = 0
failed = 0
total_rows = 0

for i, code in enumerate(test_codes, 1):
    print(f"[{i}/{len(test_codes)}] Downloading {code}...")
    try:
        success, rows = downloader.download_api_by_stock(api_config, code)
        if success:
            completed += 1
            total_rows += rows
            print(f"  Success: {rows} rows")
        else:
            failed += 1
            print(f"  Failed!")
    except Exception as e:
        failed += 1
        print(f"  Error: {e}")
    print()

print("="*60)
print("Test Summary:")
print(f"  Completed: {completed}")
print(f"  Failed: {failed}")
print(f"  Total rows: {total_rows}")
print("="*60)

# 验证第一个指数的数据
first_code = test_codes[0]
file_path = downloader.data_dir / "index_daily" / f"ts_code={first_code}" / "data.parquet"
if file_path.exists():
    df = pd.read_parquet(file_path)
    print(f"\nVerification for {first_code}:")
    print(f"  File rows: {len(df)}")
    if len(df) > 0:
        print(f"  Date range: {df['trade_date'].min()} to {df['trade_date'].max()}")
    else:
        print(f"  ERROR: File is empty!")
else:
    print(f"\nERROR: File not found for {first_code}")
