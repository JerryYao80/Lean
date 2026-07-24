"""
IC验证脚本：样本外验证筹码因子有效性（cyq_perf）。
门控：positive_ratio（低位建仓区占比）合理非零，且 score_mean 显著大于 0。

完整 IC（Spearman Rank IC vs 前向收益）需 daily 收益序列，留作 Phase 2。
当前骨架版验证：因子在真实 cyq_perf 上可计算、分布合理、有非零信号。
"""
import sys
import os
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from ToolBox.ChipDataLoader import ChipDataLoader


def _load_factors():
    """Load ChipPeakFactors via importlib (Algorithm.Python has dot in name)."""
    factors_path = ROOT / 'Algorithm.Python' / 'ChipPeakFactors.py'
    spec = importlib.util.spec_from_file_location('ChipPeakFactors', factors_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ChipPeakFactors, mod.PeakPattern


def _close_price(data_folder: str, ts_code: str, trade_date: str) -> float:
    """取 daily parquet 的收盘价（回退到最近交易日）。失败返回 NaN。"""
    daily_file = Path(data_folder) / 'daily' / f'ts_code={ts_code}' / 'data.parquet'
    if not daily_file.exists():
        return float('nan')
    try:
        df = pd.read_parquet(daily_file)
        df['trade_date'] = df['trade_date'].astype(str)
        recent = df[df['trade_date'] <= trade_date]
        if len(recent) == 0:
            return float('nan')
        return float(recent.sort_values('trade_date').iloc[-1]['close'])
    except Exception:
        return float('nan')


def run_ic_test(start_date: str, end_date: str, data_folder: str) -> dict:
    """
    遍历 cyq_perf 全A股，计算复合得分与形态分布。
    Phase 1 (骨架): 验证因子可计算且分布合理。
    增强模式：加载 moneyflow + daily_basic（若可用）。
    """
    ChipPeakFactors, PeakPattern = _load_factors()
    loader = ChipDataLoader(data_folder)
    cyq_path = Path(data_folder) / 'cyq_perf'

    codes = [d.split('=')[1] for d in os.listdir(cyq_path) if d.startswith('ts_code=')]
    codes = sorted(codes)

    scores = []
    concentrations = []
    profits = []
    mf_signals = []
    value_scores = []
    pattern_counts = {p.value: 0 for p in PeakPattern}
    processed = 0
    failed = 0
    batch_size = 500
    trade_date = end_date.replace('-', '')

    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        # 使用 enriched_batch 加载筹码 + 资金流 + 估值
        for ts_code, chip_row in loader.enriched_batch(batch, trade_date):
            try:
                current_price = _close_price(data_folder, ts_code, trade_date)
                if np.isnan(current_price):
                    failed += 1
                    continue
                score = ChipPeakFactors.composite_score(
                    chip_row, current_price, ChipPeakFactors.DEFAULT_PARAMS
                )
                pattern = ChipPeakFactors.classify_peak(
                    chip_row, current_price, ChipPeakFactors.DEFAULT_PARAMS
                )
                conc = ChipPeakFactors.concentration(chip_row)
                profit = ChipPeakFactors.profit_ratio(chip_row)
                # 增强因子统计
                mf_signal = ChipPeakFactors.net_moneyflow_signal(chip_row, ChipPeakFactors.DEFAULT_PARAMS)
                value_score = ChipPeakFactors.value_quality_score(chip_row, ChipPeakFactors.DEFAULT_PARAMS)

                scores.append(score)
                if not np.isnan(conc):
                    concentrations.append(conc)
                if not np.isnan(profit):
                    profits.append(profit)
                mf_signals.append(mf_signal)
                value_scores.append(value_score)
                pattern_counts[pattern.value] += 1
                processed += 1
            except Exception:
                failed += 1

    scores_arr = np.array(scores) if scores else np.array([0.0])
    conc_arr = np.array(concentrations) if concentrations else np.array([0.0])
    profit_arr = np.array(profits) if profits else np.array([0.0])
    mf_arr = np.array(mf_signals) if mf_signals else np.array([0.0])
    value_arr = np.array(value_scores) if value_scores else np.array([0.0])
    return {
        'total_codes': len(codes),
        'processed': processed,
        'failed': failed,
        'score_mean': float(scores_arr.mean()),
        'score_std': float(scores_arr.std()),
        'positive_ratio': float((scores_arr > 0).mean()),
        'concentration_mean': float(conc_arr.mean()),
        'profit_ratio_mean': float(profit_arr.mean()),
        'mf_signal_mean': float(mf_arr.mean()),
        'value_score_mean': float(value_arr.mean()),
        'pattern_distribution': pattern_counts,
        'note': 'Phase1 skeleton (cyq_perf + moneyflow + daily_basic) - full IC needs forward daily returns',
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Chip Peak Factor IC Validator')
    parser.add_argument('--start', required=True, help='YYYY-MM-DD')
    parser.add_argument('--end', required=True, help='YYYY-MM-DD')
    parser.add_argument('--data_folder', default='/home/project/tushare-downloader/tushare_data_v2')
    args = parser.parse_args()

    result = run_ic_test(args.start, args.end, args.data_folder)
    print("=== Chip Peak IC Validation (cyq_perf) ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
    print()
    print("Gate: positive_ratio 合理非零 + pattern_distribution 含 LOW_SINGLE_PEAK")
