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

import pandas as pd


def test_concentration_single_peak():
    """Test: 完全集中的筹码（单价位）集中度=1.0"""
    df = pd.DataFrame({
        'corrected_price': [100.0],
        'percent': [100.0]
    })
    assert ChipPeakFactors.concentration(df) == 1.0


def test_concentration_divergent():
    """Test: 完全分散的筹码集中度接近0"""
    df = pd.DataFrame({
        'corrected_price': range(100, 110),
        'percent': [10.0] * 10
    })
    conc = ChipPeakFactors.concentration(df)
    assert abs(conc) < 0.01  # 归一化熵=ln(10), 1-1=0


def test_profit_ratio_basic():
    """Test: 当前价110, 筹码在100/105/110, 获利盘=前两个=60%"""
    df = pd.DataFrame({
        'corrected_price': [100.0, 105.0, 110.0],
        'percent': [30.0, 30.0, 40.0]
    })
    profit = ChipPeakFactors.profit_ratio(df, 110.0)
    assert abs(profit - 0.6) < 0.01  # 100+105=60% (< 110, 不含110本身)


def test_average_cost_and_deviation():
    """Test: 平均成本 = 加权均值；偏离度 = (现价-均)/均"""
    df = pd.DataFrame({
        'corrected_price': [100.0, 110.0],
        'percent': [50.0, 50.0]
    })
    avg = ChipPeakFactors.average_cost(df)
    assert abs(avg - 105.0) < 0.01
    dev = ChipPeakFactors.cost_deviation(df, 110.0)
    assert abs(dev - (110.0 - 105.0) / 105.0) < 0.001


def test_classify_peak_high_single():
    """Test: 单价位集中(集中度1.0), 获利盘1.0 → HIGH_SINGLE_PEAK"""
    df = pd.DataFrame({
        'corrected_price': [100.0],
        'percent': [100.0]
    })
    params = ChipPeakFactors.DEFAULT_PARAMS
    pattern = ChipPeakFactors.classify_peak(df, 105.0, params)
    assert pattern == PeakPattern.HIGH_SINGLE_PEAK  # 集中度1.0>0.6, 获利盘1.0>0.8


def test_classify_peak_low_single():
    """Test: 单价位集中(集中度1.0), 现价低于筹码(套牢), 获利盘=0 → LOW_SINGLE_PEAK。

    数据设计：筹码全部集中在 100.0 价位，现价 99.0（低于所有筹码成本），
    故获利盘=0（无人获利），属于低位建仓区。
    """
    df = pd.DataFrame({
        'corrected_price': [100.0],
        'percent': [100.0]
    })
    params = ChipPeakFactors.DEFAULT_PARAMS
    pattern = ChipPeakFactors.classify_peak(df, 99.0, params)
    assert pattern == PeakPattern.LOW_SINGLE_PEAK  # 集中度1.0>0.6, 获利盘0.0<0.2


def test_classify_peak_divergent_uniform():
    """Test: 完全均匀分散(集中度~0) → DIVERGENT"""
    df = pd.DataFrame({
        'corrected_price': range(100, 110),
        'percent': [10.0] * 10
    })
    params = ChipPeakFactors.DEFAULT_PARAMS
    pattern = ChipPeakFactors.classify_peak(df, 105.0, params)
    assert pattern == PeakPattern.DIVERGENT  # 集中度过低


def test_composite_score_low_peak_positive():
    """Test: 低位单峰密集（建仓区）应得正分。

    数据设计：筹码全部集中在 100.0 价位，现价 99.0（套牢区，无人获利）。
    - concentration = 1.0（单价位完全集中）
    - profit_ratio = 0.0（无筹码在 99.0 之下）
    - cost_deviation = (99-100)/100 = -0.01（max(0,-0.01)=0）
    - classify → LOW_SINGLE_PEAK
    - score = 1.0 × (1-0) × (1+0) = 1.0 > 0
    """
    df = pd.DataFrame({
        'corrected_price': [100.0],
        'percent': [100.0]
    })
    score = ChipPeakFactors.composite_score(df, 99.0, ChipPeakFactors.DEFAULT_PARAMS)
    assert score > 0


def test_composite_score_high_peak_zero():
    """Test: 高位单峰密集（派发区）应得 0 分。

    数据设计：筹码全部集中在 100.0 价位，现价 105.0（全部获利）。
    - concentration = 1.0
    - profit_ratio = 1.0（全部筹码 < 105）
    - classify → HIGH_SINGLE_PEAK
    - score = 0.0
    """
    df = pd.DataFrame({
        'corrected_price': [100.0],
        'percent': [100.0]
    })
    score = ChipPeakFactors.composite_score(df, 105.0, ChipPeakFactors.DEFAULT_PARAMS)
    assert score == 0.0


def test_composite_score_divergent_zero():
    """Test: 发散分布应得 0 分"""
    df = pd.DataFrame({
        'corrected_price': range(100, 110),
        'percent': [10.0] * 10
    })
    score = ChipPeakFactors.composite_score(df, 105.0, ChipPeakFactors.DEFAULT_PARAMS)
    assert score == 0.0
