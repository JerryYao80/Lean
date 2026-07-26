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


# fmt: off
# Static metadata table — 48 factors (33 FactorRegistry + 15 Barra).
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
