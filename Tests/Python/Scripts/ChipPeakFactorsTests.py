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


def _enriched_row(cost_5, cost_95, weight_avg, winner_rate,
                  net_mf_amount=None, pe_ttm=None, pb=None, turnover_rate=None):
    """构造一行增强 Series（筹码 + 资金流 + 估值）。"""
    row = _row(cost_5, cost_95, weight_avg, winner_rate)
    if net_mf_amount is not None:
        row['net_mf_amount'] = net_mf_amount
    if pe_ttm is not None:
        row['pe_ttm'] = pe_ttm
    if pb is not None:
        row['pb'] = pb
    if turnover_rate is not None:
        row['turnover_rate'] = turnover_rate
    return row


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


# === 增强因子测试 ===

def test_net_moneyflow_signal_positive():
    """Test: 净流入达到阈值 → signal=1.0"""
    row = _enriched_row(100.0, 100.0, 100.0, 5.0, net_mf_amount=5000.0)
    params = ChipPeakFactors.DEFAULT_PARAMS
    signal = ChipPeakFactors.net_moneyflow_signal(row, params)
    assert abs(signal - 1.0) < 0.001


def test_net_moneyflow_signal_half():
    """Test: 净流入一半 → signal=0.5"""
    row = _enriched_row(100.0, 100.0, 100.0, 5.0, net_mf_amount=2500.0)
    params = ChipPeakFactors.DEFAULT_PARAMS
    signal = ChipPeakFactors.net_moneyflow_signal(row, params)
    assert abs(signal - 0.5) < 0.001


def test_net_moneyflow_signal_negative():
    """Test: 净流出 → signal=0.0"""
    row = _enriched_row(100.0, 100.0, 100.0, 5.0, net_mf_amount=-1000.0)
    params = ChipPeakFactors.DEFAULT_PARAMS
    signal = ChipPeakFactors.net_moneyflow_signal(row, params)
    assert signal == 0.0


def test_net_moneyflow_signal_missing():
    """Test: 缺失资金流 → signal=0.0"""
    row = _row(100.0, 100.0, 100.0, 5.0)
    params = ChipPeakFactors.DEFAULT_PARAMS
    signal = ChipPeakFactors.net_moneyflow_signal(row, params)
    assert signal == 0.0


def test_value_quality_score_low_pe():
    """Test: PE_TTM=15（低估值）→ score=1.0"""
    row = _enriched_row(100.0, 100.0, 100.0, 5.0, pe_ttm=15.0, pb=3.0)
    params = ChipPeakFactors.DEFAULT_PARAMS
    score = ChipPeakFactors.value_quality_score(row, params)
    assert abs(score - 1.0) < 0.001


def test_value_quality_score_medium_pe():
    """Test: PE_TTM=30 → score=0.8"""
    row = _enriched_row(100.0, 100.0, 100.0, 5.0, pe_ttm=30.0, pb=3.0)
    params = ChipPeakFactors.DEFAULT_PARAMS
    score = ChipPeakFactors.value_quality_score(row, params)
    assert abs(score - 0.8) < 0.001


def test_value_quality_score_high_pb():
    """Test: PB>6 → 打折20%"""
    row = _enriched_row(100.0, 100.0, 100.0, 5.0, pe_ttm=30.0, pb=7.0)
    params = ChipPeakFactors.DEFAULT_PARAMS
    score = ChipPeakFactors.value_quality_score(row, params)
    assert abs(score - 0.8 * 0.8) < 0.001


def test_value_quality_score_missing():
    """Test: 缺失估值 → score=0.5（中性）"""
    row = _row(100.0, 100.0, 100.0, 5.0)
    params = ChipPeakFactors.DEFAULT_PARAMS
    score = ChipPeakFactors.value_quality_score(row, params)
    assert abs(score - 0.5) < 0.001


def test_turnover_risk_low():
    """Test: turnover<3% → risk=0"""
    row = _enriched_row(100.0, 100.0, 100.0, 5.0, turnover_rate=2.0)
    risk = ChipPeakFactors.turnover_risk(row)
    assert risk == 0.0


def test_turnover_risk_medium():
    """Test: 3%≤turnover<10% → risk=0.5"""
    row = _enriched_row(100.0, 100.0, 100.0, 5.0, turnover_rate=5.0)
    risk = ChipPeakFactors.turnover_risk(row)
    assert abs(risk - 0.5) < 0.001


def test_turnover_risk_high():
    """Test: turnover≥10% → risk=1.0"""
    row = _enriched_row(100.0, 100.0, 100.0, 5.0, turnover_rate=12.0)
    risk = ChipPeakFactors.turnover_risk(row)
    assert risk == 1.0


def test_composite_score_enriched_high():
    """Test: 低位单峰 + 大流入 + 低估值 → 高分"""
    # base=0.95, mf_signal=1.0, value=1.0, turnover_risk=0
    # final = 0.95 × (1+0.3×1) × (1+0.2×1) × (1-0) = 0.95 × 1.3 × 1.2 = 1.482
    row = _enriched_row(100.0, 100.0, 100.0, 5.0,
                         net_mf_amount=5000.0, pe_ttm=15.0, pb=3.0, turnover_rate=2.0)
    score = ChipPeakFactors.composite_score(row, 99.0, ChipPeakFactors.DEFAULT_PARAMS)
    assert score > 1.0  # 融合后应高于纯筹码得分 0.95


def test_composite_score_enriched_low_turnover():
    """Test: 高换手率 → 打折"""
    # base=0.95, mf_signal=1.0, value=1.0, turnover_risk=1.0
    # final = 0.95 × 1.3 × 1.2 × (1-1×0.3) = 0.95 × 1.3 × 1.2 × 0.7 = 1.0374
    row = _enriched_row(100.0, 100.0, 100.0, 5.0,
                         net_mf_amount=5000.0, pe_ttm=15.0, pb=3.0, turnover_rate=12.0)
    score = ChipPeakFactors.composite_score(row, 99.0, ChipPeakFactors.DEFAULT_PARAMS)
    assert score < 1.482  # 高换手率应打折
