import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / 'Scripts' / 'tushare_realtime_feed.py'
    if not module_path.exists():
        raise AssertionError(f'Expected module to exist: {module_path}')

    spec = importlib.util.spec_from_file_location('tushare_realtime_feed', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TushareRealtimeFeedTests(unittest.TestCase):
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
                'stk_limit': {
                    'path': 'stk_limit/ts_code={symbol}/data.parquet',
                    'date_field': 'trade_date',
                    'symbol_field': 'ts_code',
                },
            }
        }
        path = root / 'catalog.json'
        path.write_text(json.dumps(catalog), encoding='utf-8')
        return path

    def test_get_latest_snapshot_returns_latest_price_and_limits(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog = self.make_catalog(root)
            self.write_parquet(root / 'daily' / 'ts_code=600000.SH' / 'data.parquet', [
                {'ts_code': '600000.SH', 'trade_date': '20240102', 'open': 8.0, 'high': 8.3, 'low': 7.9, 'close': 8.1, 'vol': 10.0},
                {'ts_code': '600000.SH', 'trade_date': '20240103', 'open': 8.1, 'high': 8.4, 'low': 8.0, 'close': 8.2, 'vol': 11.0},
            ])
            self.write_parquet(root / 'stk_limit' / 'ts_code=600000.SH' / 'data.parquet', [
                {'ts_code': '600000.SH', 'trade_date': '20240103', 'up_limit': 8.91, 'down_limit': 7.29},
            ])

            feed = module.TushareRealtimeDataFeed(root, catalog)
            snapshot = feed.get_latest_snapshot(['600000.SH'])

        self.assertEqual(snapshot['600000.SH']['trade_date'], '20240103')
        self.assertEqual(snapshot['600000.SH']['close'], 8.2)
        self.assertEqual(snapshot['600000.SH']['up_limit'], 8.91)
        self.assertEqual(snapshot['600000.SH']['down_limit'], 7.29)


if __name__ == '__main__':
    unittest.main()
