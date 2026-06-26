"""
ChipDataLoader - 内存受限的筹码数据加载器。

加载 cyq_perf（筹码及胜率）parquet，提供 5 档成本分位 + 加权平均成本 +
获利盘比例，并用 adj_factor 进行复权校正。支持单只加载与分批流式处理。

数据基础说明：cyq_chips 实测为每交易日单点位（price+集中度标量），无法支撑
多桶因子数学；改用 cyq_perf 的离散分位分布。详见
docs/chip-peak-cyqperf-refactor.md。

数据布局（按 ts_code 分区的 parquet）:
    {data_folder}/cyq_perf/ts_code={ts_code}/data.parquet
    {data_folder}/adj_factor/ts_code={ts_code}/data.parquet
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Generator, Optional, Tuple

import pandas as pd

# cyq_perf 中需要复权校正的成本字段（原始价位，需 × adj_factor）
_COST_FIELDS = (
    'cost_5pct',
    'cost_15pct',
    'cost_50pct',
    'cost_85pct',
    'cost_95pct',
    'weight_avg',
)


class ChipDataLoader:
    """
    内存受限的筹码数据加载器（cyq_perf）。
    惰性加载 + 复权校正 + 分批流式处理。
    """

    def __init__(
        self,
        data_folder: str,
        cyq_dir: str = 'cyq_perf',
        adj_dir: str = 'adj_factor',
        max_batch_size: int = 500,
    ):
        """
        初始化筹码数据加载器。

        Args:
            data_folder: tushare 数据根目录 (如 tushare_data_v2)
            cyq_dir: 筹码数据子目录名（默认 cyq_perf）
            adj_dir: 复权因子子目录名
            max_batch_size: 单批最大股票数 (内存保护)
        """
        self.base_path = Path(data_folder)
        self.cyq_path = self.base_path / cyq_dir
        self.adj_path = self.base_path / adj_dir
        self.max_batch_size = max_batch_size
        # 缓存 adj_factor (DataFrame 较小，可安全常驻)
        self._adj_cache: Dict[str, Optional[pd.DataFrame]] = {}

    def load_single(self, ts_code: str, trade_date: str) -> Optional[pd.Series]:
        """
        加载单只股票的筹码分布（cyq_perf 单行 + 复权校正）。

        Args:
            ts_code: Tushare 股票代码，如 '600519.SH'
            trade_date: 交易日期，YYYYMMDD 字符串，如 '20231113'

        Returns:
            pd.Series，关键字段:
              - cost_5pct_adj .. cost_95pct_adj: 复权校正后的 5 档成本分位
              - weight_avg_adj: 复权校正后的加权平均成本
              - winner_rate: 获利盘比例 %（0-100，复权不变）
              - trade_date: 实际命中的交易日
            数据缺失返回 None。
        """
        cyq_file = self.cyq_path / f'ts_code={ts_code}' / 'data.parquet'
        if not cyq_file.exists():
            return None

        try:
            df = pd.read_parquet(cyq_file)
            df['trade_date'] = df['trade_date'].astype(str)
            trade_date = str(trade_date)

            # 优先精确匹配 trade_date，否则回退到 <= trade_date 的最近交易日
            mask = df['trade_date'] == trade_date
            if not mask.any():
                recent = df[df['trade_date'] <= trade_date]
                if len(recent) == 0:
                    return None
                latest = recent['trade_date'].max()
                row = df[df['trade_date'] == latest].iloc[0]
            else:
                row = df[mask].iloc[0]

            adj_factor = self._adj_factor_for(ts_code, str(row['trade_date']))

            out = pd.Series(dtype=object)
            # 复权校正成本字段
            for f in _COST_FIELDS:
                if f in row.index:
                    out[f'{f}_adj'] = float(row[f]) * adj_factor
                else:
                    out[f'{f}_adj'] = float('nan')
            # 获利盘比例（复权不变）
            out['winner_rate'] = float(row['winner_rate']) if 'winner_rate' in row.index else float('nan')
            out['trade_date'] = str(row['trade_date'])
            return out
        except Exception:
            # 数据损坏或读取异常，返回 None 让上层处理
            return None

    def _adj_factor_for(self, ts_code: str, trade_date: str) -> float:
        """取指定交易日的复权因子（缺失或异常回退到 1.0）。"""
        adj_df = self._load_adj_factor(ts_code)
        if adj_df is None or len(adj_df) == 0:
            return 1.0
        adj_row = adj_df[adj_df['trade_date'] == trade_date]
        if len(adj_row) == 0:
            recent_adj = adj_df[adj_df['trade_date'] <= trade_date]
            if len(recent_adj) > 0:
                return float(recent_adj.sort_values('trade_date').iloc[-1]['adj_factor'])
            return 1.0
        return float(adj_row.iloc[0]['adj_factor'])

    def _load_adj_factor(self, ts_code: str) -> Optional[pd.DataFrame]:
        """加载复权因子（带缓存）。"""
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
    ) -> Generator[Tuple[str, pd.Series], None, None]:
        """
        流式加载一批股票（生成器）。
        每次yield后，调用方处理完Series，下次循环前被释放（内存安全）。

        Args:
            ts_codes: 股票代码列表
            trade_date: 交易日期 YYYYMMDD

        Yields:
            Tuple[ts_code, pd.Series] - 逐只产出
        """
        batch = ts_codes[: self.max_batch_size]
        for ts_code in batch:
            try:
                row = self.load_single(ts_code, trade_date)
                if row is not None:
                    yield ts_code, row
            except Exception:
                continue
