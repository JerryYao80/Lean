# CrowdingFactors.py - A股拥挤度因子库
# 纯静态因子计算方法，无LEAN依赖
# 支持交易拥挤度、资金拥挤度、筹码拥挤度三维度融合

import pandas as pd
import numpy as np
from typing import Optional
from dataclasses import dataclass


@dataclass
class CrowdingParams:
    """拥挤度因子参数配置"""
    # 交易拥挤度参数
    davol_short_window: int = 20
    davol_long_window: int = 120
    turnover_zscore_cap: float = 2.0
    volume_ratio_threshold: float = 1.5

    # 资金拥挤度参数
    net_mf_ratio_cap: float = 0.3
    margin_growth_threshold: float = 0.5
    northbound_ratio_cap: float = 10.0

    # 筹码拥挤度参数
    chip_bandwidth_threshold: float = 0.2
    winner_rate_threshold: float = 70.0

    # 权重配置
    trading_weight: float = 0.35
    fund_weight: float = 0.35
    chip_weight: float = 0.30

    # 拥挤度阈值
    high_crowding_threshold: float = 0.7
    low_crowding_threshold: float = 0.3


DEFAULT_PARAMS = CrowdingParams()


class CrowdingFactors:
    """
    A股拥挤度因子计算库

    三维度拥挤度：
    1. Trading Crowding - 交易活跃度异常（换手率/成交量突增）
    2. Fund Crowding - 资金涌入集中（净流入/融资余额增长）
    3. Chip Crowding - 筹码分布集中度（主力控盘程度）

    所有方法均为纯静态函数，输入crowding_row (pd.Series)，输出拥挤度分数 (0-1)
    """

    @staticmethod
    def trading_crowding(row: pd.Series, params: CrowdingParams) -> float:
        """
        交易拥挤度：DAVOL20 + 量比

        核心逻辑：
        - DAVOL20 = short_turnover / long_turnover - 1
        - DAVOL20 > 0 表示短期换手率高于长期均值（交易拥挤）
        - volume_ratio > 1 表示成交量放大

        输入字段：
        - davol20: 短期/长期换手率偏离度
        - volume_ratio: 当日量比

        输出：0-1，越大表示交易越拥挤
        """
        davol20 = float(row.get("davol20", 0.0))
        volume_ratio = float(row.get("volume_ratio", 1.0))

        # DAVOL20正值表示拥挤（short > long）
        # 放大到0-1区间：abs(davol20)通常在0-0.5
        davol_score = min(1.0, abs(davol20) * 2.0)

        # 量比偏离（volume_ratio=1为基准）
        volume_score = max(0.0, (volume_ratio - 1.0) / (params.volume_ratio_threshold - 1.0))
        volume_score = min(1.0, volume_score)

        # 加权融合
        trading_score = davol_score * 0.6 + volume_score * 0.4
        return trading_score

    @staticmethod
    def fund_crowding(row: pd.Series, params: CrowdingParams) -> float:
        """
        资金拥挤度：净流入率 + 融资余额增长 + 北向持股

        核心逻辑：
        - net_mf_ratio > 0 表示资金净流入（买入拥挤）
        - margin_growth > 0 表示融资余额增长（杠杆拥挤）
        - hk_hold_ratio 高表示外资集中持股

        输入字段：
        - net_mf_ratio: 净流入 / 成交额
        - margin_growth: 融资余额5日增长率
        - hk_hold_ratio: 北向持股比例

        输出：0-1，越大表示资金越拥挤
        """
        net_mf_ratio = float(row.get("net_mf_ratio", 0.0))
        margin_growth = float(row.get("margin_growth", 0.0))
        hk_hold_ratio = float(row.get("hk_hold_ratio", 0.0))

        # 净流入率归一化（通常在-0.2到0.2）
        net_flow_score = min(1.0, max(0.0, net_mf_ratio / params.net_mf_ratio_cap))

        # 融资增长归一化（通常在-0.1到0.1）
        margin_score = min(1.0, max(0.0, margin_growth / params.margin_growth_threshold))

        # 北向持股比例归一化（0-30%）
        hk_score = min(1.0, hk_hold_ratio / params.northbound_ratio_cap)

        # 加权融合
        fund_score = net_flow_score * 0.4 + margin_score * 0.4 + hk_score * 0.2
        return fund_score

    @staticmethod
    def chip_crowding(row: pd.Series, params: CrowdingParams) -> float:
        """
        筹码拥挤度：筹码带宽 + 盈利比例

        核心逻辑：
        - bandwidth = (cost_95 - cost_5) / weight_avg
        - bandwidth窄表示筹码集中（拥挤）
        - winner_rate高表示多数人盈利（派发风险）

        输入字段：
        - cost_5pct_adj: 5%分位成本
        - cost_95pct_adj: 95%分位成本
        - weight_avg_adj: 平均成本
        - winner_rate: 盈利比例

        输出：0-1，越大表示筹码越拥挤
        """
        cost_5 = float(row.get("cost_5pct_adj", 0.0))
        cost_95 = float(row.get("cost_95pct_adj", 0.0))
        weight_avg = float(row.get("weight_avg_adj", 1.0))
        winner_rate = float(row.get("winner_rate", 0.0))

        # 筹码带宽（bandwidth窄 = 拥挤）
        if weight_avg > 0 and cost_95 > cost_5:
            bandwidth = (cost_95 - cost_5) / weight_avg
            bandwidth_score = 1.0 - min(1.0, bandwidth / params.chip_bandwidth_threshold)
        else:
            bandwidth_score = 0.0

        # 盈利比例（winner_rate高 = 拥挤）
        winner_score = winner_rate / 100.0

        # 加权融合
        chip_score = bandwidth_score * 0.6 + winner_score * 0.4
        return chip_score

    @staticmethod
    def composite_crowding(row: pd.Series, params: Optional[CrowdingParams] = None) -> float:
        """
        综合拥挤度：三维度加权融合

        输入：crowding_row包含以下字段
        - davol20, volume_ratio (交易维度)
        - net_mf_ratio, margin_growth, hk_hold_ratio (资金维度)
        - cost_5/95pct_adj, weight_avg_adj, winner_rate (筹码维度)

        输出：0-1综合拥挤度分数
        - 0-0.3: 低拥挤（趋势可持续）
        - 0.3-0.7: 中度拥挤（中性）
        - 0.7-1.0: 高拥挤（回调风险）
        """
        if params is None:
            params = DEFAULT_PARAMS

        # 三维度分数
        trading_score = CrowdingFactors.trading_crowding(row, params)
        fund_score = CrowdingFactors.fund_crowding(row, params)
        chip_score = CrowdingFactors.chip_crowding(row, params)

        # 加权融合
        composite = (
            trading_score * params.trading_weight +
            fund_score * params.fund_weight +
            chip_score * params.chip_weight
        )

        return min(1.0, max(0.0, composite))

    @staticmethod
    def classify_crowding(crowding_score: float, params: Optional[CrowdingParams] = None) -> str:
        """
        拥挤度分类

        输出：
        - "LOW": 低拥挤（<0.3）
        - "MEDIUM": 中度拥挤（0.3-0.7）
        - "HIGH": 高拥挤（>0.7）
        """
        if params is None:
            params = DEFAULT_PARAMS

        if crowding_score < params.low_crowding_threshold:
            return "LOW"
        elif crowding_score > params.high_crowding_threshold:
            return "HIGH"
        else:
            return "MEDIUM"

    @staticmethod
    def crowding_signal(crowding_score: float, params: Optional[CrowdingParams] = None) -> float:
        """
        拥挤度交易信号（用于Alpha magnitude）

        核心逻辑：
        - 低拥挤 = 正向信号（趋势可持续）
        - 高拥挤 = 负向信号（回调风险）

        输出：-1到1的信号强度
        """
        if params is None:
            params = DEFAULT_PARAMS

        if crowding_score < params.low_crowding_threshold:
            # 低拥挤 → 正向信号（1.0 - crowding_score）
            return 1.0 - crowding_score
        elif crowding_score > params.high_crowding_threshold:
            # 高拥挤 → 负向信号
            return -(crowding_score - 0.7)
        else:
            # 中度拥挤 → 微弱信号
            return 0.0