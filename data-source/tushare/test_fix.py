#!/usr/bin/env python
"""
测试修复后的 index_daily 下载
"""
import sys
from pathlib import Path
sys.path.insert(0, '/home/project/tushare-downloader')

from downloader import TushareDownloader
from api_registry import get_api_config
import pandas as pd

# 初始化下载器
downloader = TushareDownloader()

# 获取 index_daily API 配置
api_config = get_api_config("index_daily")
if api_config is None:
    print("ERROR: index_daily API config not found!")
    sys.exit(1)

# 测试下载上证指数数据
test_code = "000001.SH"
print(f"Testing download for {test_code}...")
print(f"This will download all data, with yearly chunking if needed.")
print()

success, rows = downloader.download_api_by_stock(api_config, test_code)

print()
print("="*60)
print("RESULT:")
print(f"  Success: {success}")
print(f"  Rows: {rows}")
print("="*60)

# 验证文件
file_path = downloader.data_dir / "index_daily" / f"ts_code={test_code}" / "data.parquet"
if file_path.exists():
    df = pd.read_parquet(file_path)
    print(f"File: {file_path}")
    print(f"File rows: {len(df)}")
    if len(df) > 0:
        print(f"Date range: {df['trade_date'].min()} to {df['trade_date'].max()}")
        print("\nFirst 5 rows:")
        print(df[['ts_code', 'trade_date', 'close', 'vol']].head())
        print("\nLast 5 rows:")
        print(df[['ts_code', 'trade_date', 'close', 'vol']].tail())
        print("\nSuccess! Data downloaded and saved correctly.")
    else:
        print("ERROR: File is empty!")
else:
    print(f"ERROR: File not found: {file_path}")
