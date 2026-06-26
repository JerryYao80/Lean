import sys
import importlib.util
from pathlib import Path

# Algorithm.Python 目录名含点，无法作为 Python 包导入；
# 通过文件路径加载模块，避免对 Algorithm.Python/__init__.py 的依赖。
_ROOT = Path(__file__).resolve().parents[3]  # /home/project/hope/Lean
_MODULE_PATH = _ROOT / 'Algorithm.Python' / 'ChipPeakFactors.py'

_spec = importlib.util.spec_from_file_location('ChipPeakFactors', _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

ChipPeakFactors = _mod.ChipPeakFactors
PeakPattern = _mod.PeakPattern

import math
import pandas as pd


def _row(cost_5, cost_95, weight_avg, winner_rate):
    """构造一行 cyq_perf 风格的 Series（已复权校正字段）。"""
    return pd.Series({
        'cost_5pct_adj': cost_5,
        'cost_15pct_adj': (cost_5 + weight_avg) / 2,
        'cost_50pct_adj': weight_avg,
        'cost_85pct_adj': (cost_95 + weight_avg) / 2,
        'cost_95pct_adj': cost_95,
        'weight_avg_adj': weight_avg,
        'winner_rate': winner_rate,
    })


def test_concentration_single_peak():
    """Test: 完全集中（成本带宽=0）集中度=1.0"""
    row = _row(cost_5=100.0, cost_95=100.0, weight_avg=100.0, winner_rate=0.0)
    assert ChipPeakFactors.concentration(row) == 1.0


def test_concentration_divergent():
    """Test: 宽分布集中度应低于单峰阈值。"""
    # spread = (250-50)/150 = 1.333 → conc = exp(-1.333) ≈ 0.264 < 0.6
    row = _row(cost_5=50.0, cost_95=250.0, weight_avg=150.0, winner_rate=50.0)
    conc = ChipPeakFactors.concentration(row)
    assert conc < 0.6


def test_concentration_missing_data():
    """Test: 数据缺失返回 NaN"""
    row = pd.Series({'cost_95pct_adj': float('nan'), 'cost_5pct_adj': 100.0,
                     'weight_avg_adj': 100.0, 'winner_rate': 0.0})
    assert math.isnan(ChipPeakFactors.concentration(row))


def test_profit_ratio_basic():
    """Test: winner_rate=60 → 获利盘=0.6"""
    row = _row(cost_5=100.0, cost_95=100.0, weight_avg=100.0, winner_rate=60.0)
    profit = ChipPeakFactors.profit_ratio(row)
    assert abs(profit - 0.6) < 0.001


def test_average_cost_and_deviation():
    """Test: 平均成本=weight_avg_adj；偏离度=(现价-均)/均"""
    row = _row(cost_5=100.0, cost_95=110.0, weight_avg=105.0, winner_rate=50.0)
    avg = ChipPeakFactors.average_cost(row)
    assert abs(avg - 105.0) < 0.01
    dev = ChipPeakFactors.cost_deviation(row, 110.0)
    assert abs(dev - (110.0 - 105.0) / 105.0) < 0.001


def test_classify_peak_high_single():
    """Test: 集中(带宽0) + 获利盘1.0 → HIGH_SINGLE_PEAK（派发区）"""
    row = _row(cost_5=100.0, cost_95=100.0, weight_avg=100.0, winner_rate=95.0)
    params = ChipPeakFactors.DEFAULT_PARAMS
    pattern = ChipPeakFactors.classify_peak(row, 105.0, params)
    assert pattern == PeakPattern.HIGH_SINGLE_PEAK  # conc=1.0>0.6, profit=0.95>0.8


def test_classify_peak_low_single():
    """Test: 集中(带宽0) + 获利盘=0 → LOW_SINGLE_PEAK（建仓区）"""
    row = _row(cost_5=100.0, cost_95=100.0, weight_avg=100.0, winner_rate=5.0)
    params = ChipPeakFactors.DEFAULT_PARAMS
    pattern = ChipPeakFactors.classify_peak(row, 99.0, params)
    assert pattern == PeakPattern.LOW_SINGLE_PEAK  # conc=1.0>0.6, profit=0.05<0.2


def test_classify_peak_divergent_wide():
    """Test: 宽分布(集中度低) → DIVERGENT"""
    row = _row(cost_5=50.0, cost_95=250.0, weight_avg=150.0, winner_rate=50.0)
    params = ChipPeakFactors.DEFAULT_PARAMS
    pattern = ChipPeakFactors.classify_peak(row, 150.0, params)
    assert pattern == PeakPattern.DIVERGENT  # conc≈0.264 < 0.6


def test_composite_score_low_peak_positive():
    """Test: 低位单峰密集（建仓区）应得正分"""
    # conc=1.0, profit=0.05, dev=(99-100)/100=-0.01 → max(0,-0.01)=0
    # score = 1.0 × (1-0.05) × (1+0) = 0.95 > 0
    row = _row(cost_5=100.0, cost_95=100.0, weight_avg=100.0, winner_rate=5.0)
    score = ChipPeakFactors.composite_score(row, 99.0, ChipPeakFactors.DEFAULT_PARAMS)
    assert score > 0


def test_composite_score_high_peak_zero():
    """Test: 高位单峰密集（派发区）应得 0 分"""
    row = _row(cost_5=100.0, cost_95=100.0, weight_avg=100.0, winner_rate=95.0)
    score = ChipPeakFactors.composite_score(row, 105.0, ChipPeakFactors.DEFAULT_PARAMS)
    assert score == 0.0


def test_composite_score_divergent_zero():
    """Test: 发散分布应得 0 分"""
    row = _row(cost_5=50.0, cost_95=250.0, weight_avg=150.0, winner_rate=50.0)
    score = ChipPeakFactors.composite_score(row, 150.0, ChipPeakFactors.DEFAULT_PARAMS)
    assert score == 0.0
