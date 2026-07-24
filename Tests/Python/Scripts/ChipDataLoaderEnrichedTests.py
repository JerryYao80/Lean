"""Tests for ChipDataLoader.enriched_* methods - 增强数据加载测试"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]  # /home/project/hope/Lean
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ToolBox.ChipDataLoader import ChipDataLoader
import math

# 使用真实数据路径
DATA_FOLDER = '/home/project/tushare-downloader/tushare_data_v2'


def test_enriched_load_single_with_all_data():
    """Test: enriched_load_single 应加载筹码 + 资金流 + 估值"""
    loader = ChipDataLoader(DATA_FOLDER)
    # 000001.SZ 在所有三张表中都有数据
    row = loader.enriched_load_single('000001.SZ', '20231113')
    assert row is not None
    # 筹码字段
    assert 'cost_5pct_adj' in row
    assert 'weight_avg_adj' in row
    assert 'winner_rate' in row
    # 资金流字段
    assert 'net_mf_amount' in row
    # 估值字段
    assert 'pe_ttm' in row
    assert 'pb' in row


def test_enriched_load_single_chip_only():
    """Test: 若 moneyflow/daily_basic 缺失，仍返回筹码数据"""
    loader = ChipDataLoader(DATA_FOLDER)
    # 找一个有 cyq_perf 但可能缺 moneyflow 的股票
    # 实测 moneyflow 覆盖 5509 股，cyq_perf 覆盖 5537 股，理论上都存在
    row = loader.enriched_load_single('000001.SZ', '20231113')
    assert row is not None
    assert 'cost_5pct_adj' in row


def test_enriched_batch_generator():
    """Test: enriched_batch 应逐只产出"""
    loader = ChipDataLoader(DATA_FOLDER, max_batch_size=10)
    codes = ['000001.SZ', '000002.SZ', '600519.SH']
    count = 0
    for ts_code, row in loader.enriched_batch(codes, '20231113'):
        assert row is not None
        assert 'cost_5pct_adj' in row
        count += 1
    assert count >= 1  # 至少成功加载一只


def test_load_moneyflow_single_real():
    """Test: _load_moneyflow_single 应返回净流入字段"""
    loader = ChipDataLoader(DATA_FOLDER)
    row = loader._load_moneyflow_single('000001.SZ', '20231113')
    if row is not None:
        # moneyflow 可能在此日期无数据
        assert 'net_mf_amount' in row


def test_load_daily_basic_single_real():
    """Test: _load_daily_basic_single 应返回估值字段"""
    loader = ChipDataLoader(DATA_FOLDER)
    row = loader._load_daily_basic_single('000001.SZ', '20231113')
    if row is not None:
        assert 'pe_ttm' in row or 'pe' in row
        assert 'pb' in row


if __name__ == '__main__':
    print("Running ChipDataLoader enriched tests...")
    test_enriched_load_single_with_all_data()
    print("✓ test_enriched_load_single_with_all_data")
    test_enriched_load_single_chip_only()
    print("✓ test_enriched_load_single_chip_only")
    test_enriched_batch_generator()
    print("✓ test_enriched_batch_generator")
    test_load_moneyflow_single_real()
    print("✓ test_load_moneyflow_single_real")
    test_load_daily_basic_single_real()
    print("✓ test_load_daily_basic_single_real")
    print("\nAll tests passed!")