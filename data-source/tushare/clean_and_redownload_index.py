#!/usr/bin/env python
"""
清理并重新下载 index_daily 数据
"""
import shutil
from pathlib import Path

# 删除 index_daily 数据目录
data_dir = Path("tushare_data/index_daily")
if data_dir.exists():
    print(f"Deleting {data_dir}...")
    shutil.rmtree(data_dir)
    print("Deleted.")
else:
    print(f"{data_dir} does not exist.")

# 重新下载
print("\nStarting download...")
from downloader import TushareDownloader

downloader = TushareDownloader()
stats = downloader.download_all(api_names=['index_daily'])

print("\n" + "="*60)
print("Download Complete!")
print("="*60)
print(f"Completed: {stats['completed']}")
print(f"Skipped: {stats['skipped']}")
print(f"Failed: {stats['failed']}")
print(f"Total rows: {stats['total_rows']}")
