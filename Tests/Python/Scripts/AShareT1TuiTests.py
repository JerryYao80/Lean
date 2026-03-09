import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


def load_module():
    module_path = Path(__file__).resolve().parents[3] / 'Scripts' / 'ashare_t1_tui.py'
    if not module_path.exists():
        raise AssertionError(f'Expected module to exist: {module_path}')

    spec = importlib.util.spec_from_file_location('ashare_t1_tui', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AShareT1TuiTests(unittest.TestCase):
    def test_compute_position_rows_marks_t1_unavailable_quantity(self):
        module = load_module()
        rows = module.compute_position_rows([
            {'symbol': '600000.SH', 'name': '浦发银行', 'quantity': 1000, 'available_quantity': 0, 'cost_price': 8.9, 'last_price': 8.6},
            {'symbol': '000001.SZ', 'name': '平安银行', 'quantity': 500, 'available_quantity': 500, 'cost_price': 12.0, 'last_price': 12.4},
        ])

        self.assertEqual(rows[0]['available_label'], '0 (T+1)')
        self.assertLess(rows[0]['pnl'], 0)
        self.assertEqual(rows[1]['available_label'], '500')
        self.assertGreater(rows[1]['pnl'], 0)

    def test_load_runtime_state_sorts_latest_signals_first(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            portfolio_path = root / 'portfolio.json'
            signals_path = root / 'signals.json'
            portfolio_path.write_text(json.dumps({'cash': 100000, 'positions': []}), encoding='utf-8')
            signals_path.write_text(json.dumps([
                {'timestamp': '2024-01-03T14:50:00', 'action': 'SELL', 'symbol': '600000.SH'},
                {'timestamp': '2024-01-03T09:35:00', 'action': 'BUY', 'symbol': '000001.SZ'},
            ]), encoding='utf-8')

            state = module.load_runtime_state(portfolio_path, signals_path)

        self.assertEqual(state['signals'][0]['action'], 'SELL')
        self.assertEqual(state['signals'][1]['action'], 'BUY')

    def test_load_runtime_state_normalizes_pascal_case_and_jsonl_signals(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            portfolio_path = root / 'portfolio.json'
            signals_path = root / 'signals.jsonl'
            portfolio_path.write_text(json.dumps({'cash': 100000, 'positions': []}), encoding='utf-8')
            signals_path.write_text(
                '\n'.join([
                    json.dumps({'Timestamp': '2024-01-03T09:35:00', 'Action': 'BUY', 'Symbol': '000001.SZ', 'Name': '平安银行'}),
                    json.dumps({'Timestamp': '2024-01-03T14:50:00', 'Action': 'SELL', 'Symbol': '600000.SH', 'Name': '浦发银行'}),
                ]),
                encoding='utf-8',
            )

            state = module.load_runtime_state(portfolio_path, signals_path)

        self.assertEqual(state['signals'][0]['action'], 'SELL')
        self.assertEqual(state['signals'][0]['name'], '浦发银行')
        self.assertEqual(state['signals'][1]['symbol'], '000001.SZ')

    def test_load_tui_config_supports_lean_launcher_parameters(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / 'Launcher' / 'config' / 'config-ashare-t1-live-paper.json'
            (root / 'Launcher' / 'bin' / 'Debug').mkdir(parents=True, exist_ok=True)
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps({
                'algorithm-type-name': 'AShareT1MeanReversionAlgorithm',
                'job-queue-handler': 'QuantConnect.Queues.JobQueue',
                'parameters': {
                    'tushare-data-path': '/tmp/tushare',
                    'signal-file': '../../../Results/ashare-t1-signals.json',
                    'portfolio-snapshot-file': '../../../Results/ashare-t1-portfolio.json',
                    'dataset-catalog': '../../../Launcher/config/config-ashare-dataset-catalog.json',
                },
            }), encoding='utf-8')

            config = module.load_tui_config(config_path)

        self.assertEqual(config['tushare-data-path'], '/tmp/tushare')
        self.assertEqual(Path(config['signal-file']), (root / 'Results' / 'ashare-t1-signals.json').resolve())
        self.assertEqual(Path(config['portfolio-snapshot-file']), (root / 'Results' / 'ashare-t1-portfolio.json').resolve())
        self.assertEqual(Path(config['dataset-catalog']), (root / 'Launcher' / 'config' / 'config-ashare-dataset-catalog.json').resolve())


if __name__ == '__main__':
    unittest.main()
