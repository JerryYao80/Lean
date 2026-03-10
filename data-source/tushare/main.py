"""
Tushare 全量数据下载器主程序

用法:
    python main.py [options]

选项:
    --api STR       指定下载的 API 名称 (逗号分隔)
    --category STR  指定下载的类别 (逗号分隔)
    --priority INT  指定下载优先级 (1=高, 2=中, 3=低)
    --workers INT   指定并发线程数
    --test          运行测试模式 (下载少量数据验证)
    --list          列出所有可用 API
"""
import argparse
import sys
import logging
import time
from pathlib import Path

import config
from downloader import TushareDownloader
from api_registry import (
    ALL_APIS, get_enabled_apis, get_all_categories, 
    get_apis_by_category, API_REGISTRY
)

# 配置日志 - moved to setup_logging()

logger = logging.getLogger("main")

def setup_logging():
    """Ensure log directory exists and configure logging"""
    Path(config.LOG_DIR).mkdir(parents=True, exist_ok=True)
    
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL),
        format=config.LOG_FORMAT,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                Path(config.LOG_DIR) / f"download_{int(time.time())}.log", 
                encoding='utf-8'
            )
        ]
    )

def list_apis():
    """列出所有 API"""
    print(f"\n{'='*60}")
    print(f"{'API Name':<20} | {'Category':<15} | {'Priority':<8} | {'Description'}")
    print(f"{'-'*60}")
    
    categories = get_all_categories()
    for cat in sorted(categories):
        apis = get_apis_by_category(cat)
        for api in apis:
            print(f"{api.api_name:<20} | {api.category:<15} | {api.priority:<8} | {api.description}")
            
    print(f"{'='*60}")
    print(f"Total APIs: {len(ALL_APIS)}")

def main():
    setup_logging()
    
    parser = argparse.ArgumentParser(description="Tushare Data Downloader")
    parser.add_argument("--api", type=str, help="指定 API 名称 (逗号分隔)")
    parser.add_argument("--category", type=str, help="指定类别 (逗号分隔)")
    parser.add_argument("--priority", type=int, choices=[1, 2, 3], help="指定优先级")
    parser.add_argument("--workers", type=int, default=config.MAX_WORKERS, help="并发线程数")
    parser.add_argument("--test", action="store_true", help="测试模式 (仅下载 stock_basic)")
    parser.add_argument("--list", action="store_true", help="列出所有 API")
    parser.add_argument("--start-year", type=int, help="开始年份")
    parser.add_argument("--end-year", type=int, help="结束年份")
    
    args = parser.parse_args()
    
    # 列出 API
    if args.list:
        list_apis()
        return

    # 更新配置
    if args.workers:
        config.MAX_WORKERS = args.workers
    
    if args.start_year:
        config.START_YEAR = args.start_year
    if args.end_year:
        config.END_YEAR = args.end_year

    logger.info("Starting Tushare Data Downloader...")
    logger.info(f"Workers: {config.MAX_WORKERS}")
    logger.info(f"Year Range: {config.START_YEAR} - {config.END_YEAR}")
    
    # 初始化下载器
    downloader = TushareDownloader(max_workers=config.MAX_WORKERS)
    
    # 测试模式
    if args.test:
        logger.info("Running in TEST mode...")
        # 仅下载 stock_basic 和一个简单的行情数据作为测试
        api_names = ["stock_basic"]
        print(f"Testing APIs: {api_names}")
        downloader.download_all(api_names=api_names)
        
        # 另外测试一个带年份参数的
        print("Testing daily quote download (limited)...")
        # 临时修改配置以加快测试
        original_start = config.START_YEAR
        original_end = config.END_YEAR
        config.START_YEAR = 2023
        config.END_YEAR = 2023
        
        downloader.download_single_api("daily", year=2023)
        
        config.START_YEAR = original_start
        config.END_YEAR = original_end
        return

    # 解析参数
    api_names = args.api.split(",") if args.api else None
    categories = args.category.split(",") if args.category else None
    
    # 执行下载
    try:
        stats = downloader.download_all(
            api_names=api_names,
            categories=categories,
            priority=args.priority
        )
        
        print("\n" + "="*50)
        print("Download Complete!")
        print(f"Time Elapsed: {stats['elapsed_seconds']:.2f}s")
        print(f"Total Tasks: {stats['total']}")
        print(f"Processed: {stats['processed']}")
        print(f"Success: {stats['completed']}")
        print(f"Skipped: {stats['skipped']}")
        print(f"Failed: {stats['failed']}")
        print(f"Total Rows: {stats['total_rows']:,}")
        print("="*50 + "\n")
        
    except KeyboardInterrupt:
        logger.warning("Download interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
