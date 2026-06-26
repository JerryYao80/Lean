"""Tests for ChipDataLoader.py - 筹码数据加载器测试（cyq_perf）"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure ToolBox is importable
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ToolBox.ChipDataLoader import ChipDataLoader


def test_load_single_basic():
    """Test: load cyq_perf for single stock returns Series with adj-corrected fields."""
    data_folder = '/home/project/tushare-downloader/tushare_data_v2'
    loader = ChipDataLoader(data_folder)

    row = loader.load_single('600519.SH', '20231113')
    assert row is not None
    # 关键字段存在
    assert 'weight_avg_adj' in row.index
    assert 'cost_5pct_adj' in row.index
    assert 'cost_95pct_adj' in row.index
    assert 'winner_rate' in row.index
    # 数值有效
    assert float(row['weight_avg_adj']) > 0
    assert 0.0 <= float(row['winner_rate']) <= 100.0


def test_load_single_nonexistent_stock():
    """Test: loading non-existent stock returns None."""
    data_folder = '/home/project/tushare-downloader/tushare_data_v2'
    loader = ChipDataLoader(data_folder)

    row = loader.load_single('999999.SH', '20231113')
    assert row is None


def test_load_single_fallback_to_nearest_date():
    """Test: if exact date missing, returns nearest earlier date."""
    data_folder = '/home/project/tushare-downloader/tushare_data_v2'
    loader = ChipDataLoader(data_folder)

    # 未来日期，应回退到最近一个有数据的交易日
    row = loader.load_single('600519.SH', '20991231')
    assert row is not None
    assert 'weight_avg_adj' in row.index


def test_load_batch_generator():
    """Test: load_batch yields (ts_code, Series), respects max_batch_size."""
    data_folder = '/home/project/tushare-downloader/tushare_data_v2'
    loader = ChipDataLoader(data_folder, max_batch_size=2)

    ts_codes = ['600519.SH', '000001.SZ', '000002.SZ']  # 3只, batch_size=2
    results = list(loader.load_batch(ts_codes, '20231113'))

    # 应该 yield <= 2 个（batch_size 限制）
    assert len(results) <= 2
    for ts_code, row in results:
        assert row is not None
        assert 'weight_avg_adj' in row.index
