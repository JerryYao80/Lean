"""
IC验证脚本：样本外验证筹码因子有效性。
门控：IC_mean >= 0.03 才继续集成到 LEAN。

完整版需 daily parquet 计算未来收益的 Spearman Rank IC。
当前骨架版验证因子可计算性（Phase 1 数学校验）。
"""
import sys
import os
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
from ToolBox.ChipDataLoader import ChipDataLoader


def _load_factors():
    """Load ChipPeakFactors via importlib (Algorithm.Python has dot in name)."""
    factors_path = ROOT / 'Algorithm.Python' / 'ChipPeakFactors.py'
    spec = importlib.util.spec_from_file_location('ChipPeakFactors', factors_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ChipPeakFactors


def run_ic_test(start_date: str, end_date: str, data_folder: str) -> dict:
    """
    遍历交易日，全A股计算复合得分。
    Phase 1 (骨架): 验证因子在真实数据上可计算且分布合理。
    """
    ChipPeakFactors = _load_factors()
    loader = ChipDataLoader(data_folder)
    cyq_path = Path(data_folder) / 'cyq_chips'

    codes = [d.split('=')[1] for d in os.listdir(cyq_path) if d.startswith('ts_code=')]
    codes = sorted(codes)

    scores = []
    processed = 0
    failed = 0
    batch_size = 500
    trade_date = end_date.replace('-', '')

    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        for ts_code, chip_df in loader.load_batch(batch, trade_date):
            try:
                current_price = float(chip_df['corrected_price'].max())
                score = ChipPeakFactors.composite_score(
                    chip_df, current_price, ChipPeakFactors.DEFAULT_PARAMS
                )
                scores.append(score)
                processed += 1
            except Exception:
                failed += 1

    scores_arr = np.array(scores)
    return {
        'total_codes': len(codes),
        'processed': processed,
        'failed': failed,
        'score_mean': float(scores_arr.mean()) if len(scores_arr) > 0 else 0.0,
        'score_std': float(scores_arr.std()) if len(scores_arr) > 0 else 0.0,
        'positive_ratio': float((scores_arr > 0).mean()) if len(scores_arr) > 0 else 0.0,
        'note': 'Phase1 skeleton - full IC needs daily returns'
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Chip Peak Factor IC Validator')
    parser.add_argument('--start', required=True, help='YYYY-MM-DD')
    parser.add_argument('--end', required=True, help='YYYY-MM-DD')
    parser.add_argument('--data_folder', default='/home/project/tushare-downloader/tushare_data_v2')
    args = parser.parse_args()

    result = run_ic_test(args.start, args.end, args.data_folder)
    print("=== Chip Peak IC Validation ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
    print()
    print("Gate: positive_ratio indicates factor activity (target: meaningful non-zero fraction)")
