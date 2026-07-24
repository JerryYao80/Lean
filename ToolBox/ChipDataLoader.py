"""
ChipDataLoader - 内存受限的筹码数据加载器。

加载 cyq_perf（筹码及胜率）parquet，提供 5 档成本分位 + 加权平均成本 +
获利盘比例，并用 adj_factor 进行复权校正。支持单只加载与分批流式处理。

增强模式（enriched_*）：额外加载 moneyflow（资金流向）+ daily_basic（估值）
两类辅助信号，用于筹码因子的多因子融合。两类数据均按 ts_code 分区、无需复权。

数据基础说明：cyq_chips 实测为每交易日单点位（price+集中度标量），无法支撑
多桶因子数学；改用 cyq_perf 的离散分位分布。详见
docs/chip-peak-cyqperf-refactor.md。

数据布局（按 ts_code 分区的 parquet）:
    {data_folder}/cyq_perf/ts_code={ts_code}/data.parquet
    {data_folder}/adj_factor/ts_code={ts_code}/data.parquet
    {data_folder}/moneyflow/ts_code={ts_code}/data.parquet      (可选, 增强模式)
    {data_folder}/daily_basic/ts_code={ts_code}/data.parquet    (可选, 增强模式)

注意：实测 moneyflow_hsgt（北向资金）parquet 为空（0 行），不可用，故不接入。
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        mf_dir: str = 'moneyflow',
        db_dir: str = 'daily_basic',
        max_batch_size: int = 500,
        max_workers: Optional[int] = None,
    ):
        """
        初始化筹码数据加载器。

        Args:
            data_folder: tushare 数据根目录 (如 tushare_data_v2)
            cyq_dir: 筹码数据子目录名（默认 cyq_perf）
            adj_dir: 复权因子子目录名
            mf_dir: 资金流向子目录名（增强模式用，默认 moneyflow）
            db_dir: 估值指标子目录名（增强模式用，默认 daily_basic）
            max_batch_size: 单批最大股票数 (内存保护)
            max_workers: 线程池并行度。None=自动 (min(8, cpu_count))。
                         IO 密集型 parquet 加载可用线程加速；1=禁用并行。
        """
        self.base_path = Path(data_folder)
        self.cyq_path = self.base_path / cyq_dir
        self.adj_path = self.base_path / adj_dir
        self.mf_path = self.base_path / mf_dir
        self.db_path = self.base_path / db_dir
        self.max_batch_size = max_batch_size
        # 线程池并行度：默认 min(8, cpu_count)，上限 8 防止 fd 压力
        if max_workers is None:
            self.max_workers = min(8, os.cpu_count() or 1)
        else:
            self.max_workers = max(1, int(max_workers))
        # 缓存 adj_factor (DataFrame 较小，可安全常驻)
        self._adj_cache: Dict[str, Optional[pd.DataFrame]] = {}
        self._adj_lock = __import__('threading').Lock()

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
        """加载复权因子（带缓存 + 线程锁）。"""
        # 快速路径：无锁检查已缓存
        if ts_code in self._adj_cache:
            return self._adj_cache[ts_code]

        adj_file = self.adj_path / f'ts_code={ts_code}' / 'data.parquet'
        if not adj_file.exists():
            self._adj_cache[ts_code] = None
            return None

        try:
            df = pd.read_parquet(adj_file)
            df['trade_date'] = df['trade_date'].astype(str)
            # 线程安全写入缓存
            with self._adj_lock:
                self._adj_cache[ts_code] = df
            return df
        except Exception:
            with self._adj_lock:
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

    # === 增强模式：资金流向 + 估值 ===

    def _load_moneyflow_single(self, ts_code: str, trade_date: str) -> Optional[pd.Series]:
        """
        加载单只股票的资金流向数据（无需复权）。

        Args:
            ts_code: Tushare 股票代码
            trade_date: 交易日期 YYYYMMDD

        Returns:
            pd.Series，关键字段:
              - net_mf_amount: 净流入金额（单位：万元）
              - net_mf_vol: 净流入量（单位：手）
            数据缺失返回 None。

        注意：moneyflow 无需复权校正。
        """
        mf_file = self.mf_path / f'ts_code={ts_code}' / 'data.parquet'
        if not mf_file.exists():
            return None
        try:
            df = pd.read_parquet(mf_file)
            df['trade_date'] = df['trade_date'].astype(str)
            trade_date = str(trade_date)

            # 精确匹配或回退
            mask = df['trade_date'] == trade_date
            if not mask.any():
                recent = df[df['trade_date'] <= trade_date]
                if len(recent) == 0:
                    return None
                latest = recent['trade_date'].max()
                row = df[df['trade_date'] == latest].iloc[0]
            else:
                row = df[mask].iloc[0]

            out = pd.Series(dtype=object)
            # 资金流向字段（无需复权）
            for f in ['net_mf_amount', 'net_mf_vol']:
                if f in row.index:
                    out[f] = float(row[f])
            out['trade_date_mf'] = str(row['trade_date'])
            return out
        except Exception:
            return None

    def _load_daily_basic_single(self, ts_code: str, trade_date: str) -> Optional[pd.Series]:
        """
        加载单只股票的估值指标（无需复权）。

        Args:
            ts_code: Tushare 股票代码
            trade_date: 交易日期 YYYYMMDD

        Returns:
            pd.Series，关键字段:
              - pe_ttm: 滚动市盈率（推荐用 pe_ttm，更稳定）
              - pb: 市净率
              - turnover_rate: 换手率 %
              - total_mv: 总市值（单位：万元）
            数据缺失返回 None。

        注意：daily_basic 无需复权校正。
        """
        db_file = self.db_path / f'ts_code={ts_code}' / 'data.parquet'
        if not db_file.exists():
            return None
        try:
            df = pd.read_parquet(db_file)
            df['trade_date'] = df['trade_date'].astype(str)
            trade_date = str(trade_date)

            mask = df['trade_date'] == trade_date
            if not mask.any():
                recent = df[df['trade_date'] <= trade_date]
                if len(recent) == 0:
                    return None
                latest = recent['trade_date'].max()
                row = df[df['trade_date'] == latest].iloc[0]
            else:
                row = df[mask].iloc[0]

            out = pd.Series(dtype=object)
            for f in ['pe_ttm', 'pb', 'turnover_rate', 'total_mv', 'pe']:
                if f in row.index:
                    out[f] = float(row[f])
            out['trade_date_db'] = str(row['trade_date'])
            return out
        except Exception:
            return None

    def enriched_load_single(self, ts_code: str, trade_date: str) -> Optional[pd.Series]:
        """
        增强模式：加载筹码 + 资金流向 + 估值（三者合一）。

        若 cyq_perf 缺失，返回 None（筹码是核心）。
        若 moneyflow/daily_basic 缺失，仍返回筹码数据（降级）。

        Args:
            ts_code: Tushare 股票代码
            trade_date: 交易日期 YYYYMMDD

        Returns:
            pd.Series，合并字段:
              - 筹码：cost_5pct_adj .. weight_avg_adj, winner_rate
              - 资金流：net_mf_amount (可选)
              - 估值：pe_ttm, pb, turnover_rate, total_mv (可选)
        """
        chip_row = self.load_single(ts_code, trade_date)
        if chip_row is None:
            return None  # 筹码缺失则整体失败

        # 尝试追加资金流向
        mf_row = self._load_moneyflow_single(ts_code, trade_date)
        if mf_row is not None:
            chip_row = pd.concat([chip_row, mf_row])

        # 尝试追加估值
        db_row = self._load_daily_basic_single(ts_code, trade_date)
        if db_row is not None:
            chip_row = pd.concat([chip_row, db_row])

        return chip_row

    def enriched_batch(
        self, ts_codes: list, trade_date: str
    ) -> Generator[Tuple[str, pd.Series], None, None]:
        """
        增强模式流式加载：筹码 + 资金流 + 估值（并行生成器）。

        Args:
            ts_codes: 股票代码列表
            trade_date: 交易日期 YYYYMMDD

        Yields:
            Tuple[ts_code, pd.Series] - 逐只产出

        Note:
            使用 ThreadPoolExecutor 并行加载 parquet（IO 密集型）。
            线程数由 max_workers 控制（默认 min(8, cpu_count)）。
        """
        batch = ts_codes[: self.max_batch_size]

        # 单线程降级（max_workers=1）或小批量直接串行
        if self.max_workers <= 1 or len(batch) <= 5:
            for ts_code in batch:
                try:
                    row = self.enriched_load_single(ts_code, trade_date)
                    if row is not None:
                        yield ts_code, row
                except Exception:
                    continue
            return

        # 并行加载（IO 密集型，线程池加速）
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.enriched_load_single, ts_code, trade_date): ts_code
                for ts_code in batch
            }
            for future in as_completed(futures):
                ts_code = futures[future]
                try:
                    row = future.result()
                    if row is not None:
                        yield ts_code, row
                except Exception:
                    continue
