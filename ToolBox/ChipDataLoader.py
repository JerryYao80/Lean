"""
ChipDataLoader - 内存受限的筹码数据加载器。

惰性加载 cyq_chips (筹码分布) parquet 文件，并用 adj_factor 进行复权校正。
支持单只股票加载和分批流式处理。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Generator, Optional, Tuple

import pandas as pd


class ChipDataLoader:
    """
    内存受限的筹码数据加载器。
    惰性加载 + 复权校正 + 分批流式处理。

    数据布局（按 ts_code 分区的 parquet）:
        {data_folder}/cyq_chips/ts_code={ts_code}/data.parquet
        {data_folder}/adj_factor/ts_code={ts_code}/data.parquet
    """

    def __init__(
        self,
        data_folder: str,
        cyq_dir: str = 'cyq_chips',
        adj_dir: str = 'adj_factor',
        max_batch_size: int = 500,
    ):
        """
        初始化筹码数据加载器。

        Args:
            data_folder: tushare 数据根目录 (如 tushare_data_v2)
            cyq_dir: 筹码数据子目录名
            adj_dir: 复权因子子目录名
            max_batch_size: 单批最大股票数 (内存保护)
        """
        self.base_path = Path(data_folder)
        self.cyq_path = self.base_path / cyq_dir
        self.adj_path = self.base_path / adj_dir
        self.max_batch_size = max_batch_size
        # 缓存 adj_factor (DataFrame 较小，6021 行 ~ 几百 KB)
        self._adj_cache: Dict[str, Optional[pd.DataFrame]] = {}

    def load_single(self, ts_code: str, trade_date: str) -> Optional[pd.DataFrame]:
        """
        加载单只股票的筹码分布（含复权校正）。

        Args:
            ts_code: Tushare 股票代码，如 '600519.SH'
            trade_date: 交易日期，YYYYMMDD 字符串，如 '20231113'

        Returns:
            DataFrame[price, percent, corrected_price, trade_date]，或 None（数据缺失）
        """
        cyq_file = self.cyq_path / f'ts_code={ts_code}' / 'data.parquet'
        if not cyq_file.exists():
            return None

        try:
            df = pd.read_parquet(cyq_file)
            # 统一 trade_date 为字符串以便比较
            df['trade_date'] = df['trade_date'].astype(str)
            trade_date = str(trade_date)

            # 优先精确匹配 trade_date
            mask = df['trade_date'] == trade_date
            if not mask.any():
                # 回退：取 <= trade_date 的最近一个交易日
                recent = df[df['trade_date'] <= trade_date]
                if len(recent) == 0:
                    return None
                latest = recent['trade_date'].max()
                df = df[df['trade_date'] == latest]
            else:
                df = df[mask]

            if len(df) == 0:
                return None

            # 复权校正
            adj_df = self._load_adj_factor(ts_code)
            df = df.copy()

            if adj_df is not None and len(adj_df) > 0:
                adj_row = adj_df[adj_df['trade_date'] == trade_date]
                if len(adj_row) == 0:
                    # adj_factor 也回退到最近交易日
                    recent_adj = adj_df[adj_df['trade_date'] <= trade_date]
                    if len(recent_adj) > 0:
                        adj_factor = float(
                            recent_adj.sort_values('trade_date').iloc[-1]['adj_factor']
                        )
                    else:
                        adj_factor = 1.0
                else:
                    adj_factor = float(adj_row.iloc[0]['adj_factor'])

                df['corrected_price'] = df['price'] * adj_factor
            else:
                # 无复权因子，直接使用原价
                df['corrected_price'] = df['price']

            return df[
                ['price', 'percent', 'corrected_price', 'trade_date']
            ].reset_index(drop=True)
        except Exception:
            # 数据损坏或读取异常，返回 None 让上层处理
            return None

    def _load_adj_factor(self, ts_code: str) -> Optional[pd.DataFrame]:
        """
        加载复权因子（带缓存）。

        Args:
            ts_code: Tushare 股票代码

        Returns:
            DataFrame[ts_code, trade_date, adj_factor]，或 None（文件缺失）
        """
        if ts_code in self._adj_cache:
            return self._adj_cache[ts_code]

        adj_file = self.adj_path / f'ts_code={ts_code}' / 'data.parquet'
        if not adj_file.exists():
            self._adj_cache[ts_code] = None
            return None

        try:
            df = pd.read_parquet(adj_file)
            df['trade_date'] = df['trade_date'].astype(str)
            self._adj_cache[ts_code] = df
            return df
        except Exception:
            self._adj_cache[ts_code] = None
            return None

    def load_batch(
        self, ts_codes: list, trade_date: str
    ) -> Generator[Tuple[str, pd.DataFrame], None, None]:
        """
        流式加载一批股票（生成器）。

        按 max_batch_size 分批，惰性 yield (ts_code, DataFrame)，避免一次性载入内存。

        Args:
            ts_codes: 股票代码列表
            trade_date: 交易日期 YYYYMMDD

        Yields:
            Tuple[ts_code, DataFrame] - 逐只产出

        Note:
            Task 2 实现具体逻辑。
        """
        pass  # Task 2 implements
