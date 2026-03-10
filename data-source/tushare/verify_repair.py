#!/usr/bin/env python3
"""
Tushare 下载器验证脚本
用于验证修复后的下载器是否正常工作
"""
import logging
from downloader import TushareDownloader
from api_registry import get_api_config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def verify_methods():
    print("=" * 70)
    print("验证 1: 检查所有方法是否存在")
    print("=" * 70)
    
    downloader = TushareDownloader()
    methods = [
        'download_api_no_chunk',
        'download_api_by_year',
        'download_api_by_quarter',
        'download_api_by_date',
        'download_api_by_stock'
    ]
    
    all_exist = True
    for method in methods:
        exists = hasattr(downloader, method)
        status = "✅" if exists else "❌"
        print(f"{status} {method}")
        if not exists:
            all_exist = False
    
    return all_exist

def verify_configs():
    print("\n" + "=" * 70)
    print("验证 2: 检查关键 API 配置")
    print("=" * 70)
    
    critical_apis = {
        'daily': 'ChunkStrategy.STOCK',
        'daily_basic': 'ChunkStrategy.STOCK',
        'adj_factor': 'ChunkStrategy.STOCK',
        'cb_daily': 'ChunkStrategy.DATE',
        'us_daily': 'ChunkStrategy.DATE',
        'opt_daily': 'ChunkStrategy.DATE',
    }
    
    all_correct = True
    for api_name, expected in critical_apis.items():
        config = get_api_config(api_name)
        if config:
            actual = str(config.chunk_strategy)
            correct = actual == expected
            status = "✅" if correct else "❌"
            print(f"{status} {api_name:20} {actual:30} {'OK' if correct else f'Expected: {expected}'}")
            if not correct:
                all_correct = False
        else:
            print(f"❌ {api_name:20} 配置未找到")
            all_correct = False
    
    return all_correct

def main():
    print("\nTushare 下载器验证\n")
    
    methods_ok = verify_methods()
    configs_ok = verify_configs()
    
    print("\n" + "=" * 70)
    print("验证结果")
    print("=" * 70)
    
    if methods_ok and configs_ok:
        print("✅ 所有验证通过！下载器可以正常使用。")
        print("\n建议:")
        print("1. 先用小范围数据测试（如单只股票、单个交易日）")
        print("2. 观察日志，注意 'Hit 2000 row limit' 等警告信息")
        print("3. 验证下载的数据完整性")
        return 0
    else:
        print("❌ 验证失败！请检查上述错误。")
        return 1

if __name__ == '__main__':
    exit(main())
