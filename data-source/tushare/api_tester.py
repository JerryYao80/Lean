"""
Tushare API 接口测试器

测试每个接口的可用性，识别：
1. 需要权限/积分的接口
2. 爬虫接口
3. 严格限流的接口
4. 可正常使用的接口
"""
import time
import logging
from typing import Dict, Any, List, Tuple
from pathlib import Path
import json

import tushare as ts
import pandas as pd

import config
from api_registry import ALL_APIS, APIConfig, ChunkStrategy

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class APITester:
    """API 接口测试器"""
    
    def __init__(self):
        """初始化测试器"""
        self.pro = ts.pro_api(config.TUSHARE_TOKEN)
        self.pro._DataApi__token = config.TUSHARE_TOKEN
        self.pro._DataApi__http_url = config.TUSHARE_API_URL
        
        self.results = {
            "available": [],      # 可用接口
            "permission_denied": [],  # 权限不足
            "crawler": [],        # 爬虫接口
            "no_data": [],        # 没有数据
            "error": [],          # 其他错误
        }
        
    def test_single_api(
        self, 
        api_config: APIConfig, 
        test_params: Dict[str, Any] = None
    ) -> Tuple[str, str, Any]:
        """
        测试单个接口
        
        Args:
            api_config: API 配置
            test_params: 测试参数
            
        Returns:
            (状态, 错误信息, 数据)
            状态: available, permission_denied, crawler, no_data, error
        """
        api_name = api_config.api_name
        
        # 准备测试参数
        if test_params is None:
            test_params = self._prepare_test_params(api_config)
            
        logger.info(f"Testing {api_name} with params: {test_params}")
        
        try:
            # 调用 API
            api_func = getattr(self.pro, api_name, None)
            if api_func is None:
                df = self.pro.query(api_name, **test_params)
            else:
                df = api_func(**test_params)
            
            # 检查返回结果
            if df is None or len(df) == 0:
                logger.warning(f"{api_name}: No data returned")
                return "no_data", "No data returned", None
            
            logger.info(f"{api_name}: SUCCESS - {len(df)} rows, columns: {list(df.columns)}")
            return "available", "", df
            
        except Exception as e:
            error_msg = str(e).lower()
            
            # 分析错误类型
            if "权限" in str(e) or "permission" in error_msg:
                logger.warning(f"{api_name}: Permission denied - {e}")
                return "permission_denied", str(e), None
                
            elif "积分不足" in str(e) or "积分" in str(e):
                logger.warning(f"{api_name}: Insufficient credits - {e}")
                return "permission_denied", str(e), None
                
            elif "爬虫" in str(e) or "crawler" in error_msg:
                logger.warning(f"{api_name}: Crawler API - {e}")
                return "crawler", str(e), None
                
            elif "不存在" in str(e) or "not found" in error_msg:
                logger.warning(f"{api_name}: API not found - {e}")
                return "error", str(e), None
                
            else:
                logger.error(f"{api_name}: Error - {e}")
                return "error", str(e), None
                
    def _prepare_test_params(self, api_config: APIConfig) -> Dict[str, Any]:
        """
        准备测试参数
        
        根据接口的分块策略和必需参数准备合适的测试参数
        """
        params = api_config.required_params.copy()
        
        # 根据分块策略添加参数
        if api_config.chunk_strategy == ChunkStrategy.YEAR:
            # 测试最近一年的数据
            params[api_config.start_date_param] = "20240101"
            params[api_config.end_date_param] = "20240131"
            
        elif api_config.chunk_strategy == ChunkStrategy.QUARTER:
            # 测试最近一个季度
            params["period"] = "20231231"
            
        elif api_config.chunk_strategy == ChunkStrategy.DATE:
            # 测试最近一个交易日
            params[api_config.date_field or "trade_date"] = "20240102"
            
        return params
        
    def test_all_apis(self, save_report: bool = True) -> Dict[str, Any]:
        """
        测试所有接口
        
        Args:
            save_report: 是否保存测试报告
            
        Returns:
            测试结果统计
        """
        logger.info(f"Testing {len(ALL_APIS)} APIs...")
        
        for api_config in ALL_APIS:
            api_name = api_config.api_name
            
            # 如果已知是禁用的接口，直接跳过
            if not api_config.enabled:
                logger.info(f"Skipping {api_name} (marked as disabled)")
                # 根据注释推断原因
                if "爬虫" in api_config.description or "crawler" in api_name.lower():
                    self.results["crawler"].append({
                        "api_name": api_name,
                        "description": api_config.description,
                        "reason": "Crawler API"
                    })
                elif "专业版" in api_config.description or "积分" in api_config.description:
                    self.results["permission_denied"].append({
                        "api_name": api_name,
                        "description": api_config.description,
                        "reason": "Professional/High credits required"
                    })
                continue
            
            # 测试接口
            status, error_msg, data = self.test_single_api(api_config)
            
            # 记录结果
            result_entry = {
                "api_name": api_name,
                "description": api_config.description,
                "category": api_config.category,
                "chunk_strategy": api_config.chunk_strategy.value,
                "error_msg": error_msg
            }
            
            if data is not None:
                result_entry["sample_columns"] = list(data.columns)
                result_entry["sample_rows"] = len(data)
            
            self.results[status].append(result_entry)
            
            # 限流：每次调用后等待
            time.sleep(0.5)
        
        # 生成统计
        stats = {
            "total_tested": len(ALL_APIS),
            "available": len(self.results["available"]),
            "permission_denied": len(self.results["permission_denied"]),
            "crawler": len(self.results["crawler"]),
            "no_data": len(self.results["no_data"]),
            "error": len(self.results["error"]),
        }
        
        # 保存报告
        if save_report:
            self._save_report(stats)
        
        return stats
        
    def _save_report(self, stats: Dict[str, Any]) -> None:
        """保存测试报告"""
        report_dir = Path("test_reports")
        report_dir.mkdir(exist_ok=True)
        
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        
        # 保存 JSON 报告
        report_path = report_dir / f"api_test_report_{timestamp}.json"
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump({
                "stats": stats,
                "results": self.results
            }, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Report saved to {report_path}")
        
        # 打印摘要
        print("\n" + "="*60)
        print("API Testing Summary")
        print("="*60)
        print(f"Total APIs tested: {stats['total_tested']}")
        print(f"✅ Available: {stats['available']}")
        print(f"❌ Permission denied: {stats['permission_denied']}")
        print(f"🕷️  Crawler APIs: {stats['crawler']}")
        print(f"⚠️  No data: {stats['no_data']}")
        print(f"❗ Errors: {stats['error']}")
        print("="*60)
        
        # 打印可用接口列表
        if self.results["available"]:
            print("\n✅ Available APIs:")
            for item in self.results["available"]:
                print(f"  - {item['api_name']:30s} {item['description']}")
        
        # 打印需要权限的接口
        if self.results["permission_denied"]:
            print("\n❌ Permission denied APIs:")
            for item in self.results["permission_denied"]:
                print(f"  - {item['api_name']:30s} {item['description']} ({item.get('error_msg', 'Unknown')})")
        

def main():
    """主函数"""
    tester = APITester()
    
    print("Starting API testing...")
    print("This will take several minutes due to rate limiting...")
    print()
    
    stats = tester.test_all_apis(save_report=True)
    
    print("\nTesting complete!")
    

if __name__ == "__main__":
    main()
