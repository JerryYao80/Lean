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
        # 横截面相对阈值（可选，cmf-audit2.md 第4点）：调用方传入 conc_mean/conc_std 后启用
        'conc_zscore_threshold': None,  # None = 用绝对阈值 single_concentration
        'conc_mean': None,
        'conc_std': None,
        # 偏度调整权重（cmf-audit2.md 第5点）
        'skew_weight': 0.2,
        # 多因子融合权重（增强模式）
        'mf_weight': 0.3,              # 资金流信号权重
        'value_weight': 0.2,           # 估值因子权重
        # 资金流信号标准化阈值（万元）：达到此净流入记为满分 1.0
        'mf_full_amount': 5000.0,
        # 估值阈值：pe_ttm 低于此值记为满分
        'value_pe_low': 20.0,
        'value_pe_high': 60.0,
        'value_pb_high': 6.0,
    }

    @staticmethod
    def cost_skewness(chip_row) -> float:
        """筹码成本偏度 = (cost_85-cost_50) - (cost_50-cost_15)，归一化到 weight_avg。

        利用 cyq_perf 中原本闲置的 cost_15pct / cost_85pct（cmf-audit2.md 第5点）。
        - skew > 0：筹码往上方偏（85-50 段宽于 50-15 段）→ 高位筹码堆积，派发压力更早显现。
        - skew < 0：筹码往下方偏 → 低位支撑更强。
        - skew ≈ 0：成本分布关于中位成本对称。

        归一化：除以 weight_avg_adj，使偏度无量纲且对 adj_factor 不敏感（分子分母同尺度）。
        数据缺失（任一分位缺失或 weight_avg<=0）→ NaN。

        注意：这是 5 档分位下的离散偏度代理，非完整分布的真实三阶矩；
        双峰分布下可能失真（见 docs/cmf-audit2.md 本质局限）。
        """
        c85 = _f(chip_row, 'cost_85pct_adj')
        c50 = _f(chip_row, 'cost_50pct_adj')
        c15 = _f(chip_row, 'cost_15pct_adj')
        wavg = _f(chip_row, 'weight_avg_adj')
        if math.isnan(c85) or math.isnan(c50) or math.isnan(c15) or wavg <= 0:
            return float('nan')
        skew = (c85 - c50) - (c50 - c15)
        return float(skew / wavg)

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

        复权口径自动校验（cmf-audit2.md 第6点）：
        若 current_price 与 weight_avg_adj 量级差异超 5x，判定为复权口径不一致
        （如 current_price 是前复权、weight_avg_adj 是后复权），返回 0.0 而非给出
        误导性偏离值。阈值 5x 来自 A 股个股后复权/前复权长期累积的典型比例上限。
        """
        avg = ChipPeakFactors.average_cost(chip_row)
        if avg is None or avg == 0 or math.isnan(avg):
            return 0.0
        # 复权口径一致性校验：价格与成本应同量级（A 股后复权累积比例通常 < 5x）
        ratio = current_price / avg if avg > 0 else 0.0
        if ratio > 5.0 or ratio < 0.2:
            # 量级严重不匹配 → 疑似复权口径不一致，返回 0（中性，不误导）
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

        阈值校准（cmf-audit2.md 第4点）：
        single_concentration / low_profit_max / high_profit_min 是绝对阈值，未做样本外
        walk-forward 校准。conc 是绝对值判断（不像路径B/C做了横截面 z-score），不同波动率
        regime / 不同板块下同一阈值可能对应不同误判率。
        - 默认仍是绝对阈值（向后兼容）。
        - 若 params 含 'conc_zscore_threshold'（横截面 z-score 阈值），则改用
          concentration 相对全市场分位的位置判定单峰——调用方需自行算横截面均值/标准差
          并传入 'conc_mean'/'conc_std'。本函数不自行做横截面统计（无全市场上下文）。
        """
        conc = ChipPeakFactors.concentration(chip_row)
        profit = ChipPeakFactors.profit_ratio(chip_row)
        if math.isnan(conc) or math.isnan(profit):
            return PeakPattern.DIVERGENT

        # 横截面相对阈值（可选）：若调用方传入 conc_mean/conc_std，则用 z-score 判定单峰。
        # 否则回退到绝对阈值 single_concentration（默认 0.6）。
        conc_mean = params.get('conc_mean')
        conc_std = params.get('conc_std')
        conc_z_threshold = params.get('conc_zscore_threshold')
        if (conc_mean is not None and conc_std is not None
                and conc_z_threshold is not None and conc_std > 0):
            conc_z = (conc - conc_mean) / conc_std
            is_single_peak = conc_z >= conc_z_threshold
        else:
            is_single_peak = conc >= params['single_concentration']

        if not is_single_peak:
            return PeakPattern.DIVERGENT

        if profit < params['low_profit_max']:
            return PeakPattern.LOW_SINGLE_PEAK   # 低位集中：建仓区
        if profit > params['high_profit_min']:
            return PeakPattern.HIGH_SINGLE_PEAK  # 高位集中：派发区
        return PeakPattern.DIVERGENT

    # === 增强因子：资金流向 + 估值 ===

    @staticmethod
    def net_moneyflow_signal(chip_row, params: dict) -> float:
        """资金流信号：净流入金额标准化到 [0,1]。

        Args:
            chip_row: enriched Series（需含 net_mf_amount）
            params: {'mf_full_amount': 5000.0} 万元阈值

        Returns:
            信号 ∈ [0,1]。净流入达到阈值记为 1.0。
            数据缺失返回 0.0（中性）。

        注意：net_mf_amount 单位为万元（Tushare moneyflow 原值）。
        """
        mf_amt = _f(chip_row, 'net_mf_amount')
        if math.isnan(mf_amt):
            return 0.0
        threshold = params.get('mf_full_amount', 5000.0)
        if threshold <= 0:
            return 0.0
        # 标准化：净流入 / 阈值，夹到 [0,1]
        # 注意：流出（负值）记为 0，流入（正值）记为 min(流入/阈值, 1)
        signal = max(0.0, min(1.0, mf_amt / threshold))
        return signal

    @staticmethod
    def value_quality_score(chip_row, params: dict) -> float:
        """估值质量因子：低 PE/PB 记为高分。

        Args:
            chip_row: enriched Series（需含 pe_ttm, pb）
            params: {'value_pe_low': 20.0, 'value_pe_high': 60.0, 'value_pb_high': 6.0}

        Returns:
            score ∈ [0,1]。低估值高分。

        逻辑：
        - PE_TTM < 20 → 1.0（低估值）
        - 20 ≤ PE_TTM < 40 → 0.8
        - 40 ≤ PE_TTM < 60 → 0.6
        - PE_TTM ≥ 60 或 PB > 6 → 0.4（高估值，打折）
        - 缺失 → 0.5（中性）
        """
        pe_ttm = _f(chip_row, 'pe_ttm')
        pb = _f(chip_row, 'pb')
        if math.isnan(pe_ttm) or math.isnan(pb):
            return 0.5

        pe_low = params.get('value_pe_low', 20.0)
        pe_high = params.get('value_pe_high', 60.0)
        pb_high = params.get('value_pb_high', 6.0)

        # PE 分档
        if pe_ttm <= 0:
            return 0.5  # 负 PE（亏损）记中性
        if pe_ttm < pe_low:
            pe_score = 1.0
        elif pe_ttm < 40.0:
            pe_score = 0.8
        elif pe_ttm < pe_high:
            pe_score = 0.6
        else:
            pe_score = 0.4

        # PB 惩罚：PB 过高打折
        if pb > pb_high:
            pe_score *= 0.8  # 高 PB 打折 20%

        return pe_score

    @staticmethod
    def turnover_risk(chip_row) -> float:
        """换手率风险因子：过高换手率记为风险。

        Args:
            chip_row: enriched Series（需含 turnover_rate）

        Returns:
            risk ∈ [0,1]。高换手率记为高风险。

        逻辑：
        - turnover_rate < 3% → 0.0（正常）
        - 3% ≤ turnover_rate < 10% → 0.5（偏高）
        - ≥ 10% → 1.0（极高，短期炒作风险）
        - 缺失 → 0.0
        """
        turnover = _f(chip_row, 'turnover_rate')
        if math.isnan(turnover):
            return 0.0
        if turnover < 3.0:
            return 0.0
        elif turnover < 10.0:
            return 0.5
        else:
            return 1.0

    @staticmethod
    def composite_score(chip_row, current_price: float, params: dict) -> float:
        """综合得分：多因子融合（筹码 + 资金流 + 估值）。

        基础筹码得分：低位单峰密集得正分，高位/发散得0。
        增强因子融合：
        - base_score = concentration × (1-profit) × (1 + max(0, dev))
        - mf_signal = net_moneyflow_signal(chip_row, params)
        - value_score = value_quality_score(chip_row, params)
        - turnover_risk = turnover_risk(chip_row)
        - final_score = base_score × (1 + mf_weight × mf_signal) × (1 + value_weight × value_score) × (1 - turnover_risk × 0.3)

        Args:
            chip_row: pd.Series（支持 enriched Series，含资金流/估值字段）
            current_price: 当前价位（复权口径同 weight_avg_adj）
            params: 融合权重参数

        Returns:
            综合得分 ≥ 0。数据缺失 → 0.0。
        """
        pattern = ChipPeakFactors.classify_peak(chip_row, current_price, params)
        if pattern != PeakPattern.LOW_SINGLE_PEAK:
            return 0.0
        conc = ChipPeakFactors.concentration(chip_row)
        profit = ChipPeakFactors.profit_ratio(chip_row)
        dev = ChipPeakFactors.cost_deviation(chip_row, current_price)
        if math.isnan(conc) or math.isnan(profit):
            return 0.0

        # 基础筹码得分
        base_score = conc * (1 - profit) * (1 + max(0.0, dev))

        # 偏度调整（cmf-audit2.md 第5点）：用 cost_15/85pct 构造的方向性细化。
        # skew < 0（筹码往下偏，支撑强）→ 增益；skew > 0（往上偏，派发压力）→ 打折。
        # 调整幅度由 skew_weight 控制，默认 0.2（±20% 量级）。
        skew = ChipPeakFactors.cost_skewness(chip_row)
        if not math.isnan(skew):
            skew_weight = params.get('skew_weight', 0.2)
            # 限幅 [-1, 1] 避免极端值主导
            skew_clamped = max(-1.0, min(1.0, skew * 10.0))  # skew 量级通常 ~0.1，×10 放到 [-1,1]
            base_score *= (1.0 - skew_weight * skew_clamped)

        # 增强因子（向后兼容：缺失时仍用纯筹码得分）
        mf_weight = params.get('mf_weight', 0.3)
        value_weight = params.get('value_weight', 0.2)

        mf_signal = ChipPeakFactors.net_moneyflow_signal(chip_row, params)
        value_score = ChipPeakFactors.value_quality_score(chip_row, params)
        turnover_risk = ChipPeakFactors.turnover_risk(chip_row)

        # 融合公式
        # 资金流流入 +1 增益（mf_signal ∈ [0,1], 正流入）
        # 估值高分 +1 增益（value_score ∈ [0,1], 低估值高分）
        # 换手率风险：高风险 -30% 打折
        final_score = base_score * (1 + mf_weight * mf_signal) * (1 + value_weight * value_score) * (1 - turnover_risk * 0.3)

        return max(0.0, final_score)
