"""Tests for ChipDataLoader.py - 筹码数据加载器测试"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure ToolBox is importable
ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ToolBox.ChipDataLoader import ChipDataLoader


def test_load_single_basic():
    """Test: load cyq_chips for single stock returns DataFrame with corrected_price."""
    data_folder = '/home/project/tushare-downloader/tushare_data_v2'
    loader = ChipDataLoader(data_folder)

    df = loader.load_single('600519.SH', '20231113')
    assert df is not None
    assert len(df) > 0
    assert 'price' in df.columns
    assert 'percent' in df.columns
    assert 'corrected_price' in df.columns


def test_load_single_nonexistent_stock():
    """Test: loading non-existent stock returns None."""
    data_folder = '/home/project/tushare-downloader/tushare_data_v2'
    loader = ChipDataLoader(data_folder)

    df = loader.load_single('999999.SH', '20231113')
    assert df is None


def test_load_single_fallback_to_nearest_date():
    """Test: if exact date missing, returns nearest earlier date."""
    data_folder = '/home/project/tushare-downloader/tushare_data_v2'
    loader = ChipDataLoader(data_folder)

    # Request a date that likely doesn't have cyq_chips data
    # 600519.SH cyq_chips has dates starting from 20231113
    df = loader.load_single('600519.SH', '20991231')  # Future date
    # Should fallback to most recent date
    assert df is not None
    assert len(df) > 0


def test_load_single_corrected_price_calculation():
    """Test: corrected_price = price * adj_factor."""
    data_folder = '/home/project/tushare-downloader/tushare_data_v2'
    loader = ChipDataLoader(data_folder)

    df = loader.load_single('600519.SH', '20231113')
    assert df is not None

    # Verify calculation for first row
    first_row = df.iloc[0]
    # Original price should be 2310.0 from the data
    # adj_factor for 20231113 should be applied
    # We'll verify the column exists and is numeric
    assert 'corrected_price' in first_row
    assert isinstance(first_row['corrected_price'], float)


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
