"""
Tushare 数据下载引擎

核心功能:
- 并发下载
- 令牌桶限流
- 断点续传
- 自动重试
- 数据校验
"""
import os
import time
import logging
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

import tushare as ts
import pandas as pd

import config
from rate_limiter import get_rate_limiter, RateLimiter
from data_validator import DataValidator, DownloadProgress
from api_registry import (
    APIConfig, ChunkStrategy, get_api_config, 
    get_enabled_apis, ALL_APIS
)

logger = logging.getLogger(__name__)


class TushareDownloader:
    """Tushare 数据下载器"""
    
    def __init__(
        self,
        data_dir: Optional[str] = None,
        max_workers: Optional[int] = None,
        rate_limiter: Optional[RateLimiter] = None
    ):
        """
        初始化下载器
        
        Args:
            data_dir: 数据存储目录
            max_workers: 最大线程数
            rate_limiter: 限流器实例
        """
        self.data_dir = Path(data_dir or config.DATA_DIR)
        self.max_workers = max_workers or config.MAX_WORKERS
        self.rate_limiter = rate_limiter or get_rate_limiter()
        self.validator = DataValidator()
        self.progress = DownloadProgress()
        
        # 初始化 Tushare API
        self._init_tushare_api()
        
        # 创建数据目录
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # 线程锁
        self._api_lock = threading.Lock()
        self._call_context = threading.local()
        
    def _init_tushare_api(self) -> None:
        """初始化 Tushare API 连接"""
        self.pro = ts.pro_api(
            config.TUSHARE_TOKEN,
            timeout=config.TUSHARE_TIMEOUT_SECONDS
        )
        self.pro._DataApi__token = config.TUSHARE_TOKEN
        self.pro._DataApi__http_url = config.TUSHARE_API_URL
        self.pro._DataApi__timeout = config.TUSHARE_TIMEOUT_SECONDS
        logger.info(
            "Tushare API initialized with URL: %s, timeout=%ss",
            config.TUSHARE_API_URL,
            config.TUSHARE_TIMEOUT_SECONDS
        )

    def _is_timeout_error(self, error: Exception) -> bool:
        """判断是否为超时错误"""
        error_text = f"{type(error).__name__}: {error}".lower()
        timeout_markers = (
            "timed out",
            "read timed out",
            "connecttimeout",
            "readtimeout",
            "timeout",
        )
        return any(marker in error_text for marker in timeout_markers)

    def _set_last_call_result(self, error_type: Optional[str] = None, message: str = "") -> None:
        if not hasattr(self, "_call_context"):
            self._call_context = threading.local()
        self._call_context.last_error = {
            "type": error_type,
            "message": message,
        }

    def get_last_call_result(self) -> Dict[str, str]:
        if not hasattr(self, "_call_context"):
            self._call_context = threading.local()
        return getattr(self._call_context, "last_error", {"type": None, "message": ""})
        
    def _get_file_path(
        self,
        api_name: str,
        year: Optional[int] = None,
        quarter: Optional[str] = None,
        date: Optional[str] = None,
        ts_code: Optional[str] = None
    ) -> Path:
        """
        获取数据文件路径

        按照分区目录结构存储:
        - 无分块: {data_dir}/{api_name}/data.parquet
        - 按年: {data_dir}/{api_name}/year={year}/data.parquet
        - 按季度: {data_dir}/{api_name}/quarter={quarter}/data.parquet
        - 按交易日: {data_dir}/{api_name}/trade_date={date}/data.parquet
        - 按代码: {data_dir}/{api_name}/ts_code={ts_code}/data.parquet

        Note: DATE-strategy partitions use the `trade_date=` key (not `date=`) to
        disambiguate from historical residue where `date=<symbol>` directories were
        created by an old call pattern. `date` must be an 8-digit YYYYMMDD string.
        """
        base_path = self.data_dir / api_name

        if ts_code is not None:
            return base_path / f"ts_code={ts_code}" / "data.parquet"
        elif year is not None:
            return base_path / f"year={year}" / "data.parquet"
        elif quarter is not None:
            return base_path / f"quarter={quarter}" / "data.parquet"
        elif date is not None:
            if not (isinstance(date, str) and len(date) == 8 and date.isdigit()):
                raise ValueError(
                    f"date must be an 8-digit YYYYMMDD string, got: {date!r}"
                )
            return base_path / f"trade_date={date}" / "data.parquet"
        else:
            return base_path / "data.parquet"
            
    def _file_exists(self, file_path: Path) -> bool:
        """检查文件是否存在（断点续传检查）"""
        return file_path.exists() and file_path.stat().st_size > 0
        
    def _save_to_parquet(self, df: pd.DataFrame, file_path: Path) -> bool:
        """
        保存 DataFrame 到 Parquet 文件
        
        Args:
            df: 数据
            file_path: 文件路径
            
        Returns:
            是否保存成功
        """
        try:
            # 创建目录
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 保存为 Parquet
            df.to_parquet(file_path, engine='pyarrow', index=False)
            logger.debug(f"Saved {len(df)} rows to {file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save {file_path}: {e}")
            return False
            
    def _call_api_with_retry(
        self,
        api_name: str,
        **kwargs
    ) -> Optional[pd.DataFrame]:
        """
        调用 API 并自动重试
        
        Args:
            api_name: API 名称
            **kwargs: API 参数
            
        Returns:
            DataFrame 或 None
        """
        timeout_retry_limit = max(1, min(config.TIMEOUT_MAX_RETRIES, config.MAX_RETRIES))
        self._set_last_call_result()

        for attempt in range(config.MAX_RETRIES):
            try:
                # 等待获取令牌
                self.rate_limiter.wait_for_token()
                
                # 调用 API
                with self._api_lock:
                    api_func = getattr(self.pro, api_name, None)
                    if api_func is None:
                        # 尝试使用 query 方法
                        df = self.pro.query(api_name, **kwargs)
                    else:
                        df = api_func(**kwargs)

                self._set_last_call_result()
                return df
                
            except Exception as e:
                error_msg = str(e).lower()
                
                # 检查是否是权限问题（不需要重试）
                if "权限" in str(e) or "permission" in error_msg:
                    logger.warning(f"[{api_name}] Permission denied: {e}")
                    self._set_last_call_result("permission_denied", str(e))
                    return None

                if "请指定正确的接口名" in str(e) or "not found" in error_msg or "不存在" in str(e):
                    logger.error(f"[{api_name}] API not found, skip current task: {e}")
                    self._set_last_call_result("api_not_found", str(e))
                    return None

                is_timeout_error = self._is_timeout_error(e)
                retry_limit = timeout_retry_limit if is_timeout_error else config.MAX_RETRIES

                if attempt + 1 >= retry_limit:
                    if is_timeout_error:
                        logger.error(
                            f"[{api_name}] Timeout after {retry_limit} attempts, skip current task: {e}"
                        )
                        self._set_last_call_result("timeout", str(e))
                    else:
                        logger.error(
                            f"[{api_name}] All {config.MAX_RETRIES} attempts failed: {e}"
                        )
                        self._set_last_call_result("api_error", str(e))
                    return None

                if is_timeout_error:
                    delay = config.TIMEOUT_RETRY_DELAY
                    logger.warning(
                        f"[{api_name}] Timeout on attempt {attempt + 1}/{retry_limit}: {e}. "
                        f"Retrying quickly in {delay:.1f}s..."
                    )
                else:
                    delay = min(
                        config.BASE_RETRY_DELAY * (2 ** attempt),
                        config.MAX_RETRY_DELAY
                    )
                    logger.warning(
                        f"[{api_name}] Attempt {attempt + 1} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )

                time.sleep(delay)

        self._set_last_call_result("api_error", "Unknown API failure")
        return None
        
    def _generate_year_ranges(self) -> List[int]:
        """生成年份范围"""
        return list(range(config.START_YEAR, config.END_YEAR + 1))
        
    def _generate_quarter_ranges(self) -> List[str]:
        """生成季度范围 (格式: YYYYMMDD)"""
        quarters = []
        for year in range(config.START_YEAR, config.END_YEAR + 1):
            for q in [("0331", "Q1"), ("0630", "Q2"), ("0930", "Q3"), ("1231", "Q4")]:
                quarters.append(f"{year}{q[0]}")
        return quarters
        
    def _generate_date_ranges(self, year: int) -> List[str]:
        """生成日期范围（某年的所有交易日）"""
        dates = []
        start = datetime(year, 1, 1)
        end = datetime(year, 12, 31)
        current = start
        while current <= end:
            if current.weekday() < 5:
                dates.append(current.strftime("%Y%m%d"))
            current = current + pd.Timedelta(days=1)
        return dates
    
    def _get_stock_list(self, category: str) -> List[str]:
        """获取股票/基金/指数列表"""
        try:
            if "fund" in category or "etf" in category:
                df = self.pro.fund_basic(market="E")
                return df['ts_code'].tolist() if df is not None and len(df) > 0 else []
            elif "index" in category:
                # 优先从本地 index_basic 文件读取指数代码
                local_index_file = self.data_dir / "index_basic" / "data.parquet"
                if local_index_file.exists():
                    try:
                        df = pd.read_parquet(local_index_file, engine='pyarrow')
                        if df is not None and len(df) > 0 and 'ts_code' in df.columns:
                            codes = df['ts_code'].tolist()
                            logger.info(f"Loaded {len(codes)} index codes from local file: {local_index_file}")
                            return codes
                    except Exception as e:
                        logger.warning(f"Failed to read local index_basic file: {e}")

                # 回退到 API 调用
                markets = ["SSE", "SZSE", "MSCI", "CSI", "CICC", "SW", "OTH"]
                all_codes = []
                for market in markets:
                    try:
                        df = self.pro.index_basic(market=market)
                        if df is not None and len(df) > 0:
                            all_codes.extend(df['ts_code'].tolist())
                    except Exception as e:
                        logger.debug(f"Failed to fetch index list for market {market}: {e}")
                return all_codes
            else:
                df = self.pro.stock_basic()
                return df['ts_code'].tolist() if df is not None and len(df) > 0 else []
        except Exception as e:
            logger.warning(f"Failed to fetch stock/fund/index list for category {category}: {e}")
            return []
        
    def download_api_by_stock(
        self,
        api_config: APIConfig,
        ts_code: str
    ) -> Tuple[bool, int]:
        """
        按股票/基金代码下载 API 数据

        Returns:
            (成功与否, 行数)
        """
        api_name = api_config.api_name
        code_field = api_config.code_field or "ts_code"
        file_path = self._get_file_path(api_name, ts_code=ts_code)

        if self._file_exists(file_path):
            self.progress.record_skip(api_name, {code_field: ts_code}, "File exists")
            return True, 0

        params = api_config.required_params.copy()
        params[code_field] = ts_code

        df = self._call_api_with_retry(api_name, **params)

        if df is None:
            self.progress.record_failure(api_name, params, "API call failed")
            return False, 0

        if len(df) == 0:
            self._save_to_parquet(pd.DataFrame(), file_path)
            self.progress.record_success(api_name, params, 0)
            return True, 0

        # 检测是否达到 8000 行限制（Tushare API 单次返回上限）
        if len(df) >= 8000:
            logger.warning(
                f"[{api_name}][{ts_code}] Hit 8000 row limit, switching to yearly chunks"
            )
            # 对于指数数据，使用更合理的年份范围
            # 如果是 index_daily 类的接口，从数据中的最早日期推断开始年份
            if len(df) > 0 and 'trade_date' in df.columns:
                # 从现有数据的最早日期开始，避免下载过多无用数据
                min_date = df['trade_date'].min()
                start_year = int(str(min_date)[:4])
                # 确保不会漏掉之前的数据（因为第一次调用只返回最近8000行）
                # 使用更合理的起始年份（至少10年前）
                reasonable_start = max(config.START_YEAR, datetime.now().year - 15)
                start_year = min(start_year, reasonable_start)
            else:
                start_year = max(config.START_YEAR, datetime.now().year - 10)

            all_yearly_data = []
            for year in range(start_year, config.END_YEAR + 1):
                year_params = api_config.required_params.copy()
                year_params[code_field] = ts_code
                year_params["start_date"] = f"{year}0101"
                year_params["end_date"] = f"{year}1231"

                year_df = self._call_api_with_retry(api_name, **year_params)
                if year_df is not None and len(year_df) > 0:
                    all_yearly_data.append(year_df)

                    # 如果单年数据也超过 8000 行，进一步按月拆分
                    if len(year_df) >= 8000:
                        logger.error(
                            f"[{api_name}][{ts_code}][{year}] Still hit 8000 row limit! "
                            "Data may be incomplete."
                        )

            if all_yearly_data:
                df = pd.concat(all_yearly_data, ignore_index=True)
            else:
                df = pd.DataFrame()

        self.validator.validate_dataframe(df, api_name)

        if self._save_to_parquet(df, file_path):
            self.progress.record_success(api_name, params, len(df))
            logger.info(f"[{api_name}][{ts_code}] Downloaded {len(df)} rows")
            return True, len(df)
        else:
            self.progress.record_failure(api_name, params, "Save failed")
            return False, 0
    
    def download_api_no_chunk(
        self,
        api_config: APIConfig
    ) -> Tuple[bool, int]:
        api_name = api_config.api_name
        file_path = self._get_file_path(api_name)
        
        if self._file_exists(file_path):
            self.progress.record_skip(api_name, {}, "File exists")
            return True, 0
            
        params = api_config.required_params.copy()
        df = self._call_api_with_retry(api_name, **params)
        
        if df is None:
            self.progress.record_failure(api_name, params, "API call failed")
            return False, 0
            
        if len(df) == 0:
            self._save_to_parquet(pd.DataFrame(), file_path)
            self.progress.record_success(api_name, params, 0)
            return True, 0
            
        self.validator.validate_dataframe(df, api_name)
        
        if self._save_to_parquet(df, file_path):
            self.progress.record_success(api_name, params, len(df))
            logger.info(f"[{api_name}] Downloaded {len(df)} rows")
            return True, len(df)
        else:
            self.progress.record_failure(api_name, params, "Save failed")
            return False, 0
    
    def download_api_by_year(
        self,
        api_config: APIConfig,
        year: int
    ) -> Tuple[bool, int]:
        api_name = api_config.api_name
        file_path = self._get_file_path(api_name, year=year)
        
        if self._file_exists(file_path):
            self.progress.record_skip(api_name, {"year": year}, "File exists")
            return True, 0
            
        params = api_config.required_params.copy()
        params["start_date"] = f"{year}0101"
        params["end_date"] = f"{year}1231"
        
        df = self._call_api_with_retry(api_name, **params)
        
        if df is None:
            self.progress.record_failure(api_name, params, "API call failed")
            return False, 0
            
        if len(df) == 0:
            self._save_to_parquet(pd.DataFrame(), file_path)
            self.progress.record_success(api_name, params, 0)
            return True, 0
        
        if len(df) >= 2000:
            logger.warning(
                f"[{api_name}][{year}] Hit 2000 row limit, switching to monthly chunks"
            )
            all_monthly_data = []
            for month in range(1, 13):
                month_start = f"{year}{month:02d}01"
                last_day = 31 if month in [1,3,5,7,8,10,12] else (30 if month != 2 else 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28)
                month_end = f"{year}{month:02d}{last_day}"
                
                month_params = api_config.required_params.copy()
                month_params["start_date"] = month_start
                month_params["end_date"] = month_end
                
                month_df = self._call_api_with_retry(api_name, **month_params)
                if month_df is not None and len(month_df) > 0:
                    all_monthly_data.append(month_df)
                    
                    if len(month_df) >= 2000:
                        logger.error(
                            f"[{api_name}][{year}-{month:02d}] Still hit 2000 row limit! "
                            "Consider using DATE chunking strategy instead."
                        )
            
            if all_monthly_data:
                df = pd.concat(all_monthly_data, ignore_index=True)
            else:
                df = pd.DataFrame()
            
        self.validator.validate_dataframe(df, api_name)
        
        if self._save_to_parquet(df, file_path):
            self.progress.record_success(api_name, params, len(df))
            logger.info(f"[{api_name}][{year}] Downloaded {len(df)} rows")
            return True, len(df)
        else:
            self.progress.record_failure(api_name, params, "Save failed")
            return False, 0
    
    def download_api_by_quarter(
        self,
        api_config: APIConfig,
        quarter: str
    ) -> Tuple[bool, int]:
        api_name = api_config.api_name
        file_path = self._get_file_path(api_name, quarter=quarter)
        
        if self._file_exists(file_path):
            self.progress.record_skip(api_name, {"quarter": quarter}, "File exists")
            return True, 0
            
        params = api_config.required_params.copy()
        params["period"] = quarter
        
        df = self._call_api_with_retry(api_name, **params)
        
        if df is None:
            self.progress.record_failure(api_name, params, "API call failed")
            return False, 0
            
        if len(df) == 0:
            self._save_to_parquet(pd.DataFrame(), file_path)
            self.progress.record_success(api_name, params, 0)
            return True, 0
            
        self.validator.validate_dataframe(df, api_name)
        
        if self._save_to_parquet(df, file_path):
            self.progress.record_success(api_name, params, len(df))
            logger.info(f"[{api_name}][{quarter}] Downloaded {len(df)} rows")
            return True, len(df)
        else:
            self.progress.record_failure(api_name, params, "Save failed")
            return False, 0
    
    def download_api_by_date(
        self,
        api_config: APIConfig,
        date: str
    ) -> Tuple[bool, int]:
        api_name = api_config.api_name
        file_path = self._get_file_path(api_name, date=date)
        
        if self._file_exists(file_path):
            self.progress.record_skip(api_name, {"date": date}, "File exists")
            return True, 0
            
        params = api_config.required_params.copy()
        params["trade_date"] = date
        
        df = self._call_api_with_retry(api_name, **params)
        
        if df is None:
            self.progress.record_failure(api_name, params, "API call failed")
            return False, 0
            
        if len(df) == 0:
            self._save_to_parquet(pd.DataFrame(), file_path)
            self.progress.record_success(api_name, params, 0)
            return True, 0
            
        self.validator.validate_dataframe(df, api_name)
        
        if self._save_to_parquet(df, file_path):
            self.progress.record_success(api_name, params, len(df))
            logger.info(f"[{api_name}][{date}] Downloaded {len(df)} rows")
            return True, len(df)
        else:
            self.progress.record_failure(api_name, params, "Save failed")
            return False, 0

    def _create_download_tasks(
        self,
        api_configs: List[APIConfig]
    ) -> List[Tuple[APIConfig, dict]]:
        """
        创建下载任务列表
        
        Returns:
            [(api_config, params), ...]
        """
        tasks = []
        
        for api_config in api_configs:
            if not api_config.enabled:
                continue
                
            if api_config.chunk_strategy == ChunkStrategy.NONE:
                tasks.append((api_config, {}))
                
            elif api_config.chunk_strategy == ChunkStrategy.YEAR:
                for year in self._generate_year_ranges():
                    tasks.append((api_config, {"year": year}))
                    
            elif api_config.chunk_strategy == ChunkStrategy.QUARTER:
                for quarter in self._generate_quarter_ranges():
                    tasks.append((api_config, {"quarter": quarter}))
                    
            elif api_config.chunk_strategy == ChunkStrategy.DATE:
                for year in range(max(2020, config.START_YEAR), config.END_YEAR + 1):
                    for date in self._generate_date_ranges(year):
                        tasks.append((api_config, {"date": date}))
                        
            elif api_config.chunk_strategy == ChunkStrategy.STOCK:
                stock_list = self._get_stock_list(api_config.category)
                for ts_code in stock_list:
                    tasks.append((api_config, {"ts_code": ts_code}))
                        
        return tasks
        
    def _execute_task(
        self,
        api_config: APIConfig,
        params: dict
    ) -> Tuple[bool, int]:
        """执行单个下载任务"""
        try:
            if api_config.chunk_strategy == ChunkStrategy.NONE:
                return self.download_api_no_chunk(api_config)
            elif api_config.chunk_strategy == ChunkStrategy.YEAR:
                return self.download_api_by_year(api_config, params["year"])
            elif api_config.chunk_strategy == ChunkStrategy.QUARTER:
                return self.download_api_by_quarter(api_config, params["quarter"])
            elif api_config.chunk_strategy == ChunkStrategy.DATE:
                return self.download_api_by_date(api_config, params["date"])
            elif api_config.chunk_strategy == ChunkStrategy.STOCK:
                return self.download_api_by_stock(api_config, params["ts_code"])
            else:
                return False, 0
        except Exception as e:
            logger.error(f"Task failed: {api_config.api_name} {params}: {e}")
            self.progress.record_failure(api_config.api_name, params, str(e))
            return False, 0
            
    def download_all(
        self,
        api_names: Optional[List[str]] = None,
        categories: Optional[List[str]] = None,
        priority: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        下载所有数据
        
        Args:
            api_names: 指定要下载的 API 名称列表
            categories: 指定要下载的类别列表
            priority: 指定优先级 (1, 2, 3)
            
        Returns:
            下载统计信息
        """
        # 筛选 API
        if api_names:
            api_configs = [
                get_api_config(name) for name in api_names 
                if get_api_config(name) is not None
            ]
        else:
            api_configs = get_enabled_apis()
            
        if categories:
            api_configs = [api for api in api_configs if api.category in categories]
            
        if priority is not None:
            api_configs = [api for api in api_configs if api.priority == priority]
            
        logger.info(f"Preparing to download {len(api_configs)} APIs...")
        
        # 创建任务
        tasks = self._create_download_tasks(api_configs)
        self.progress.add_total(len(tasks))
        
        logger.info(f"Created {len(tasks)} download tasks")
        
        # 并发执行
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._execute_task, api_config, params): (api_config, params)
                for api_config, params in tasks
            }
            
            for future in as_completed(futures):
                api_config, params = futures[future]
                try:
                    success, rows = future.result()
                except Exception as e:
                    logger.error(f"Task error: {api_config.api_name}: {e}")
                    
                # 打印进度
                self.progress.print_progress()
                
        print()  # 换行
        
        elapsed = time.time() - start_time
        
        # 统计信息
        stats = self.progress.get_progress()
        stats["elapsed_seconds"] = elapsed
        stats["rate_limiter_stats"] = self.rate_limiter.get_stats()
        stats["validation_summary"] = self.validator.get_summary()
        
        logger.info(f"Download completed in {elapsed:.1f}s")
        logger.info(f"Stats: {stats}")
        
        return stats
        
    def download_single_api(
        self,
        api_name: str,
        year: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        下载单个 API 的数据
        
        Args:
            api_name: API 名称
            year: 可选年份（用于分块下载）
            
        Returns:
            下载统计
        """
        api_config = get_api_config(api_name)
        if api_config is None:
            raise ValueError(f"Unknown API: {api_name}")
            
        if year is not None:
            success, rows = self.download_api_by_year(api_config, year)
        else:
            if api_config.chunk_strategy == ChunkStrategy.NONE:
                success, rows = self.download_api_no_chunk(api_config)
            elif api_config.chunk_strategy == ChunkStrategy.YEAR:
                # 下载所有年份
                total_rows = 0
                for y in self._generate_year_ranges():
                    s, r = self.download_api_by_year(api_config, y)
                    total_rows += r
                success = True
                rows = total_rows
            else:
                success, rows = self.download_api_no_chunk(api_config)
                
        return {
            "api_name": api_name,
            "success": success,
            "rows": rows
        }
