"""
数据完整性校验模块
"""
import logging
import hashlib
from typing import Optional, List, Dict, Any
from pathlib import Path

import pandas as pd

import config

logger = logging.getLogger(__name__)


class DataValidator:
    """数据完整性校验器"""
    
    def __init__(self):
        self.validation_results: List[Dict[str, Any]] = []
        
    def validate_dataframe(
        self,
        df: pd.DataFrame,
        api_name: str,
        expected_columns: Optional[List[str]] = None,
        min_rows: int = 0
    ) -> bool:
        """
        验证 DataFrame 数据完整性
        
        Args:
            df: 待验证的 DataFrame
            api_name: API 接口名称
            expected_columns: 期望的列名列表
            min_rows: 最小行数要求
            
        Returns:
            是否验证通过
        """
        result = {
            "api_name": api_name,
            "valid": True,
            "row_count": len(df) if df is not None else 0,
            "errors": []
        }
        
        # 检查是否为 None
        if df is None:
            result["valid"] = False
            result["errors"].append("DataFrame is None")
            self.validation_results.append(result)
            return False
            
        # 检查是否为空
        if len(df) < min_rows:
            result["valid"] = False
            result["errors"].append(f"Row count {len(df)} is less than minimum {min_rows}")
            
        # 检查期望的列
        if expected_columns and config.VALIDATE_SCHEMA:
            missing_columns = set(expected_columns) - set(df.columns)
            if missing_columns:
                result["valid"] = False
                result["errors"].append(f"Missing columns: {missing_columns}")
                
        # 检查是否有完全重复的行
        if len(df) > 0:
            duplicates = df.duplicated().sum()
            if duplicates > 0:
                result["warnings"] = [f"Found {duplicates} duplicate rows"]
                logger.warning(f"[{api_name}] Found {duplicates} duplicate rows")
                
        # 记录验证结果
        self.validation_results.append(result)
        
        if not result["valid"]:
            logger.warning(f"[{api_name}] Validation failed: {result['errors']}")
        else:
            logger.debug(f"[{api_name}] Validation passed: {len(df)} rows")
            
        return result["valid"]
        
    def calculate_checksum(self, df: pd.DataFrame) -> str:
        """
        计算 DataFrame 的校验和
        
        Args:
            df: DataFrame
            
        Returns:
            MD5 校验和
        """
        if df is None or len(df) == 0:
            return ""
        # 使用 DataFrame 的字符串表示计算校验和
        data_str = df.to_csv(index=False)
        return hashlib.md5(data_str.encode()).hexdigest()
        
    def validate_parquet_file(
        self,
        file_path: Path,
        expected_row_count: Optional[int] = None
    ) -> bool:
        """
        验证 Parquet 文件完整性
        
        Args:
            file_path: Parquet 文件路径
            expected_row_count: 期望的行数
            
        Returns:
            是否验证通过
        """
        try:
            df = pd.read_parquet(file_path)
            
            if expected_row_count is not None and len(df) != expected_row_count:
                logger.warning(
                    f"Row count mismatch for {file_path}: "
                    f"expected {expected_row_count}, got {len(df)}"
                )
                return False
                
            return True
        except Exception as e:
            logger.error(f"Failed to validate {file_path}: {e}")
            return False
            
    def get_summary(self) -> Dict[str, Any]:
        """获取验证结果摘要"""
        total = len(self.validation_results)
        passed = sum(1 for r in self.validation_results if r["valid"])
        failed = total - passed
        total_rows = sum(r["row_count"] for r in self.validation_results)
        
        return {
            "total_validations": total,
            "passed": passed,
            "failed": failed,
            "total_rows": total_rows,
            "pass_rate": (passed / total * 100) if total > 0 else 0
        }
        
    def clear(self) -> None:
        """清除验证结果"""
        self.validation_results = []


class DownloadProgress:
    """下载进度追踪器"""
    
    def __init__(self):
        self.total_tasks = 0
        self.completed_tasks = 0
        self.skipped_tasks = 0
        self.failed_tasks = 0
        self.total_rows = 0
        self.task_details: List[Dict[str, Any]] = []
        self.lock = __import__('threading').Lock()
        
    def add_total(self, count: int) -> None:
        """增加总任务数"""
        with self.lock:
            self.total_tasks += count
            
    def record_success(self, api_name: str, params: dict, rows: int) -> None:
        """记录成功的任务"""
        with self.lock:
            self.completed_tasks += 1
            self.total_rows += rows
            self.task_details.append({
                "api_name": api_name,
                "params": params,
                "status": "success",
                "rows": rows
            })
            
    def record_skip(self, api_name: str, params: dict, reason: str) -> None:
        """记录跳过的任务"""
        with self.lock:
            self.skipped_tasks += 1
            self.task_details.append({
                "api_name": api_name,
                "params": params,
                "status": "skipped",
                "reason": reason
            })
            
    def record_failure(self, api_name: str, params: dict, error: str) -> None:
        """记录失败的任务"""
        with self.lock:
            self.failed_tasks += 1
            self.task_details.append({
                "api_name": api_name,
                "params": params,
                "status": "failed",
                "error": error
            })
            
    def get_progress(self) -> Dict[str, Any]:
        """获取当前进度"""
        with self.lock:
            processed = self.completed_tasks + self.skipped_tasks + self.failed_tasks
            return {
                "total": self.total_tasks,
                "processed": processed,
                "completed": self.completed_tasks,
                "skipped": self.skipped_tasks,
                "failed": self.failed_tasks,
                "total_rows": self.total_rows,
                "progress_pct": (processed / self.total_tasks * 100) if self.total_tasks > 0 else 0
            }
            
    def print_progress(self) -> None:
        """打印进度信息"""
        progress = self.get_progress()
        print(
            f"\r进度: {progress['processed']}/{progress['total']} "
            f"({progress['progress_pct']:.1f}%) | "
            f"成功: {progress['completed']} | "
            f"跳过: {progress['skipped']} | "
            f"失败: {progress['failed']} | "
            f"总行数: {progress['total_rows']:,}",
            end="",
            flush=True
        )
