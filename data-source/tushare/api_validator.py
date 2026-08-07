"""
API验证脚本 - 验证修复后的API配置是否正确

此脚本会:
1. 测试所有启用的API是否可以正常调用
2. 验证必填参数是否齐全
3. 检查API名称是否正确
4. 生成详细的验证报告
"""
import os
import sys
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
import json

import tushare as ts
import pandas as pd

import config
from api_registry import (
    get_enabled_apis, 
    APIConfig, 
    ChunkStrategy,
    get_all_categories
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class APIValidator:
    
    def __init__(self):
        self.pro = ts.pro_api(config.TUSHARE_TOKEN)
        self.pro._DataApi__token = config.TUSHARE_TOKEN
        self.pro._DataApi__http_url = config.TUSHARE_API_URL
        
        self.results = {
            "success": [],
            "failed": [],
            "skipped": [],
            "warnings": []
        }
        
    def _get_test_params(self, api_config: APIConfig) -> Dict:
        params = api_config.required_params.copy()
        
        if api_config.chunk_strategy == ChunkStrategy.STOCK:
            params[api_config.code_field or "ts_code"] = "600000.SH"
        elif api_config.chunk_strategy == ChunkStrategy.DATE:
            params[api_config.date_field or "trade_date"] = "20240101"
        elif api_config.chunk_strategy == ChunkStrategy.YEAR:
            params["start_date"] = "20240101"
            params["end_date"] = "20240131"
        elif api_config.chunk_strategy == ChunkStrategy.QUARTER:
            params["period"] = "20231231"
            
        return params
    
    def validate_single_api(self, api_config: APIConfig) -> Dict:
        api_name = api_config.api_name
        result = {
            "api_name": api_name,
            "description": api_config.description,
            "category": api_config.category,
            "status": "unknown",
            "message": "",
            "test_params": {}
        }
        
        if not api_config.enabled:
            result["status"] = "skipped"
            result["message"] = "API is disabled"
            return result
        
        try:
            test_params = self._get_test_params(api_config)
            result["test_params"] = test_params
            
            logger.info(f"Testing {api_name} with params: {test_params}")
            
            api_func = getattr(self.pro, api_name, None)
            if api_func is None:
                df = self.pro.query(api_name, **test_params)
            else:
                df = api_func(**test_params)
            
            if df is None:
                result["status"] = "warning"
                result["message"] = "API returned None"
            elif len(df) == 0:
                result["status"] = "warning"
                result["message"] = "API returned empty DataFrame (no data for test params)"
            else:
                result["status"] = "success"
                result["message"] = f"Successfully retrieved {len(df)} rows"
                result["columns"] = list(df.columns)
                
            time.sleep(0.3)
            
        except Exception as e:
            error_msg = str(e)
            result["status"] = "failed"
            result["message"] = error_msg
            
            if "请指定正确的接口名" in error_msg:
                result["fix_needed"] = "Incorrect API name"
            elif "必填参数" in error_msg or "参数校验失败" in error_msg:
                result["fix_needed"] = "Missing required parameters"
            elif "权限" in error_msg or "permission" in error_msg.lower():
                result["status"] = "skipped"
                result["message"] = "Insufficient permissions"
            
        return result
    
    def validate_all(self, categories: Optional[List[str]] = None) -> Dict:
        logger.info("=" * 80)
        logger.info("Starting API Validation")
        logger.info("=" * 80)
        
        enabled_apis = get_enabled_apis()
        
        if categories:
            enabled_apis = [api for api in enabled_apis if api.category in categories]
        
        logger.info(f"Total APIs to validate: {len(enabled_apis)}")
        
        for idx, api_config in enumerate(enabled_apis, 1):
            logger.info(f"\n[{idx}/{len(enabled_apis)}] Validating {api_config.api_name}...")
            
            result = self.validate_single_api(api_config)
            
            if result["status"] == "success":
                self.results["success"].append(result)
                logger.info(f"✓ {result['message']}")
            elif result["status"] == "failed":
                self.results["failed"].append(result)
                logger.error(f"✗ {result['message']}")
            elif result["status"] == "warning":
                self.results["warnings"].append(result)
                logger.warning(f"⚠ {result['message']}")
            elif result["status"] == "skipped":
                self.results["skipped"].append(result)
                logger.info(f"⊘ {result['message']}")
        
        self._print_summary()
        return self.results
    
    def _print_summary(self):
        total = (len(self.results["success"]) + len(self.results["failed"]) + 
                 len(self.results["warnings"]) + len(self.results["skipped"]))
        
        logger.info("\n" + "=" * 80)
        logger.info("VALIDATION SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Total APIs tested: {total}")
        logger.info(f"✓ Success: {len(self.results['success'])}")
        logger.info(f"⚠ Warnings: {len(self.results['warnings'])}")
        logger.info(f"✗ Failed: {len(self.results['failed'])}")
        logger.info(f"⊘ Skipped: {len(self.results['skipped'])}")
        
        if self.results["failed"]:
            logger.info("\n" + "-" * 80)
            logger.info("FAILED APIs (需要修复):")
            logger.info("-" * 80)
            for result in self.results["failed"]:
                logger.error(f"  {result['api_name']}: {result['message']}")
                if "fix_needed" in result:
                    logger.error(f"    → Fix: {result['fix_needed']}")
        
        if self.results["warnings"]:
            logger.info("\n" + "-" * 80)
            logger.info("WARNINGS (可能需要检查):")
            logger.info("-" * 80)
            for result in self.results["warnings"]:
                logger.warning(f"  {result['api_name']}: {result['message']}")
    
    def save_report(self, output_path: str = "api_validation_report.json"):
        report = {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total": sum(len(v) for v in self.results.values()),
                "success": len(self.results["success"]),
                "failed": len(self.results["failed"]),
                "warnings": len(self.results["warnings"]),
                "skipped": len(self.results["skipped"])
            },
            "details": self.results
        }
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        logger.info(f"\nValidation report saved to: {output_path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Validate Tushare API configurations")
    parser.add_argument(
        "--category", 
        nargs="+",
        help="Specific categories to validate (e.g., stock_basic stock_quote)"
    )
    parser.add_argument(
        "--output",
        default="api_validation_report.json",
        help="Output file path for validation report"
    )
    
    args = parser.parse_args()
    
    validator = APIValidator()
    results = validator.validate_all(categories=args.category)
    validator.save_report(args.output)
    
    failed_count = len(results["failed"])
    sys.exit(0 if failed_count == 0 else 1)


if __name__ == "__main__":
    main()
