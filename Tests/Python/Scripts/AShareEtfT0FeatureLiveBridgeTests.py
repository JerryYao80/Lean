import importlib.util
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / 'Scripts' / 'ashare_etf_t0_feature_live_bridge.py'
    if not module_path.exists():
        raise AssertionError(f'Expected module to exist: {module_path}')

    spec = importlib.util.spec_from_file_location('ashare_etf_t0_feature_live_bridge', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StaticQuoteClient:
    def __init__(self, frame: pd.DataFrame):
        self._frame = frame

    def fetch_quotes(self, ts_codes: list[str]) -> pd.DataFrame:
        return self._frame[self._frame['ts_code'].isin(ts_codes)].copy()


class AShareEtfT0FeatureLiveBridgeTests(unittest.TestCase):
    def test_build_live_feature_row_uses_previous_day_features_as_signals(self):
        module = load_module()
        history = pd.DataFrame([
            {
                'trade_date': '20240122',
                'close': 1.02,
                'pct_chg': 0.50,
                'amount': 1000,
                'momentum_5': 0.03,
                'momentum_20': 0.08,
                'liquidity_5': 1200,
                'close_location': 0.75,
                'volatility_10': 0.012,
                'gap_abs': 0.010,
                'nav_premium_1': 0.004,
                'nav_premium_z20': 1.2,
                'share_change_5': 0.01,
                'size_change_5': 0.02,
                'excess_gap': 0.003,
                'excess_intraday': -0.002,
                'tracking_error_10': 0.005,
                'index_momentum_5': -0.01,
                'unit_nav': 1.01,
                'adj_nav': 1.01,
            },
            {
                'trade_date': '20240123',
                'close': 1.03,
                'pct_chg': 0.60,
                'amount': 1100,
                'momentum_5': 0.04,
                'momentum_20': 0.09,
                'liquidity_5': 1300,
                'close_location': 0.65,
                'volatility_10': 0.013,
                'gap_abs': 0.011,
                'nav_premium_1': 0.005,
                'nav_premium_z20': 1.1,
                'share_change_5': 0.02,
                'size_change_5': 0.03,
                'excess_gap': 0.004,
                'excess_intraday': -0.001,
                'tracking_error_10': 0.006,
                'index_momentum_5': -0.02,
                'unit_nav': 1.02,
                'adj_nav': 1.02,
            },
        ])
        quote = {
            'ts_code': '510300.SH',
            'trade_date': '20240124',
            'feature_timestamp': '2024-01-24 09:35:00',
            'pre_close': 1.03,
            'open': 1.04,
            'high': 1.05,
            'low': 1.02,
            'price': 1.045,
            'amount': 1500,
            'vol': 200,
        }

        row = module.build_live_feature_row(history, quote)

        self.assertEqual(row['trade_date'], '20240124')
        self.assertEqual(row['feature_timestamp'], '2024-01-24 09:35:00')
        self.assertAlmostEqual(row['open'], 1.04)
        self.assertAlmostEqual(row['close'], 1.045)
        self.assertAlmostEqual(row['signal_momentum_20'], 0.09)
        self.assertAlmostEqual(row['signal_momentum_5'], 0.04)
        self.assertAlmostEqual(row['signal_nav_premium_1'], 0.005)
        self.assertAlmostEqual(row['signal_excess_intraday'], -0.001)

    def test_upsert_live_feature_file_replaces_same_day_snapshot(self):
        module = load_module()
        base_history = pd.DataFrame([
            {'trade_date': '20240123', 'close': 1.03, 'signal_momentum_20': 0.09},
        ])

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / 'sse' / 'daily' / '510300.csv'
            module.upsert_live_feature_file(target, base_history, {
                'trade_date': '20240124',
                'close': 1.04,
                'signal_momentum_20': 0.09,
                'feature_timestamp': '2024-01-24 09:35:00',
            })
            module.upsert_live_feature_file(target, base_history, {
                'trade_date': '20240124',
                'close': 1.06,
                'signal_momentum_20': 0.09,
                'feature_timestamp': '2024-01-24 10:05:00',
            })
            frame = pd.read_csv(target, dtype={'trade_date': str})

        self.assertEqual(frame['trade_date'].tolist(), ['20240123', '20240124'])
        self.assertEqual(frame.loc[frame['trade_date'] == '20240124', 'feature_timestamp'].iloc[0], '2024-01-24 10:05:00')
        self.assertAlmostEqual(frame.loc[frame['trade_date'] == '20240124', 'close'].iloc[0], 1.06)

    def test_load_live_bridge_config_reads_lean_parameter_block(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / 'Launcher' / 'config' / 'config.json'
            config_path.parent.mkdir(parents=True)
            config_path.write_text(json.dumps({
                'parameters': {
                    'feature-data-path': '../../../Data/alternative/ashare-etf-t0-live-features',
                    'live-feature-report-file': '../../../Results/bridge.json',
                    'live-feature-poll-interval-seconds': '15',
                    'live-feature-minute-frequency': '1min',
                    'exclude-money-market-etfs': 'false',
                }
            }), encoding='utf-8')

            module.repo_root = lambda: root
            config = module.load_live_bridge_config(config_path)

        self.assertEqual(config['live-feature-poll-interval-seconds'], 15)
        self.assertEqual(config['live-feature-minute-frequency'], '1MIN')
        self.assertFalse(config['exclude-money-market-etfs'])
        self.assertEqual(Path(config['feature-data-path']), root / 'Data' / 'alternative' / 'ashare-etf-t0-live-features')
        self.assertEqual(Path(config['live-feature-report-file']), root / 'Results' / 'bridge.json')

    def test_fetch_quotes_falls_back_to_single_symbol_rt_min_requests(self):
        module = load_module()

        class FakeApi:
            def __init__(self):
                self.calls = []

            def rt_min(self, ts_code=None, freq=None):
                self.calls.append((ts_code, freq))
                if isinstance(ts_code, str) and ',' in ts_code:
                    raise Exception('返回错误，请检查代码格式: 不支持的市场后缀: SZ,159109')
                return pd.DataFrame([
                    {
                        'ts_code': ts_code,
                        'trade_time': '2024-01-24 09:31:00',
                        'open': 1.0,
                        'high': 1.1,
                        'low': 0.9,
                        'close': 1.05,
                        'vol': 100,
                        'amount': 1000,
                    }
                ])

        client = module.TushareRealtimeMinuteClient('token', batch_size=4, frequency='1MIN')
        fake_api = FakeApi()
        client._api = fake_api

        quotes = client.fetch_quotes(['159101.SZ', '159109.SZ'])
        quotes_second = client.fetch_quotes(['159120.SZ'])

        self.assertEqual(fake_api.calls[0], ('159101.SZ,159109.SZ', '1MIN'))
        self.assertEqual(fake_api.calls[1], ('159101.SZ', '1MIN'))
        self.assertEqual(fake_api.calls[2], ('159109.SZ', '1MIN'))
        self.assertEqual(fake_api.calls[3], ('159120.SZ', '1MIN'))
        self.assertEqual(set(quotes['ts_code']), {'159101.SZ', '159109.SZ'})
        self.assertEqual(quotes_second['ts_code'].tolist(), ['159120.SZ'])

    def test_aggregate_minute_bars_rolls_up_rt_min_rows(self):
        module = load_module()
        minute_frame = pd.DataFrame([
            {
                'ts_code': '510300.SH',
                'trade_time': '2024-01-24 09:31:00',
                'open': 1.04,
                'close': 1.045,
                'high': 1.046,
                'low': 1.039,
                'vol': 100,
                'amount': 1000,
            },
            {
                'ts_code': '510300.SH',
                'trade_time': '2024-01-24 09:32:00',
                'open': 1.045,
                'close': 1.047,
                'high': 1.050,
                'low': 1.044,
                'vol': 120,
                'amount': 1400,
            },
            {
                'ts_code': '159915.SZ',
                'trade_time': '2024-01-24 09:31:00',
                'open': 2.01,
                'close': 2.02,
                'high': 2.03,
                'low': 2.00,
                'vol': 80,
                'amount': 900,
            },
        ])

        snapshots = module.TushareRealtimeMinuteClient.aggregate_minute_bars(minute_frame)
        snapshots = snapshots.set_index('ts_code')

        self.assertEqual(set(snapshots.index), {'510300.SH', '159915.SZ'})
        self.assertEqual(snapshots.loc['510300.SH', 'trade_date'], '20240124')
        self.assertEqual(snapshots.loc['510300.SH', 'feature_timestamp'], '2024-01-24 09:32:00')
        self.assertAlmostEqual(snapshots.loc['510300.SH', 'open'], 1.04)
        self.assertAlmostEqual(snapshots.loc['510300.SH', 'high'], 1.05)
        self.assertAlmostEqual(snapshots.loc['510300.SH', 'low'], 1.039)
        self.assertAlmostEqual(snapshots.loc['510300.SH', 'close'], 1.047)
        self.assertAlmostEqual(snapshots.loc['510300.SH', 'price'], 1.047)
        self.assertAlmostEqual(snapshots.loc['510300.SH', 'vol'], 220.0)
        self.assertAlmostEqual(snapshots.loc['510300.SH', 'amount'], 2400.0)

    def test_bootstrap_live_feature_files_writes_history_rows(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = module.default_config()
            config['feature-data-path'] = str(root / 'features')
            history_cache = {
                '510300.SH': pd.DataFrame([
                    {'trade_date': '20240101', 'close': 1.0, 'signal_momentum_20': 0.1},
                    {'trade_date': '20240102', 'close': 1.1, 'signal_momentum_20': 0.2},
                ])
            }

            written = module.bootstrap_live_feature_files(config, history_cache)
            feature_path = root / 'features' / 'sse' / 'daily' / '510300.csv'
            frame = pd.read_csv(feature_path, dtype={'trade_date': str})

        self.assertEqual(written, 1)
        self.assertEqual(frame['trade_date'].tolist(), ['20240101', '20240102'])
        self.assertIn('feature_timestamp', frame.columns)

    def test_refresh_live_feature_snapshots_writes_feature_files(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = module.default_config()
            config['feature-data-path'] = str(root / 'features')
            history_cache = {
                '510300.SH': pd.DataFrame([
                    {
                        'trade_date': '20240123',
                        'close': 1.03,
                        'pct_chg': 0.60,
                        'amount': 1100,
                        'momentum_5': 0.04,
                        'momentum_20': 0.09,
                        'liquidity_5': 1300,
                        'close_location': 0.65,
                        'volatility_10': 0.013,
                        'gap_abs': 0.011,
                    }
                ])
            }
            quotes = pd.DataFrame([
                {
                    'ts_code': '510300.SH',
                    'trade_date': '20240124',
                    'feature_timestamp': '2024-01-24 09:35:00',
                    'pre_close': 1.03,
                    'open': 1.04,
                    'high': 1.05,
                    'low': 1.02,
                    'price': 1.045,
                    'amount': 1500,
                    'vol': 200,
                }
            ])

            report = module.refresh_live_feature_snapshots(
                config,
                StaticQuoteClient(quotes),
                history_cache,
                current_time=datetime(2024, 1, 24, 9, 35, 0),
            )
            feature_path = root / 'features' / 'sse' / 'daily' / '510300.csv'
            frame = pd.read_csv(feature_path, dtype={'trade_date': str})

        self.assertEqual(report['written_count'], 1)
        self.assertEqual(frame.loc[frame['trade_date'] == '20240124', 'feature_timestamp'].iloc[0], '2024-01-24 09:35:00')


if __name__ == '__main__':
    unittest.main()
