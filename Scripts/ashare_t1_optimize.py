#!/usr/bin/env python3

import argparse
import itertools
import json
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from ashare_t1_backtest import load_pipeline_config, run_backtest


def load_param_grid(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def iter_param_combinations(param_grid: dict) -> list[dict]:
    keys = list(param_grid.keys())
    values = [param_grid[key] for key in keys]
    return [dict(zip(keys, combination)) for combination in itertools.product(*values)]


def run_optimization(config_path: str | Path, param_grid_path: str | Path, output_path: str | Path | None = None) -> dict:
    param_grid = load_param_grid(param_grid_path)
    trials = []
    for params in iter_param_combinations(param_grid):
        config = load_pipeline_config(config_path, params)
        summary = run_backtest(config)
        trials.append({
            'params': params,
            'summary': summary,
        })

    trials = sorted(
        trials,
        key=lambda trial: (
            trial['summary'].get('total_return', 0),
            trial['summary'].get('final_equity', 0),
        ),
        reverse=True,
    )
    result = {
        'trial_count': len(trials),
        'best_params': trials[0]['params'] if trials else {},
        'best_summary': trials[0]['summary'] if trials else {},
        'trials': trials,
    }

    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run parameter optimization for A-share T+1 backtests')
    parser.add_argument('--config', required=True)
    parser.add_argument('--param-grid', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--workers', type=int, default=1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_optimization(args.config, args.param_grid, args.output)
    print(json.dumps({
        'trial_count': result['trial_count'],
        'best_params': result['best_params'],
        'best_total_return': result['best_summary'].get('total_return', 0),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
