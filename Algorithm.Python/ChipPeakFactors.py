"""
ChipPeakFactors - 筹码峰因子计算库（cyq_perf 标量版）。

纯函数，无 LEAN 依赖，无副作用。可离线（IC 验证）和 LEAN 模型内复用。

输入约定（chip_row: pd.Series，来自 ChipDataLoader.load_single）:
    - cost_5pct_adj .. cost_95pct_adj: 复权校正后的 5 档成本分位
    - weight_avg_adj: 复权校正后的加权平均成本
    - winner_rate: 获利盘比例 %（0-100，复权不变）

数据基础说明：cyq_chips 实测为单点位，无法做多桶 Shannon 熵；改用 cyq_perf
5 档成本分位的带宽法。详见 docs/chip-peak-cyqperf-refactor.md。
"""

import math
from enum import Enum


class PeakPattern(Enum):
    LOW_SINGLE_PEAK = "low_single_peak"      # 低位单峰密集（建仓区）
    HIGH_SINGLE_PEAK = "high_single_peak"    # 高位单峰密集（派发区）
    DOUBLE_PEAK = "double_peak"              # 双峰（保留枚举，暂不检测）
    DIVERGENT = "divergent"                  # 发散


def _f(row, key: str) -> float:
    """安全取 Series 字段为 float；缺失返回 NaN。"""
    try:
        v = row[key]
    except (KeyError, IndexError):
        return float('nan')
    try:
        return float(v)
    except (TypeError, ValueError):
        return float('nan')


class ChipPeakFactors:
    """筹码峰因子（cyq_perf 标量版）。全部为静态纯函数。"""

    # 默认参数（阈值来自 chouma.md 调研）
    DEFAULT_PARAMS = {
        'single_concentration': 0.6,   # 单峰集中度阈值（exp(-spread)）
        'low_profit_max': 0.2,         # 低位获利盘上限
        'high_profit_min': 0.8,        # 高位（派发区）获利盘下限
    }

    @staticmethod
    def concentration(chip_row) -> float:
        """筹码集中度 = exp(-spread), spread = (cost_95-cost_5)/weight_avg. [0,1]

        90% 成本带宽相对加权均价越窄 → 越集中 → 越接近 1。
        带宽法对 adj_factor 不敏感（分子分母同尺度），稳健。
        数据缺失返回 NaN。
        """
        c95 = _f(chip_row, 'cost_95pct_adj')
        c5 = _f(chip_row, 'cost_5pct_adj')
        wavg = _f(chip_row, 'weight_avg_adj')
        if math.isnan(c95) or math.isnan(c5) or wavg <= 0:
            return float('nan')
        spread = (c95 - c5) / wavg
        if spread < 0:
            spread = 0.0
        return float(math.exp(-spread))

    @staticmethod
    def profit_ratio(chip_row) -> float:
        """获利盘比例 = winner_rate / 100. [0,1]

        直接取 cyq_perf.winner_rate（复权不变），无需累加多桶。
        """
        wr = _f(chip_row, 'winner_rate')
        if math.isnan(wr):
            return float('nan')
        # winner_rate 可能为 0-100；夹到 [0,1]
        pr = wr / 100.0
        return float(max(0.0, min(1.0, pr)))

    @staticmethod
    def average_cost(chip_row) -> float:
        """平均成本 = weight_avg_adj（复权校正后的加权均价）。"""
        return _f(chip_row, 'weight_avg_adj')

    @staticmethod
    def cost_deviation(chip_row, current_price: float) -> float:
        """平均成本偏离度 = (现价 - 均成本)/均成本.

        current_price 与 weight_avg_adj 须在同一复权口径（后复权）。
        """
        avg = ChipPeakFactors.average_cost(chip_row)
        if avg is None or avg == 0 or math.isnan(avg):
            return 0.0
        return float((current_price - avg) / avg)

    @staticmethod
    def classify_peak(chip_row, current_price: float, params: dict) -> PeakPattern:
        """筹码峰形态分类（阈值法）。

        - 集中度 < single_concentration → DIVERGENT
        - 单峰密集 & 获利盘 < low_profit_max → LOW_SINGLE_PEAK（建仓区）
        - 单峰密集 & 获利盘 > high_profit_min → HIGH_SINGLE_PEAK（派发区）
        - 其余 → DIVERGENT
        双峰检测暂不实现（YAGNI），统一归入 DIVERGENT。
        数据缺失（NaN 集中度）→ DIVERGENT（保守不发信号）。
        """
        conc = ChipPeakFactors.concentration(chip_row)
        profit = ChipPeakFactors.profit_ratio(chip_row)
        if math.isnan(conc) or math.isnan(profit):
            return PeakPattern.DIVERGENT

        if conc < params['single_concentration']:
            return PeakPattern.DIVERGENT

        if profit < params['low_profit_max']:
            return PeakPattern.LOW_SINGLE_PEAK   # 低位集中：建仓区
        if profit > params['high_profit_min']:
            return PeakPattern.HIGH_SINGLE_PEAK  # 高位集中：派发区
        return PeakPattern.DIVERGENT

    @staticmethod
    def composite_score(chip_row, current_price: float, params: dict) -> float:
        """综合得分：低位单峰密集得正分，高位/发散得0。

        - LOW_SINGLE_PEAK:  conc × (1-profit) × (1 + max(0, dev))，正分
        - 其它:             0.0
        数据缺失 → 0.0。
        """
        pattern = ChipPeakFactors.classify_peak(chip_row, current_price, params)
        if pattern != PeakPattern.LOW_SINGLE_PEAK:
            return 0.0
        conc = ChipPeakFactors.concentration(chip_row)
        profit = ChipPeakFactors.profit_ratio(chip_row)
        dev = ChipPeakFactors.cost_deviation(chip_row, current_price)
        if math.isnan(conc) or math.isnan(profit):
            return 0.0
        return float(conc * (1 - profit) * (1 + max(0.0, dev)))
