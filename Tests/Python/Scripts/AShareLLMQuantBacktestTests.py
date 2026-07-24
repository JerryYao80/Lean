import importlib.util
import unittest
from pathlib import Path

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / 'Scripts' / 'ashare_llm_quant_backtest.py'
    if not module_path.exists():
        raise AssertionError(f'Expected module to exist: {module_path}')

    spec = importlib.util.spec_from_file_location('ashare_llm_quant_backtest', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AShareLLMQuantBacktestTests(unittest.TestCase):
    def test_latest_snapshot_by_date_uses_most_recent_prior_snapshot(self):
        module = load_module()

        mapping = module.latest_snapshot_by_date(
            ['20240131', '20240229', '20240329'],
            ['20240130', '20240228'],
        )

        self.assertEqual(mapping['20240131'], '20240130')
        self.assertEqual(mapping['20240229'], '20240228')
        self.assertEqual(mapping['20240329'], '20240228')

    def test_simulate_portfolio_applies_new_weights_on_next_trade_day(self):
        module = load_module()

        trade_dates = ['20240131', '20240201', '20240202']
        daily_frames = {
            '20240131': pd.DataFrame([{'symbol': '000001.SZ', 'pct_chg': 10.0}]).set_index('symbol'),
            '20240201': pd.DataFrame([{'symbol': '000001.SZ', 'pct_chg': 10.0}]).set_index('symbol'),
            '20240202': pd.DataFrame([{'symbol': '000001.SZ', 'pct_chg': 0.0}]).set_index('symbol'),
        }
        rebalance_targets = {
            '20240131': {
                'signal_date': '20240131',
                'snapshot_date': '20240130',
                'risk_state': 'RISK_ON',
                'target_exposure': 1.0,
                'selected_count': 1,
                'selected_symbols': '000001.SZ',
                'target_weights': {'000001.SZ': 1.0},
            }
        }

        daily, rebalances = module.simulate_portfolio(
            trade_dates,
            daily_frames,
            rebalance_targets,
            fee_rate=0.0,
            initial_capital=100.0,
        )

        self.assertAlmostEqual(float(daily.iloc[0]['equity']), 100.0)
        self.assertAlmostEqual(float(daily.iloc[1]['equity']), 110.0)
        self.assertEqual(rebalances.iloc[0]['effective_date'], '20240201')


if __name__ == '__main__':
    unittest.main()
