import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / 'Scripts' / 'ashare_t1_backtest.py'
    if not module_path.exists():
        raise AssertionError(f'Expected module to exist: {module_path}')

    spec = importlib.util.spec_from_file_location('ashare_t1_backtest', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AShareT1BacktestTests(unittest.TestCase):
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

    def test_prepare_symbol_frame_computes_mean_reversion_zscore(self):
        module = load_module()
        frame = pd.DataFrame([
            {'trade_date': f'202401{day:02d}', 'ts_code': '600000.SH', 'open': 10 + day, 'high': 10.5 + day, 'low': 9.5 + day, 'close': 10 + day, 'pct_chg': 1.0}
            for day in range(1, 8)
        ])
        frame.loc[6, 'close'] = 8.0

        prepared = module.prepare_symbol_frame('600000.SH', frame, lookback_period=5)
        last_row = prepared.iloc[-1]

        self.assertIn('zscore', prepared.columns)
        self.assertIn('signal_enter', prepared.columns)
        self.assertLess(last_row['zscore'], 0)

    def test_backtest_from_prepared_data_respects_t1_exit_delay(self):
        module = load_module()
        panel = pd.DataFrame([
            {'trade_date': '20240102', 'symbol': '600000.SH', 'close': 10.0, 'open': 10.0, 'zscore': -2.5, 'signal_enter': True, 'signal_exit': False},
            {'trade_date': '20240103', 'symbol': '600000.SH', 'close': 10.6, 'open': 10.2, 'zscore': 0.5, 'signal_enter': False, 'signal_exit': True},
            {'trade_date': '20240104', 'symbol': '600000.SH', 'close': 10.7, 'open': 10.5, 'zscore': 0.2, 'signal_enter': False, 'signal_exit': True},
        ])

        daily, trades, summary = module.backtest_from_prepared_data(
            panel,
            initial_capital=100000,
            max_positions=1,
            position_size=0.5,
            fee_rate=0.001,
        )

        self.assertEqual(trades.iloc[0]['action'], 'BUY')
        self.assertEqual(trades.iloc[0]['trade_date'], '20240102')
        self.assertEqual(trades.iloc[1]['action'], 'SELL')
        self.assertEqual(trades.iloc[1]['trade_date'], '20240103')
        self.assertEqual(summary['completed_trades'], 1)
        self.assertGreaterEqual(daily.iloc[-1]['equity'], 0)

    def test_run_backtest_loads_universe_and_writes_report_files(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog = self.make_catalog(root)
            tushare_root = root / 'tushare'
            report_path = root / 'report.md'
            trade_report = root / 'trades.csv'
            daily_report = root / 'daily.csv'
            summary_report = root / 'summary.json'

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
                'report-file': str(report_path),
                'trade-report-file': str(trade_report),
                'daily-summary-file': str(daily_report),
                'summary-file': str(summary_report),
            }
            config_path = root / 'config.json'
            config_path.write_text(json.dumps(config), encoding='utf-8')

            summary = module.run_backtest(module.load_pipeline_config(config_path))

            self.assertIn('trade_days', summary)
            self.assertTrue(report_path.exists())
            self.assertTrue(trade_report.exists())
            self.assertTrue(daily_report.exists())
            self.assertTrue(summary_report.exists())


if __name__ == '__main__':
    unittest.main()
