import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import pandas as pd


def load_module():
    module_path = Path(__file__).resolve().parents[3] / 'Scripts' / 'export_ashare_stock_data.py'
    if not module_path.exists():
        raise AssertionError(f'Expected module to exist: {module_path}')

    spec = importlib.util.spec_from_file_location('export_ashare_stock_data', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AShareStockExportTests(unittest.TestCase):
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

    def read_zip_rows(self, zip_path: Path) -> list[str]:
        with zipfile.ZipFile(zip_path, 'r') as archive:
            entry = archive.namelist()[0]
            with archive.open(entry) as handle:
                return handle.read().decode('utf-8').strip().splitlines()

    def test_load_stock_universe_filters_active_a_share_markets(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog = self.make_catalog(root)
            self.write_parquet(root / 'stock_basic' / 'data.parquet', [
                {'ts_code': '600000.SH', 'market': '主板', 'list_status': 'L'},
                {'ts_code': '300750.SZ', 'market': '创业板', 'list_status': 'L'},
                {'ts_code': '688981.SH', 'market': '科创板', 'list_status': 'L'},
                {'ts_code': '430001.BJ', 'market': '北交所', 'list_status': 'L'},
                {'ts_code': '000003.SZ', 'market': '主板', 'list_status': 'D'},
            ])

            layer = module.TushareDataLayer(root, catalog)
            universe = module.load_stock_universe(layer)

        self.assertEqual(universe, ['300750.SZ', '600000.SH', '688981.SH'])

    def test_export_stock_universe_writes_zip_files_and_report(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog = self.make_catalog(root)
            tushare_root = root / 'tushare'
            lean_root = root / 'lean'
            report_path = root / 'report.json'

            self.write_parquet(tushare_root / 'stock_basic' / 'data.parquet', [
                {'ts_code': '600000.SH', 'market': '主板', 'list_status': 'L'},
                {'ts_code': '000001.SZ', 'market': '主板', 'list_status': 'L'},
            ])
            self.write_parquet(tushare_root / 'daily' / 'ts_code=600000.SH' / 'data.parquet', [
                {'ts_code': '600000.SH', 'trade_date': '20240102', 'open': 8.0, 'high': 8.2, 'low': 7.9, 'close': 8.1, 'vol': 12.5},
                {'ts_code': '600000.SH', 'trade_date': '20240103', 'open': 8.1, 'high': 8.3, 'low': 8.0, 'close': 8.2, 'vol': 13.0},
            ])
            self.write_parquet(tushare_root / 'daily' / 'ts_code=000001.SZ' / 'data.parquet', [
                {'ts_code': '000001.SZ', 'trade_date': '20240102', 'open': 10.0, 'high': 10.1, 'low': 9.9, 'close': 10.0, 'vol': 20.0},
            ])

            config = {
                'tushare-data-path': str(tushare_root),
                'dataset-catalog': str(catalog),
                'lean-data-path': str(lean_root),
                'start-date': '20240101',
                'end-date': '20240131',
                'report-file': str(report_path),
            }
            config_path = root / 'config.json'
            config_path.write_text(json.dumps(config), encoding='utf-8')

            report = module.export_stock_universe(module.load_pipeline_config(config_path))
            zip_rows = self.read_zip_rows(lean_root / 'equity' / 'sse' / 'daily' / '600000.zip')
            report_from_disk = json.loads(report_path.read_text(encoding='utf-8'))

        self.assertEqual(zip_rows, [
            '20240102 00:00,80000,82000,79000,81000,1250',
            '20240103 00:00,81000,83000,80000,82000,1300',
        ])
        self.assertEqual(report['stock_universe_count'], 2)
        self.assertEqual(report['exported_count'], 2)
        self.assertEqual(report_from_disk['lean_export_count'], 2)


if __name__ == '__main__':
    unittest.main()
