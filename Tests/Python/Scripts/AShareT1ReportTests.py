import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


def load_module():
    module_path = Path(__file__).resolve().parents[3] / 'Scripts' / 'ashare_t1_report.py'
    if not module_path.exists():
        raise AssertionError(f'Expected module to exist: {module_path}')

    spec = importlib.util.spec_from_file_location('ashare_t1_report', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AShareT1ReportTests(unittest.TestCase):
    def test_write_report_creates_markdown_from_summary_json(self):
        module = load_module()
        summary = {
            'start_date': '20240101',
            'end_date': '20240131',
            'initial_capital': 1000000,
            'universe_count': 2,
            'loaded_symbol_count': 2,
            'total_return': 0.1234,
            'final_equity': 1123400,
            'trade_days': 20,
            'completed_trades': 3,
            'open_positions': 1,
            'lookback_period': 5,
            'entry_threshold': -1.0,
            'exit_threshold': 0.0,
            'max_positions': 2,
            'position_size': 0.5,
            'fee_rate': 0.001,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary_path = root / 'summary.json'
            summary_path.write_text(json.dumps(summary), encoding='utf-8')

            output_dir = root / 'reports'
            written = module.write_report(module.load_backtest_result(summary_path), output_dir)

            text = written.read_text(encoding='utf-8')

        self.assertTrue(written.name.endswith('.md'))
        self.assertIn('A股 T+1 回测报告', text)
        self.assertIn('总收益率: 12.34%', text)
        self.assertIn('完整交易次数: 3', text)


if __name__ == '__main__':
    unittest.main()
