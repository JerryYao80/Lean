import numpy as np
import pandas as pd
from enum import Enum


class PeakPattern(Enum):
    LOW_SINGLE_PEAK = "low_single_peak"      # 低位单峰密集（建仓区）
    HIGH_SINGLE_PEAK = "high_single_peak"    # 高位单峰密集（派发区）
    DOUBLE_PEAK = "double_peak"              # 双峰
    DIVERGENT = "divergent"                  # 发散


class ChipPeakFactors:
    """筹码峰因子计算库。纯函数，无 LEAN 依赖，无副作用。

    输入 DataFrame 约定:
        - corrected_price: float, 复权校正后的价格
        - percent: float, 价格占比%（合计约 100）
    所有方法为静态纯函数，可离线（IC 验证）和 LEAN 模型内复用。
    """

    # 默认参数（来自 chouma.md）
    DEFAULT_PARAMS = {
        'single_concentration': 0.6,   # 单峰集中度阈值
        'low_profit_max': 0.2,         # 低位获利盘上限
        'high_profit_min': 0.8,        # 高位（派发区）获利盘下限
        'double_peak_ratio': 0.3,
        'double_peak_gap': 0.15,
    }

    @staticmethod
    def concentration(chip_df: pd.DataFrame) -> float:
        """筹码集中度 = 1 - 归一化熵 (Shannon entropy). [0,1], 大=集中。

        完全集中在单价位 → 1.0；完全均匀分散 → 0.0。
        """
        w = chip_df['percent'].values.astype(float)
        total = w.sum()
        if total <= 0:
            return 0.0
        p = w / total
        p_safe = p[p > 0]
        H = -np.sum(p_safe * np.log(p_safe))
        N = len(p)
        if N <= 1:
            # 单价位：天然完全集中
            return 1.0
        return float(1.0 - H / np.log(N))

    @staticmethod
    def profit_ratio(chip_df: pd.DataFrame, current_price: float) -> float:
        """获利盘比例 = 当前价之下筹码占比. [0,1]

        严格小于 current_price 的 corrected_price 视为获利（成本低于现价）。
        """
        mask = chip_df['corrected_price'] < current_price
        return float(chip_df.loc[mask, 'percent'].sum() / 100.0)

    @staticmethod
    def average_cost(chip_df: pd.DataFrame) -> float:
        """平均成本 = Σ(price×percent)/Σ(percent)"""
        p = chip_df['corrected_price'].values
        w = chip_df['percent'].values
        return float(np.sum(p * w) / np.sum(w))

    @staticmethod
    def cost_deviation(chip_df: pd.DataFrame, current_price: float) -> float:
        """平均成本偏离度 = (现价 - 均成本)/均成本"""
        avg = ChipPeakFactors.average_cost(chip_df)
        if avg == 0:
            return 0.0
        return float((current_price - avg) / avg)

    @staticmethod
    def classify_peak(chip_df: pd.DataFrame, current_price: float, params: dict) -> 'PeakPattern':
        """筹码峰形态分类（阈值法）。

        - 集中度 < single_concentration → DIVERGENT
        - 单峰密集 & 获利盘 < low_profit_max → LOW_SINGLE_PEAK（建仓区）
        - 单峰密集 & 获利盘 > high_profit_min → HIGH_SINGLE_PEAK（派发区）
        - 其余 → DIVERGENT
        双峰检测暂不实现（YAGNI），统一归入 DIVERGENT。
        """
        conc = ChipPeakFactors.concentration(chip_df)
        profit = ChipPeakFactors.profit_ratio(chip_df, current_price)

        if conc < params['single_concentration']:
            # 集中度不足 → 发散
            return PeakPattern.DIVERGENT

        # 单峰密集，判断高低位
        if profit < params['low_profit_max']:
            return PeakPattern.LOW_SINGLE_PEAK   # 低位集中：建仓区
        if profit > params['high_profit_min']:
            return PeakPattern.HIGH_SINGLE_PEAK  # 高位集中：派发区
        return PeakPattern.DIVERGENT

    @staticmethod
    def composite_score(chip_df: pd.DataFrame, current_price: float, params: dict) -> float:
        """综合得分：低位单峰密集得正分，高位/发散得0。

        - LOW_SINGLE_PEAK:  conc × (1-profit) × (1 + max(0, dev))，正分
        - DOUBLE_PEAK:      0.1 × conc（双峰检测暂不触发，保留接口）
        - 其它:             0.0
        """
        pattern = ChipPeakFactors.classify_peak(chip_df, current_price, params)
        conc = ChipPeakFactors.concentration(chip_df)
        profit = ChipPeakFactors.profit_ratio(chip_df, current_price)
        dev = ChipPeakFactors.cost_deviation(chip_df, current_price)

        if pattern == PeakPattern.LOW_SINGLE_PEAK:
            return conc * (1 - profit) * (1 + max(0, dev))  # 正分
        elif pattern == PeakPattern.DOUBLE_PEAK:
            return 0.1 * conc  # 微弱正分（双峰检测暂不触发，保留接口）
        return 0.0
