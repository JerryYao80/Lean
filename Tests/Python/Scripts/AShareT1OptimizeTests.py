import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


def load_module(name: str):
    module_path = Path(__file__).resolve().parents[3] / 'Scripts' / name
    if not module_path.exists():
        raise AssertionError(f'Expected module to exist: {module_path}')

    spec = importlib.util.spec_from_file_location(name.replace('.py', ''), module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AShareT1OptimizeTests(unittest.TestCase):
    def write_parquet(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(path, index=False)

    def make_catalog(self, root: Path) -> Path:
        catalog = {
            'datasets': {
                'daily': {
                    'path': 'daily/ts_code={symbol}/data.parquet',
                    'date_field': 'trade_date',
                    'symbol_field': 'ts_code',
                },
                'stock_basic': {
                    'path': 'stock_basic/data.parquet',
                    'symbol_field': 'ts_code',
                },
            }
        }
        path = root / 'catalog.json'
        path.write_text(json.dumps(catalog), encoding='utf-8')
        return path

    def test_run_optimization_ranks_trials_and_writes_output(self):
        module = load_module('ashare_t1_optimize.py')

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog = self.make_catalog(root)
            tushare_root = root / 'tushare'
            output_path = root / 'optimize.json'

            self.write_parquet(tushare_root / 'stock_basic' / 'data.parquet', [
                {'ts_code': '600000.SH', 'market': '主板', 'list_status': 'L'},
                {'ts_code': '000001.SZ', 'market': '主板', 'list_status': 'L'},
            ])
            for symbol, closes in {
                '600000.SH': [10.0, 10.2, 10.3, 9.8, 9.6, 9.5, 9.7, 9.9],
                '000001.SZ': [12.0, 12.1, 12.3, 12.2, 12.0, 11.7, 11.8, 12.0],
            }.items():
                rows = []
                for index, close in enumerate(closes, start=1):
                    rows.append({
                        'ts_code': symbol,
                        'trade_date': f'202401{index:02d}',
                        'open': close,
                        'high': close * 1.01,
                        'low': close * 0.99,
                        'close': close,
                        'pct_chg': 0.0,
                    })
                self.write_parquet(tushare_root / 'daily' / f'ts_code={symbol}' / 'data.parquet', rows)

            config = {
                'tushare-data-path': str(tushare_root),
                'dataset-catalog': str(catalog),
                'start-date': '20240101',
                'end-date': '20240131',
                'universe': ['600000.SH', '000001.SZ'],
                'lookback-period': 5,
                'entry-threshold': -1.0,
                'exit-threshold': 0.0,
                'max-positions': 1,
                'position-size': 0.5,
                'initial-capital': 100000,
                'fee-rate': 0.001,
                'report-file': str(root / 'report.md'),
                'trade-report-file': str(root / 'trades.csv'),
                'daily-summary-file': str(root / 'daily.csv'),
                'summary-file': str(root / 'summary.json'),
            }
            config_path = root / 'config.json'
            config_path.write_text(json.dumps(config), encoding='utf-8')

            param_grid = {
                'entry-threshold': [-1.0, -0.5],
                'max-positions': [1],
            }
            param_grid_path = root / 'params.json'
            param_grid_path.write_text(json.dumps(param_grid), encoding='utf-8')

            result = module.run_optimization(config_path, param_grid_path, output_path)
            written = json.loads(output_path.read_text(encoding='utf-8'))

        self.assertEqual(result['trial_count'], 2)
        self.assertEqual(written['trial_count'], 2)
        self.assertIn('best_params', written)
        self.assertIn('best_summary', written)
        self.assertEqual(len(written['trials']), 2)


if __name__ == '__main__':
    unittest.main()
