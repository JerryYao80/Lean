"""
智能数据下载器

功能:
1. 按类别分批下载
2. 每个接口先测试再下载
3. 详细的进度报告和错误记录
4. 断点续传支持
"""
import argparse
import time
import json
import logging
from pathlib import Path
from typing import List, Dict, Any

import config
from downloader import TushareDownloader
from api_registry import get_enabled_apis, get_all_categories, get_apis_by_category
from api_tester import APITester

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SmartDownloader:
    """智能下载器"""
    
    def __init__(self):
        self.downloader = TushareDownloader()
        self.tester = APITester()
        self.report = {
            "categories": {},
            "failed_apis": [],
            "summary": {}
        }
        
    def test_and_download_category(
        self, 
        category: str,
        test_first: bool = True
    ) -> Dict[str, Any]:
        """
        测试并下载指定类别的数据
        
        Args:
            category: 类别名称
            test_first: 是否先测试接口
            
        Returns:
            下载统计信息
        """
        logger.info(f"Processing category: {category}")
        
        apis = [api for api in get_apis_by_category(category) if api.enabled]
        logger.info(f"Found {len(apis)} enabled APIs in {category}")
        
        category_stats = {
            "total_apis": len(apis),
            "tested": 0,
            "available": 0,
            "permission_denied": 0,
            "downloaded": 0,
            "failed": 0,
            "apis": []
        }
        
        for api in apis:
            api_result = {
                "api_name": api.api_name,
                "description": api.description,
                "status": "unknown"
            }
            
            # 测试接口（如果需要）
            if test_first:
                logger.info(f"Testing {api.api_name}...")
                status, error_msg, data = self.tester.test_single_api(api)
                category_stats["tested"] += 1
                
                if status == "available":
                    category_stats["available"] += 1
                    api_result["test_status"] = "available"
                elif status == "permission_denied":
                    category_stats["permission_denied"] += 1
                    api_result["test_status"] = "permission_denied"
                    api_result["error"] = error_msg
                    category_stats["apis"].append(api_result)
                    logger.warning(f"Skipping {api.api_name}: {error_msg}")
                    continue
                else:
                    logger.warning(f"Test returned {status} for {api.api_name}")
                    api_result["test_status"] = status
                    
                time.sleep(0.3)
            
            # 下载数据
            try:
                logger.info(f"Downloading {api.api_name}...")
                stats = self.downloader.download_all(api_names=[api.api_name])
                
                if stats["completed"] > 0 or stats["skipped"] > 0:
                    category_stats["downloaded"] += 1
                    api_result["status"] = "success"
                    api_result["stats"] = stats
                else:
                    category_stats["failed"] += 1
                    api_result["status"] = "failed"
                    self.report["failed_apis"].append({
                        "category": category,
                        "api_name": api.api_name,
                        "reason": "No data downloaded"
                    })
                    
            except Exception as e:
                category_stats["failed"] += 1
                api_result["status"] = "error"
                api_result["error"] = str(e)
                logger.error(f"Error downloading {api.api_name}: {e}")
                self.report["failed_apis"].append({
                    "category": category,
                    "api_name": api.api_name,
                    "reason": str(e)
                })
                
            category_stats["apis"].append(api_result)
            
        self.report["categories"][category] = category_stats
        return category_stats
        
    def download_all_categories(
        self,
        categories: List[str] = None,
        test_first: bool = True
    ) -> Dict[str, Any]:
        """
        下载所有类别的数据
        
        Args:
            categories: 要下载的类别列表（None=全部）
            test_first: 是否先测试接口
            
        Returns:
            总体统计信息
        """
        if categories is None:
            categories = sorted(get_all_categories())
            
        logger.info(f"Will download {len(categories)} categories")
        start_time = time.time()
        
        for i, category in enumerate(categories, 1):
            logger.info(f"\n{'='*60}")
            logger.info(f"Category {i}/{len(categories)}: {category}")
            logger.info(f"{'='*60}")
            
            try:
                stats = self.test_and_download_category(category, test_first)
                logger.info(f"Category {category} completed: "
                          f"{stats['downloaded']} downloaded, "
                          f"{stats['failed']} failed")
            except Exception as e:
                logger.error(f"Error processing category {category}: {e}")
                
        elapsed = time.time() - start_time
        
        # 生成总体统计
        self.report["summary"] = {
            "total_categories": len(categories),
            "total_time": elapsed,
            "total_downloaded": sum(c.get("downloaded", 0) for c in self.report["categories"].values()),
            "total_failed": sum(c.get("failed", 0) for c in self.report["categories"].values()),
        }
        
        self._save_report()
        self._print_summary()
        
        return self.report
        
    def _save_report(self):
        """保存下载报告"""
        report_dir = Path("download_reports")
        report_dir.mkdir(exist_ok=True)
        
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        report_path = report_dir / f"download_report_{timestamp}.json"
        
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(self.report, f, ensure_ascii=False, indent=2)
            
        logger.info(f"Report saved to {report_path}")
        
    def _print_summary(self):
        """打印下载摘要"""
        print("\n" + "="*60)
        print("Download Summary")
        print("="*60)
        print(f"Total categories: {self.report['summary']['total_categories']}")
        print(f"Total time: {self.report['summary']['total_time']:.1f}s")
        print(f"APIs downloaded: {self.report['summary']['total_downloaded']}")
        print(f"APIs failed: {self.report['summary']['total_failed']}")
        print("="*60)
        
        if self.report["failed_apis"]:
            print("\nFailed APIs:")
            for item in self.report["failed_apis"]:
                print(f"  - {item['category']:15s} {item['api_name']:30s} {item['reason']}")
        

def main():
    parser = argparse.ArgumentParser(description="Smart Tushare Downloader")
    parser.add_argument("--category", type=str, help="指定类别（逗号分隔）")
    parser.add_argument("--no-test", action="store_true", help="跳过接口测试，直接下载")
    parser.add_argument("--list", action="store_true", help="列出所有类别")
    
    args = parser.parse_args()
    
    if args.list:
        categories = sorted(get_all_categories())
        print("\nAvailable categories:")
        for cat in categories:
            apis = [api for api in get_apis_by_category(cat) if api.enabled]
            print(f"  - {cat:20s} ({len(apis)} APIs)")
        return
        
    downloader = SmartDownloader()
    
    categories = None
    if args.category:
        categories = args.category.split(",")
        
    test_first = not args.no_test
    
    print(f"Starting smart download...")
    print(f"Test first: {test_first}")
    print()
    
    downloader.download_all_categories(categories, test_first)
    
    print("\nDownload completed!")
    

if __name__ == "__main__":
    main()
