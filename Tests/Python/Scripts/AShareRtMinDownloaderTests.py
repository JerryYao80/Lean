import importlib.util
import tempfile
import unittest
from pathlib import Path

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / 'data-source' / 'tushare' / 'rt_min_downloader.py'
    if not module_path.exists():
        raise AssertionError(f'Expected module to exist: {module_path}')

    spec = importlib.util.spec_from_file_location('rt_min_downloader', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AShareRtMinDownloaderTests(unittest.TestCase):
    def test_save_frame_writes_per_date_symbol_partitions(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = module.RtMinDownloader(output_root=temp_dir, token='token')
            frame = pd.DataFrame([
                {
                    'ts_code': '510300.SH',
                    'trade_time': '2024-01-24 09:31:00',
                    'open': 1.0,
                    'high': 1.1,
                    'low': 0.9,
                    'close': 1.05,
                    'vol': 100,
                    'amount': 1000,
                },
                {
                    'ts_code': '510300.SH',
                    'trade_time': '2024-01-24 09:32:00',
                    'open': 1.05,
                    'high': 1.2,
                    'low': 1.0,
                    'close': 1.08,
                    'vol': 120,
                    'amount': 1400,
                },
            ])

            report = downloader.save_frame(frame)
            target = Path(temp_dir) / 'rt_min' / 'freq=1MIN' / 'date=20240124' / 'ts_code=510300.SH' / 'data.parquet'
            saved = pd.read_parquet(target)

        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['files'], 1)
        self.assertEqual(saved['feature_timestamp'].tolist(), ['2024-01-24 09:31:00', '2024-01-24 09:32:00'])

    def test_merge_rt_min_frames_deduplicates_same_timestamp(self):
        module = load_module()

        existing = pd.DataFrame([
            {'ts_code': '510300.SH', 'trade_date': '20240124', 'feature_timestamp': '2024-01-24 09:31:00', 'close': 1.01},
        ])
        incoming = pd.DataFrame([
            {'ts_code': '510300.SH', 'trade_date': '20240124', 'feature_timestamp': '2024-01-24 09:31:00', 'close': 1.02},
            {'ts_code': '510300.SH', 'trade_date': '20240124', 'feature_timestamp': '2024-01-24 09:32:00', 'close': 1.03},
        ])

        merged = module.merge_rt_min_frames(existing, incoming)

        self.assertEqual(merged['feature_timestamp'].tolist(), ['2024-01-24 09:31:00', '2024-01-24 09:32:00'])
        self.assertAlmostEqual(merged.loc[merged['feature_timestamp'] == '2024-01-24 09:31:00', 'close'].iloc[0], 1.02)


if __name__ == '__main__':
    unittest.main()
