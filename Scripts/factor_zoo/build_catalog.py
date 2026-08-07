"""FactorCatalog generator (Phase 4, spec §3.2).

Scans the factor zoo's static metadata (sourced from C# FactorRegistry registrations
+ FactorStoreConfig Barra/parquet adapters) and merges Phase 3 freshness state, then
writes Results/factor-zoo/factor-catalog.yaml. Consumed by:
  - Scripts/inspiration/hypothesize.py (reconstruction LLM prompt — id/name/category/
    selection_hint) so the LLM knows what factors the zoo offers.
  - Scripts/auto_optimize/bayesian_optimizer.py (factor-include/factor-weight params).

Why a static Python table (not a C# AllMetadata() call): FactorStore.AllMetadata() is
C#-only and not callable from Python; no existing Python module holds factor metadata.
The table below is the authoritative Python mirror of the C# registrations in
Common/Factors/Core/FactorRegistry.cs and Common/Factors/Store/FactorStoreConfig.cs,
plus the 7 Phase 5 factor_builders (accruals_sloan, gross_profitability, asset_growth,
ivol_20d, max_ret_20d, short_term_reversal, roe_change).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FRESHNESS_PATH = REPO_ROOT / "Results" / "factor-zoo" / "freshness.json"
DEFAULT_OUT_PATH = REPO_ROOT / "Results" / "factor-zoo" / "factor-catalog.yaml"
UNIVERSE = ["CSI300", "CSI500"]


# storage templates
def _parquet(root: str, col: str, influx: str = None) -> dict:
    s = {"kind": "parquet", "path": f"{root}/<date>/<ts_code>.parquet", "column": col}
    if influx:
        s["influx"] = influx
    return s


def _csv(path: str) -> dict:
    return {"kind": "csv", "path": path}


def _influx(measurement: str) -> dict:
    return {"kind": "influx", "influx": measurement}


def _runtime() -> dict:
    return {"kind": "runtime", "path": "FactorRegistry.Compute(history)"}


# WorldQuant Alpha101 — short Chinese selection hints per alpha number.
# Sourced from the 101 formulaic alphas (Kakushadze 2015). Used by the
# hypothesize LLM prompt to know each alpha's intent at a glance.
ALPHA101_HINTS = {
    1: "Ts_ArgMax 反转动量", 2: "量价相关性反转", 3: "开量相关性反转", 4: "低价股时序反转",
    5: "VWAP 均值回归", 6: "开量负相关", 7: "条件动量反转", 8: "5日收益动量反转",
    9: "极值条件动量", 10: "极值条件动量(rank)", 11: "VWAP-收盘极值×量变", 12: "量变×价反转",
    13: "量价协方差反转", 14: "收益动量×开量相关", 15: "高低量相关求和反转", 16: "高低量协方差反转",
    17: "复合时序反转", 18: "日内波动+开收相关", 19: "7日趋势符号×长动量", 20: "开盘缺口反转",
    21: "均线条件反转", 22: "高低量相关变化", 23: "新高反转", 24: "百日趋势条件",
    25: "收益×量×VWAP×振幅", 26: "量价时序相关极值", 27: "量VWAP相关阈值", 28: "VWAP-收盘缩放",
    29: "嵌套rank极值", 30: "趋势符号计数×量比", 31: "复合动量反转", 32: "均线偏离+长相关",
    33: "开收比反转", 34: "波动率比+动量", 35: "量价时序反转", 36: "复合多因子",
    37: "开收延迟相关+开收", 38: "收盘时序反转", 39: "7日动量×量比衰减", 40: "高波动×高低量相关",
    41: "高低几何均值-VWAP", 42: "VWAP-收盘均值回归", 43: "量比×价反转时序", 44: "高价量相关反转",
    45: "延迟收盘相关", 46: "10日趋势阈值", 47: "量价复合", 48: "收益自相关行业中性",
    49: "10日趋势阈值", 50: "量VWAP相关极值", 51: "10日趋势阈值", 52: "低价极值×长收益",
    53: "日内位置变化", 54: "开收高低幂比", 55: "价格位置×量相关", 56: "收益动量×市值",
    57: "VWAP-收盘/衰减argmax", 58: "VWAP行业中性×量", 59: "VWAP行业中性×量", 60: "日内位置×量缩放",
    61: "VWAP极值<量相关", 62: "VWAP量相关<开高低", 63: "行业中性收盘动量", 64: "量相关<位置变化",
    65: "量相关<开盘极值", 66: "VWAP动量+日内位置", 67: "高价极值^行业中性相关", 68: "高价量相关<价变化",
    69: "行业中性VWAP动量^相关", 70: "VWAP变化^行业中性相关", 71: "复合时序最大", 72: "高低量相关/时序相关",
    73: "VWAP动量最大", 74: "量相关<高低量相关", 75: "VWAP量相关<低价量相关", 76: "VWAP动量最大",
    77: "VWAP偏离最小", 78: "量相关^VWAP量相关", 79: "行业中性变化<时序相关", 80: "行业中性符号^相关",
    81: "量相关对数<时序相关", 82: "开盘动量最小", 83: "振幅延迟×量/位置", 84: "VWAP时序幂",
    85: "量相关^时序相关", 86: "量相关<开盘收盘", 87: "VWAP动量最大", 88: "rank差最小",
    89: "量相关-行业中性VWAP", 90: "收盘极值^行业中性相关", 91: "行业中性嵌套衰减", 92: "条件rank最小",
    93: "行业中性VWAP相关/动量", 94: "VWAP极值^时序相关", 95: "开盘极值<量相关时序", 96: "量相关最大",
    97: "行业中性动量-时序相关", 98: "VWAP量相关-argmin", 99: "量相关<低价量相关", 100: "行业中性复合",
    101: "日内动量(close-open)/(high-low)",
}


_ALPHA101_DESCRIPTIONS = None  # lazy cache


# ── Technical indicators (stk_factor_pro, 48 qfq factors) ──
# Each maps a stk_factor_pro column (qfq variant) to a factor_id. The indicator
# values are ALREADY computed by tushare; the builder only extracts the asof
# row — no re-computation. Backfilled by backfill_technical.py (per-ts_code by-year).
TECH_INDICATORS = {
    "macd_qfq": ("tech_macd", "MACD", "异同移动平均线柱值, 正=多头动能, 正向", "Trend"),
    "macd_dif_qfq": ("tech_macd_dif", "MACD DIF", "MACD 快线(DIF), 金叉死叉信号", "Trend"),
    "macd_dea_qfq": ("tech_macd_dea", "MACD DEA", "MACD 慢线(DEA), 信号线", "Trend"),
    "rsi_qfq_6": ("tech_rsi_6", "RSI(6)", "6日 RSI, >80超买<20超卖, 反向", "Trend"),
    "rsi_qfq_12": ("tech_rsi_12", "RSI(12)", "12日 RSI, 中短期强弱, 反向", "Trend"),
    "rsi_qfq_24": ("tech_rsi_24", "RSI(24)", "24日 RSI, 中期强弱, 反向", "Trend"),
    "kdj_k_qfq": ("tech_kdj_k", "KDJ K", "KDJ 之 K 线, >80超买<20超卖, 反向", "Trend"),
    "kdj_d_qfq": ("tech_kdj_d", "KDJ D", "KDJ 之 D 线, 慢线, 反向", "Trend"),
    "kdj_qfq": ("tech_kdj_j", "KDJ J", "KDJ 之 J 线, 灵敏超买超卖, 反向", "Trend"),
    "boll_upper_qfq": ("tech_boll_upper", "Boll Upper", "布林上轨, 价格触碰=超买, 反向", "Volatility"),
    "boll_mid_qfq": ("tech_boll_mid", "Boll Mid", "布林中轨(20日均线), 均值回归锚", "Trend"),
    "boll_lower_qfq": ("tech_boll_lower", "Boll Lower", "布林下轨, 触碰=超卖, 正向", "Volatility"),
    "bias1_qfq": ("tech_bias1", "BIAS1", "6日乖离率, 偏离均线, 反向", "Trend"),
    "bias2_qfq": ("tech_bias2", "BIAS2", "12日乖离率, 反向", "Trend"),
    "bias3_qfq": ("tech_bias3", "BIAS3", "24日乖离率, 反向", "Trend"),
    "cci_qfq": ("tech_cci", "CCI", "顺势指标, >100超买<-100超卖, 反向", "Trend"),
    "wr_qfq": ("tech_wr", "WR", "威廉指标, >20超买<-20超卖, 反向", "Trend"),
    "mfi_qfq": ("tech_mfi", "MFI", "资金流量指标, 量价RSI, 反向", "Volatility"),
    "mtm_qfq": ("tech_mtm", "MTM", "动量指标, 正=上涨动量, 正向", "Trend"),
    "roc_qfq": ("tech_roc", "ROC", "变动率, 12日收益动量, 正向", "Trend"),
    "obv_qfq": ("tech_obv", "OBV", "能量潮, 量价配合, 正向", "Liquidity"),
    "psy_qfq": ("tech_psy", "PSY", "心理线, >75超买<25超卖, 反向", "Trend"),
    "trix_qfq": ("tech_trix", "TRIX", "三重平滑均线, 趋势过滤, 正向", "Trend"),
    "dpo_qfq": ("tech_dpo", "DPO", "去趋势价格, 震荡指标, 反向", "Volatility"),
    "cr_qfq": ("tech_cr", "CR", "能量指标, >200超买, 反向", "Trend"),
    "emv_qfq": ("tech_emv", "EMV", "简易波动, 量价能量, 正向", "Liquidity"),
    "mass_qfq": ("tech_mass", "MASS", "梅斯线, >27反转, 反向", "Trend"),
    "asi_qfq": ("tech_asi", "ASI", "实质震荡指标, 趋势确认, 正向", "Trend"),
    "bbi_qfq": ("tech_bbi", "BBI", "多空指标, 综合均线, 正向", "Trend"),
    "atr_qfq": ("tech_atr", "ATR", "真实波幅, 高=高波动, 反向", "Volatility"),
    "vr_qfq": ("tech_vr", "VR", "容量比率, >350超买, 反向", "Liquidity"),
    "dmi_adx_qfq": ("tech_dmi_adx", "DMI ADX", "趋势强度, >25有趋势, 正向", "Trend"),
    "dmi_pdi_qfq": ("tech_dmi_pdi", "DMI PDI", "+DI 多头方向指标, 正向", "Trend"),
    "dmi_mdi_qfq": ("tech_dmi_mdi", "DMI MDI", "-DI 空头方向指标, 反向", "Trend"),
    "expma_12_qfq": ("tech_expma_12", "EXPMA12", "12日指数均线, 短期趋势", "Trend"),
    "expma_50_qfq": ("tech_expma_50", "EXPMA50", "50日指数均线, 中期趋势", "Trend"),
    "ktn_upper_qfq": ("tech_ktn_upper", "Keltner Upper", "肯特纳上轨, 反向", "Volatility"),
    "ktn_mid_qfq": ("tech_ktn_mid", "Keltner Mid", "肯特纳中轨, 均值锚", "Trend"),
    "ktn_down_qfq": ("tech_ktn_down", "Keltner Lower", "肯特纳下轨, 正向", "Volatility"),
    "taq_up_qfq": ("tech_taq_up", "TAQ Up", "通道上轨, 反向", "Volatility"),
    "taq_mid_qfq": ("tech_taq_mid", "TAQ Mid", "通道中轨, 均值锚", "Trend"),
    "taq_down_qfq": ("tech_taq_down", "TAQ Down", "通道下轨, 正向", "Volatility"),
    "dfma_dif_qfq": ("tech_dfma_dif", "DFMA DIF", "均价线差值(DIF), 正向", "Trend"),
    "dfma_difma_qfq": ("tech_dfma_difma", "DFMA DIFMA", "均价线差值均线, 反向", "Trend"),
    "xsii_td1_qfq": ("tech_xsii_td1", "XSII TD1", "薛斯通道内轨1, 反向", "Volatility"),
    "xsii_td2_qfq": ("tech_xsii_td2", "XSII TD2", "薛斯通道内轨2, 反向", "Volatility"),
    "xsii_td3_qfq": ("tech_xsii_td3", "XSII TD3", "薛斯通道外轨3, 反向", "Volatility"),
    "xsii_td4_qfq": ("tech_xsii_td4", "XSII TD4", "薛斯通道外轨4, 反向", "Volatility"),
}


def _tech_entry(col: str, info: tuple[str, str, str, str]) -> dict:
    fid, name, hint, category = info
    return {
        "id": fid,
        "name": name,
        "category": category,
        "compute_mode": "Precomputed",
        "storage": _parquet(f"result/factor-zoo/{fid}", fid, f"lean_factor_{fid}"),
        "tushare_deps": ["stk_factor_pro"],
        "selection_hint": hint,
        "parameters": {"indicator_col": col},
    }


def _load_alpha101_descriptions() -> dict:
    """Load Scripts/factor_zoo/alpha101_descriptions.yaml (101 entries).
    Returns {} if missing/corrupt (graceful degrade; catalog then omits the
    4 description fields for alphas, keeping only selection_hint).
    """
    global _ALPHA101_DESCRIPTIONS
    if _ALPHA101_DESCRIPTIONS is not None:
        return _ALPHA101_DESCRIPTIONS
    p = Path(__file__).parent / "alpha101_descriptions.yaml"
    if not p.exists():
        _ALPHA101_DESCRIPTIONS = {}
        return {}
    try:
        _ALPHA101_DESCRIPTIONS = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        _ALPHA101_DESCRIPTIONS = {}
    return _ALPHA101_DESCRIPTIONS


def _alpha_entry(n: int, hint: str) -> dict:
    aid = f"alpha{n:03d}"
    entry = {
        "id": aid,
        "name": f"WorldQuant Alpha#{n}",
        "category": "Alpha101",
        "compute_mode": "Precomputed",
        "storage": _parquet(f"result/factor-zoo/{aid}", aid, f"lean_factor_{aid}"),
        "tushare_deps": ["daily", "adj_factor", "daily_basic", "index_member_all"],
        "selection_hint": hint,
        "parameters": {"n": n},
    }
    desc = _load_alpha101_descriptions().get(aid, {})
    if desc:
        entry["intent"] = desc.get("intent", "")
        entry["scenarios"] = desc.get("scenarios", [])
        entry["direction"] = desc.get("direction", "")
        entry["family"] = desc.get("family", "")
    return entry


# fmt: off
# Static metadata table — 207 factors (36 FactorRegistry + 15 Barra + 7 Phase 5 + 101 Alpha101 + 48 Technical).
# Fields: id, name, category, compute_mode (Runtime|Precomputed), storage,
# tushare_deps, selection_hint, parameters.
FACTOR_METADATA: list[dict] = [
    # ── Volatility (5) ──
    {"id": "iv_pct_252d", "name": "252-Day IV Percentile", "category": "Volatility", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["daily_basic"], "selection_hint": "IV 历史分位, 高=期权贵, 通常反向", "parameters": {"lookback": 252}},
    {"id": "hv_20d", "name": "20-Day Realized Volatility", "category": "Volatility", "compute_mode": "Runtime", "storage": _runtime(), "tushare_deps": ["daily", "adj_factor"], "selection_hint": "20日已实现波动率, 高=高波动", "parameters": {"window": 20}},
    {"id": "vix", "name": "A-Share VIX (30d interpolated)", "category": "Volatility", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["opt_daily"], "selection_hint": "A 股 VIX, 高=恐慌, 反向", "parameters": {}},
    {"id": "iv_skew_252d", "name": "252-Day IV Skew", "category": "Volatility", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["opt_daily"], "selection_hint": "IV 偏度, 反映看跌需求, 正偏=避险情绪", "parameters": {"lookback": 252}},
    {"id": "iv_term_structure", "name": "IV Term Structure (near/next)", "category": "Volatility", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["opt_daily"], "selection_hint": "近远月 IV 价差, 正=近月贵(事件驱动)", "parameters": {}},

    # ── Trend (3) ──
    {"id": "momentum_20d", "name": "20-Day Momentum", "category": "Trend", "compute_mode": "Runtime", "storage": _runtime(), "tushare_deps": ["daily", "adj_factor"], "selection_hint": "20日动量, 高=近期强势, 通常正向", "parameters": {"window": 20}},
    {"id": "ma_cross_5_20", "name": "MA5/20 Cross", "category": "Trend", "compute_mode": "Runtime", "storage": _runtime(), "tushare_deps": ["daily"], "selection_hint": "5/20 均线交叉, 金叉=多头信号", "parameters": {"short": 5, "long": 20}},
    {"id": "rsi_14d", "name": "14-Day RSI", "category": "Trend", "compute_mode": "Runtime", "storage": _runtime(), "tushare_deps": ["daily"], "selection_hint": "14日 RSI, >70 超买<30 超卖, 反向", "parameters": {"window": 14}},

    # ── Value (3) ──
    {"id": "pe_pct_252d", "name": "PE 252d Percentile", "category": "Value", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["daily_basic"], "selection_hint": "PE 历史分位, 高=估值贵, 反向", "parameters": {}},
    {"id": "pb_pct_252d", "name": "PB 252d Percentile", "category": "Value", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["daily_basic"], "selection_hint": "PB 历史分位, 高=估值贵, 反向", "parameters": {}},
    {"id": "dividend_yield", "name": "Dividend Yield", "category": "Value", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["fund_div"], "selection_hint": "股息率, 高=价值型, 正向", "parameters": {}},

    # ── Quality (3) ──
    {"id": "roe", "name": "ROE", "category": "Quality", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["fina_indicator"], "selection_hint": "净资产收益率, 高=盈利强, 正向", "parameters": {}},
    {"id": "net_margin", "name": "Net Profit Margin", "category": "Quality", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["fina_indicator"], "selection_hint": "净利润率, 高=盈利质量好, 正向", "parameters": {}},
    {"id": "debt_to_asset", "name": "Debt-to-Asset", "category": "Quality", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["fina_indicator"], "selection_hint": "资产负债率, 高=杠杆高风险, 反向", "parameters": {}},

    # ── Sentiment (3) ──
    {"id": "crowding", "name": "Trading Crowding Score", "category": "Sentiment", "compute_mode": "Precomputed", "storage": _parquet("result/crowding-factor", "composite", "lean_ashare_crowding_factor"), "tushare_deps": ["daily_basic", "moneyflow", "margin_detail", "hsgt_top10", "cyq_perf"], "selection_hint": "拥挤度, 高=过热, 通常反向", "parameters": {}},
    {"id": "pcr", "name": "Put-Call Ratio", "category": "Sentiment", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["opt_daily"], "selection_hint": "认沽认购比, 高=看跌情绪, 反向指标", "parameters": {}},
    {"id": "northbound_pct", "name": "Northbound Holding %", "category": "Sentiment", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["moneyflow_hsgt"], "selection_hint": "北向持股占比, 高=外资看好, 正向", "parameters": {}},

    # ── Liquidity (2) ──
    {"id": "turnover_20d", "name": "20-Day Turnover Rate", "category": "Liquidity", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["daily_basic"], "selection_hint": "20日换手率, 高=活跃, 中性偏反向", "parameters": {}},
    {"id": "amihud_20d", "name": "20-Day Amihud Illiquidity", "category": "Liquidity", "compute_mode": "Runtime", "storage": _runtime(), "tushare_deps": ["daily"], "selection_hint": "Amihud 流动性不足, 高=流动性差, 正向(流动性溢价)", "parameters": {"window": 20}},

    # ── Chip (5) ──
    {"id": "chip_concentration", "name": "Chip Concentration", "category": "Chip", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["cyq_perf"], "selection_hint": "筹码集中度, 高=筹码集中, 正向", "parameters": {}},
    {"id": "chip_profit_ratio", "name": "Chip Profit Ratio", "category": "Chip", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["cyq_perf"], "selection_hint": "获利盘比例, 高=获利盘多, 反向", "parameters": {}},
    {"id": "chip_peak_pattern", "name": "Chip Peak Pattern", "category": "Chip", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["cyq_perf"], "selection_hint": "筹码峰形态, 单峰=趋势, 多峰=震荡", "parameters": {}},
    {"id": "chip_peak_composite", "name": "Chip Peak Composite", "category": "Chip", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["cyq_perf", "moneyflow"], "selection_hint": "筹码峰复合, 高=筹码结构优, 正向", "parameters": {}},
    {"id": "chip_cost_deviation", "name": "Chip Cost Deviation", "category": "Chip", "compute_mode": "Precomputed", "storage": _runtime(), "tushare_deps": ["cyq_perf"], "selection_hint": "筹码成本偏离, 高=价格偏离成本, 反向", "parameters": {}},

    # ── Forward (12) ──
    {"id": "big_order_net_flow", "name": "Big-Order Net Inflow Rate", "category": "Forward", "compute_mode": "Precomputed", "storage": _influx("lean_ashare_forward_factor"), "tushare_deps": ["moneyflow"], "selection_hint": "大单净流入率, 高=主力买入, 正向", "parameters": {}},
    {"id": "northbound_momentum", "name": "Northbound Capital Momentum (3D/5D)", "category": "Forward", "compute_mode": "Precomputed", "storage": _influx("lean_ashare_forward_factor"), "tushare_deps": ["moneyflow_hsgt"], "selection_hint": "北向资金动量, 高=外资持续流入, 正向", "parameters": {}},
    {"id": "auction_gap", "name": "Auction Gap Rate", "category": "Forward", "compute_mode": "Precomputed", "storage": _influx("lean_ashare_forward_factor"), "tushare_deps": ["stk_auction_o"], "selection_hint": "集合竞价缺口, 高=开盘情绪强, 正向", "parameters": {}},
    {"id": "momentum_acceleration", "name": "Momentum Acceleration", "category": "Forward", "compute_mode": "Precomputed", "storage": _influx("lean_ashare_forward_factor"), "tushare_deps": ["bak_daily"], "selection_hint": "动量加速度, 正=加速上涨, 正向", "parameters": {}},
    {"id": "volume_anomaly_zscore", "name": "Volume Anomaly Z-score", "category": "Forward", "compute_mode": "Precomputed", "storage": _influx("lean_ashare_forward_factor"), "tushare_deps": ["bak_daily"], "selection_hint": "成交量异常 Z 分, 高=量能异动, 正向", "parameters": {}},
    {"id": "earnings_surprise", "name": "Earnings Surprise vs Forecast Mid", "category": "Forward", "compute_mode": "Precomputed", "storage": _influx("lean_ashare_forward_factor"), "tushare_deps": ["express", "forecast"], "selection_hint": "盈余惊喜, 正=超预期, 正向", "parameters": {}},
    {"id": "disclosure_timing", "name": "Disclosure Advance/Delay Days", "category": "Forward", "compute_mode": "Precomputed", "storage": _influx("lean_ashare_forward_factor"), "tushare_deps": ["disclosure_date"], "selection_hint": "披露时机, 早披露=好消息, 正向", "parameters": {}},
    {"id": "insider_trade", "name": "Insider Trade", "category": "Forward", "compute_mode": "Precomputed", "storage": _influx("lean_ashare_forward_factor"), "tushare_deps": ["stk_holdertrade"], "selection_hint": "内部人交易, 高管净买入=利好, 正向", "parameters": {}},
    {"id": "pledge_risk", "name": "Pledge Risk", "category": "Forward", "compute_mode": "Precomputed", "storage": _influx("lean_ashare_forward_factor"), "tushare_deps": ["pledge_stat"], "selection_hint": "股权质押风险, 高=质押重, 反向", "parameters": {}},
    {"id": "m1_m2_scissors", "name": "M1-M2 Scissors", "category": "Forward", "compute_mode": "Precomputed", "storage": _influx("lean_ashare_forward_factor"), "tushare_deps": ["cn_m"], "selection_hint": "M1-M2 剪刀差, 正=资金活化, 正向", "parameters": {}},
    {"id": "theme_heat", "name": "Theme Heat", "category": "Forward", "compute_mode": "Precomputed", "storage": _influx("lean_ashare_forward_factor"), "tushare_deps": ["limit_list_d", "kpl_list"], "selection_hint": "题材热度, 高=题材过热, 反向", "parameters": {}},
    {"id": "convert_premium", "name": "Convertible Bond Premium", "category": "Forward", "compute_mode": "Precomputed", "storage": _influx("lean_ashare_forward_factor"), "tushare_deps": ["cb_share"], "selection_hint": "转股溢价率, 高=债性强股性弱, 反向", "parameters": {}},

    # ── Barra CNE5 V2 (15) ──
    {"id": "barra_beta", "name": "Barra Beta", "category": "Barra", "compute_mode": "Precomputed", "storage": _csv("Data/alternative/barra-cne5v2-factors/<mkt>/daily/<ticker>.csv"), "tushare_deps": ["daily"], "selection_hint": "市场敏感度 Beta, 高=高弹性", "parameters": {"weight": {"default": -0.05, "range": [-0.2, 0.2]}}},
    {"id": "barra_momentum", "name": "Barra Momentum", "category": "Barra", "compute_mode": "Precomputed", "storage": _csv("Data/alternative/barra-cne5v2-factors/<mkt>/daily/<ticker>.csv"), "tushare_deps": ["daily"], "selection_hint": "Barra 动量, 高=动量强, 正向", "parameters": {}},
    {"id": "barra_size", "name": "Barra Size", "category": "Barra", "compute_mode": "Precomputed", "storage": _csv("Data/alternative/barra-cne5v2-factors/<mkt>/daily/<ticker>.csv"), "tushare_deps": ["daily_basic"], "selection_hint": "市值规模, 大盘=低波动", "parameters": {}},
    {"id": "barra_earnyld", "name": "Barra Earnings Yield", "category": "Barra", "compute_mode": "Precomputed", "storage": _csv("Data/alternative/barra-cne5v2-factors/<mkt>/daily/<ticker>.csv"), "tushare_deps": ["income"], "selection_hint": "盈利收益率, 高=便宜, 正向", "parameters": {}},
    {"id": "barra_resvol", "name": "Barra Residual Volatility", "category": "Barra", "compute_mode": "Precomputed", "storage": _csv("Data/alternative/barra-cne5v2-factors/<mkt>/daily/<ticker>.csv"), "tushare_deps": ["daily"], "selection_hint": "残差波动, 高=特质波动大, 反向", "parameters": {}},
    {"id": "barra_growth", "name": "Barra Growth", "category": "Barra", "compute_mode": "Precomputed", "storage": _csv("Data/alternative/barra-cne5v2-factors/<mkt>/daily/<ticker>.csv"), "tushare_deps": ["income", "fina_indicator"], "selection_hint": "成长性, 高=高增长, 正向", "parameters": {}},
    {"id": "barra_btop", "name": "Barra Book-to-Price", "category": "Barra", "compute_mode": "Precomputed", "storage": _csv("Data/alternative/barra-cne5v2-factors/<mkt>/daily/<ticker>.csv"), "tushare_deps": ["balancesheet"], "selection_hint": "账面市值比, 高=价值型, 正向", "parameters": {}},
    {"id": "barra_leverage", "name": "Barra Leverage", "category": "Barra", "compute_mode": "Precomputed", "storage": _csv("Data/alternative/barra-cne5v2-factors/<mkt>/daily/<ticker>.csv"), "tushare_deps": ["balancesheet"], "selection_hint": "杠杆, 高=杠杆大, 反向", "parameters": {}},
    {"id": "barra_liquidity", "name": "Barra Liquidity", "category": "Barra", "compute_mode": "Precomputed", "storage": _csv("Data/alternative/barra-cne5v2-factors/<mkt>/daily/<ticker>.csv"), "tushare_deps": ["daily"], "selection_hint": "流动性, 高=交易活跃, 反向", "parameters": {}},
    {"id": "barra_nlsize", "name": "Barra Non-Linear Size", "category": "Barra", "compute_mode": "Precomputed", "storage": _csv("Data/alternative/barra-cne5v2-factors/<mkt>/daily/<ticker>.csv"), "tushare_deps": ["daily_basic"], "selection_hint": "非线性市值, 中盘暴露", "parameters": {}},
    {"id": "barra_moneyflow", "name": "Barra Moneyflow", "category": "Barra", "compute_mode": "Precomputed", "storage": _csv("Data/alternative/barra-cne5v2-factors/<mkt>/daily/<ticker>.csv"), "tushare_deps": ["moneyflow"], "selection_hint": "资金流, 高=资金净流入, 正向", "parameters": {}},
    {"id": "barra_quality", "name": "Barra Quality", "category": "Barra", "compute_mode": "Precomputed", "storage": _csv("Data/alternative/barra-cne5v2-factors/<mkt>/daily/<ticker>.csv"), "tushare_deps": ["fina_indicator"], "selection_hint": "质量, 高=盈利质量好, 正向", "parameters": {}},
    {"id": "barra_northbound", "name": "Barra Northbound", "category": "Barra", "compute_mode": "Precomputed", "storage": _csv("Data/alternative/barra-cne5v2-factors/<mkt>/daily/<ticker>.csv"), "tushare_deps": ["hsgt_top10"], "selection_hint": "北向暴露, 高=外资偏好, 正向", "parameters": {}},
    {"id": "barra_margin", "name": "Barra Margin", "category": "Barra", "compute_mode": "Precomputed", "storage": _csv("Data/alternative/barra-cne5v2-factors/<mkt>/daily/<ticker>.csv"), "tushare_deps": ["margin_detail"], "selection_hint": "融资融券, 高=杠杆资金活跃, 反向", "parameters": {}},
    {"id": "barra_chipcost", "name": "Barra Chip Cost", "category": "Barra", "compute_mode": "Precomputed", "storage": _csv("Data/alternative/barra-cne5v2-factors/<mkt>/daily/<ticker>.csv"), "tushare_deps": ["cyq_perf"], "selection_hint": "筹码成本, 高=成本偏离, 反向", "parameters": {}},

    # ── Phase 5: 7 new factors (parquet result/factor-zoo/<id>/) ──
    {"id": "accruals_sloan", "name": "Accruals (Sloan 1996)", "category": "Quality", "compute_mode": "Precomputed", "storage": _parquet("result/factor-zoo/accruals_sloan", "accruals_sloan", "lean_factor_accruals_sloan"), "tushare_deps": ["balancesheet", "cashflow"], "selection_hint": "Sloan 应计, 高应计=盈余质量差, 通常反向", "parameters": {}},
    {"id": "gross_profitability", "name": "Gross Profitability (Novy-Marx)", "category": "Quality", "compute_mode": "Precomputed", "storage": _parquet("result/factor-zoo/gross_profitability", "gross_profitability", "lean_factor_gross_profitability"), "tushare_deps": ["income", "balancesheet"], "selection_hint": "毛利盈利能力, 高=盈利强, 正向", "parameters": {}},
    {"id": "asset_growth", "name": "Asset Growth (Cooper-Gulen-Schill)", "category": "Investment", "compute_mode": "Precomputed", "storage": _parquet("result/factor-zoo/asset_growth", "asset_growth", "lean_factor_asset_growth"), "tushare_deps": ["balancesheet", "stock_basic"], "selection_hint": "资产增长率, 高=过度扩张, 反向", "parameters": {}},
    {"id": "roe_change", "name": "ROE YoY Change", "category": "Quality", "compute_mode": "Precomputed", "storage": _parquet("result/factor-zoo/roe_change", "roe_change", "lean_factor_roe_change"), "tushare_deps": ["fina_indicator"], "selection_hint": "ROE 同比变化, 正=质量改善, 正向(质量动量)", "parameters": {}},
    {"id": "ivol_20d", "name": "20d Idiosyncratic Volatility", "category": "Volatility", "compute_mode": "Precomputed", "storage": _parquet("result/factor-zoo/ivol_20d", "ivol_20d", "lean_factor_ivol_20d"), "tushare_deps": ["daily", "adj_factor", "index_daily"], "selection_hint": "特异性波动(市场模型残差), 高=特质风险大, 反向(低波动异象)", "parameters": {}},
    {"id": "max_ret_20d", "name": "20d MAX Return (Bali)", "category": "Trend", "compute_mode": "Precomputed", "storage": _parquet("result/factor-zoo/max_ret_20d", "max_ret_20d", "lean_factor_max_ret_20d"), "tushare_deps": ["daily"], "selection_hint": "20日最大日收益(取负), 高MAX=彩票偏好过热, 反向", "parameters": {}},
    {"id": "short_term_reversal", "name": "20d Short-Term Reversal", "category": "Reversal", "compute_mode": "Precomputed", "storage": _parquet("result/factor-zoo/short_term_reversal", "short_term_reversal", "lean_factor_short_term_reversal"), "tushare_deps": ["daily", "adj_factor"], "selection_hint": "1月反转(取负), 短期超涨回调, 反向", "parameters": {}},

    # ── Alpha101 (101 WorldQuant formulaic alphas) ──
    *[_alpha_entry(n, ALPHA101_HINTS[n]) for n in range(1, 102)],

    # ── Technical (48 stk_factor_pro qfq indicators) ──
    *[_tech_entry(col, info) for col, info in TECH_INDICATORS.items()],
]
# fmt: on


def load_freshness(path) -> dict:
    """Read Phase 3 freshness.json. Returns {} if path is None/missing/corrupt (never throws).

    None path means "no freshness data" — callers must opt in by passing an explicit
    path. Falling back to a default path here would mask missing data as "fresh",
    violating the honesty contract (test_missing_freshness_yields_unknown_status_null_date).
    """
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_catalog(freshness_path=None, out_path=None) -> dict:
    """Build the FactorCatalog dict and write it to out_path (yaml).

    Merges static FACTOR_METADATA with Phase 3 freshness (last_date/status).
    Returns the catalog dict.
    """
    freshness = load_freshness(freshness_path)
    factors = []
    for meta in FACTOR_METADATA:
        fid = meta["id"]
        fr = freshness.get(fid, {})
        entry = dict(meta)
        entry["last_date"] = fr.get("last_date")
        # Honesty contract: a factor with NO freshness entry is "unknown"
        # (not evaluatable) — never masquerade as "fresh". Only an explicit
        # freshness entry with status "fresh"/"stale" may carry that status.
        entry["status"] = fr.get("status") if fr.get("status") else "unknown"
        factors.append(entry)
    catalog = {
        "version": datetime.now(timezone.utc).isoformat(),
        "universe": list(UNIVERSE),
        "factors": factors,
    }
    out = Path(out_path) if out_path else DEFAULT_OUT_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(catalog, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return catalog


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FactorCatalog generator (spec §3.2)")
    ap.add_argument("--freshness-path", default=None, help="Phase 3 freshness.json (default Results/factor-zoo/freshness.json)")
    ap.add_argument("--out-path", default=None, help="output yaml (default Results/factor-zoo/factor-catalog.yaml)")
    ap.add_argument("--print", action="store_true", help="print catalog to stdout")
    args = ap.parse_args(argv)
    catalog = build_catalog(freshness_path=args.freshness_path, out_path=args.out_path)
    print(f"Wrote {len(catalog['factors'])} factors to "
          f"{args.out_path or DEFAULT_OUT_PATH}")
    if args.print:
        print(yaml.safe_dump(catalog, allow_unicode=True, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
