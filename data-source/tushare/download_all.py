#!/usr/bin/env python
"""
全量数据下载脚本

按优先级和类别批量下载所有Tushare数据
"""
import argparse
import sys
import time
from pathlib import Path

from smart_downloader import SmartDownloader
from api_registry import get_all_categories


def main():
    parser = argparse.ArgumentParser(
        description="Tushare 全量数据下载",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  # 下载所有数据
  python download_all.py
  
  # 下载指定类别
  python download_all.py --category stock_basic,stock_quote
  
  # 跳过接口测试，直接下载
  python download_all.py --no-test
  
  # 下载小数据量的类别（测试用）
  python download_all.py --quick
        """
    )
    parser.add_argument(
        "--category", 
        type=str, 
        help="指定类别（逗号分隔），默认下载全部"
    )
    parser.add_argument(
        "--no-test", 
        action="store_true", 
        help="跳过接口测试，直接下载"
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="快速测试模式，只下载小数据量类别"
    )
    
    args = parser.parse_args()
    
    # 确定要下载的类别
    if args.quick:
        categories = ["fx", "spot", "options", "news"]
        print("Quick mode: downloading small categories only")
    elif args.category:
        categories = args.category.split(",")
        print(f"Downloading specified categories: {categories}")
    else:
        categories = sorted(get_all_categories())
        print(f"Downloading ALL {len(categories)} categories")
    
    test_first = not args.no_test
    
    print(f"Test before download: {test_first}")
    print()
    
    user_input = input("Continue? [Y/n]: ")
    if user_input.lower() == 'n':
        print("Cancelled")
        return
    
    print("\nStarting download...")
    print("="*60)
    
    downloader = SmartDownloader()
    
    try:
        start_time = time.time()
        report = downloader.download_all_categories(categories, test_first)
        elapsed = time.time() - start_time
        
        print("\n" + "="*60)
        print("Download Complete!")
        print("="*60)
        print(f"Time elapsed: {elapsed:.1f}s ({elapsed/60:.1f} minutes)")
        print(f"Categories processed: {report['summary']['total_categories']}")
        print(f"APIs downloaded: {report['summary']['total_downloaded']}")
        print(f"APIs failed: {report['summary']['total_failed']}")
        
        if report['failed_apis']:
            print("\nFailed APIs:")
            for item in report['failed_apis']:
                print(f"  - {item['category']:15s} {item['api_name']:30s}")
                
    except KeyboardInterrupt:
        print("\n\nDownload interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    

if __name__ == "__main__":
    main()
