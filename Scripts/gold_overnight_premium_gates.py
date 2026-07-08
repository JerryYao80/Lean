#!/usr/bin/env python3
"""Gate 0/1/2 校验器。设计 §6。
Gate 0: 数据完整性（zeroed-field 检测、范围对齐）。
Gate 1: 成本敏感度。
Gate 2: IC 有效性 kill switch（OOS IC > 0，否则 EFFECTIVENESS_FAIL）。
"""
from __future__ import annotations
from typing import Tuple
from datetime import datetime
import numpy as np
import pandas as pd


def gate0_zeroed_field(sig: pd.DataFrame, threshold_pct: float = 0.05) -> Tuple[bool, str]:
    """Z_signal 非 NaN/非零比例须 > 1 - threshold。复用 BarraCNE5V4 zeroed-field 语义。"""
    if "z_signal" not in sig.columns:
        return False, "z_signal column missing"
    total = len(sig)
    if total == 0:
        return False, "empty signal frame"
    nan_or_zero = sig["z_signal"].isna() | (sig["z_signal"] == 0)
    bad_ratio = nan_or_zero.sum() / total
    if bad_ratio > threshold_pct:
        return False, f"z_signal NaN/zero ratio {bad_ratio:.1%} > {threshold_pct:.0%}"
    return True, "ok"


def gate0_mapping_alignment(au: pd.DataFrame, mapping: pd.DataFrame) -> Tuple[bool, str]:
    """fut_mapping(AUL.SHF) 范围须与 fut_daily(AU.SHF) 对齐。设计 §6.1 + §1.3 约束3。"""
    if au.empty or mapping.empty:
        return False, "au or mapping empty"
    au_span = (str(au["trade_date"].min()), str(au["trade_date"].max()))
    mp_span = (str(mapping["trade_date"].min()), str(mapping["trade_date"].max()))
    def _d(s): return datetime.strptime(str(s), "%Y%m%d")
    start_diff = abs((_d(au_span[0]) - _d(mp_span[0])).days)
    end_diff = abs((_d(au_span[1]) - _d(mp_span[1])).days)
    if start_diff > 730:
        return False, f"mapping start {mp_span[0]} vs au start {au_span[0]} differ > 2y"
    if end_diff > 2:
        return False, f"mapping end {mp_span[1]} vs au end {au_span[1]} differ > 2d"
    return True, f"aligned au={au_span} mapping={mp_span}"


def gate1_cost_sensitivity(sig: pd.DataFrame, cost_bps: float = 15) -> Tuple[bool, str]:
    """|gap_expected| 期望收益须 > 往返成本。设计 §6.2。"""
    if sig.empty:
        return False, "empty"
    mean_gap = sig["gap_expected"].abs().mean()
    if np.isnan(mean_gap):
        return False, "gap_expected all NaN"
    gap_bps = mean_gap * 10000
    if gap_bps <= cost_bps:
        return False, f"mean |gap| {gap_bps:.1f}bps <= cost {cost_bps}bps"
    return True, f"mean |gap| {gap_bps:.1f}bps > cost {cost_bps}bps"


def gate2_ic_kill_switch(sig: pd.DataFrame, oos_split: float = 0.7) -> Tuple[bool, str]:
    """OOS IC > 0 且 20 日 rolling IC 符号一致性 > 60%。否则 EFFECTIVENESS_FAIL。
    设计 §6.3: 这是"有效而非仅可用"的硬执行。失败附 ft_mins 升级说明。"""
    if sig.empty or "forward_return" not in sig.columns:
        return False, "forward_return missing (need signal + forward return for IC)"
    sig = sig.dropna(subset=["signal", "forward_return"]).reset_index(drop=True)
    if len(sig) < 40:
        return False, f"insufficient rows for IC: {len(sig)}"
    oos = sig.iloc[int(len(sig) * oos_split):]
    if len(oos) < 10:
        return False, "OOS too short"
    # NaN-safe Pearson IC: constant signal → 0 variance → corrcoef yields NaN → EFFECTIVENESS_FAIL
    with np.errstate(invalid="ignore", divide="ignore"):
        ic_oos = np.corrcoef(oos["signal"], oos["forward_return"])[0, 1]
    if np.isnan(ic_oos) or ic_oos <= 0:
        return False, (f"EFFECTIVENESS_FAIL: OOS IC={ic_oos:.3f} <= 0. "
                       f"日线代理信号无预测力。升级路径: 需 ft_mins (10000 积分) 做真夜盘隔离。")
    rolling_ic = sig["signal"].rolling(20).corr(sig["forward_return"]).dropna()
    if len(rolling_ic) == 0:
        return False, "rolling IC empty"
    sign_consistency = (rolling_ic > 0).sum() / len(rolling_ic)
    if sign_consistency < 0.6:
        return False, f"rolling IC sign consistency {sign_consistency:.1%} < 60%"
    return True, f"OOS IC={ic_oos:.3f}, sign consistency={sign_consistency:.1%}"


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", required=True)
    ap.add_argument("--au", default=None)
    ap.add_argument("--mapping", default=None)
    args = ap.parse_args()
    sig = pd.read_parquet(args.signals)
    print("=== Gate 0 ===")
    ok, msg = gate0_zeroed_field(sig)
    print(f"zeroed-field: {ok} | {msg}")
    if args.au and args.mapping:
        au = pd.read_parquet(args.au)
        mp = pd.read_parquet(args.mapping)
        ok2, msg2 = gate0_mapping_alignment(au, mp)
        print(f"mapping-align: {ok2} | {msg2}")
    print("=== Gate 1 ===")
    ok, msg = gate1_cost_sensitivity(sig)
    print(f"cost: {ok} | {msg}")
    print("=== Gate 2 (需 forward_return 列；导出器可加) ===")
    if "forward_return" in sig.columns:
        ok, msg = gate2_ic_kill_switch(sig)
        print(f"ic: {ok} | {msg}")
    else:
        print("skipped (no forward_return column)")


if __name__ == "__main__":
    main()
