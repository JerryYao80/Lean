#!/usr/bin/env python3

import argparse
import json
import sys
import zipfile
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from tushare_data_layer import TushareDataLayer
from tushare_lean_export import build_export_rows, ts_code_to_lean_parts, validate_zip_rows

PATH_KEYS = {
    'tushare-data-path',
    'dataset-catalog',
    'lean-data-path',
    'report-file',
}
DEFAULT_INCLUDED_MARKETS = ('主板', '创业板', '科创板')


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config() -> dict:
    root = repo_root()
    return {
        'tushare-data-path': '/home/project/tushare-downloader/tushare_data',
        'dataset-catalog': str(root / 'Launcher' / 'config' / 'config-ashare-dataset-catalog.json'),
        'lean-data-path': str(root / 'Data'),
        'start-date': '20180101',
        'end-date': '20251231',
        'included-markets': list(DEFAULT_INCLUDED_MARKETS),
        'report-file': str(root / 'Results' / 'ashare-stock-export-report.json'),
    }


def resolve_config_paths(config: dict, base_dir: Path) -> dict:
    resolved = dict(config)
    for key in PATH_KEYS:
        value = resolved.get(key)
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        resolved[key] = str(path)
    return resolved


def load_pipeline_config(config_path: str | Path | None = None, overrides: dict | None = None) -> dict:
    config = default_config()

    if config_path:
        config_path = Path(config_path).resolve()
        loaded = json.loads(config_path.read_text(encoding='utf-8'))
        config.update(resolve_config_paths(loaded, config_path.parent))

    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            config[key] = value

    return resolve_config_paths(config, repo_root())


def load_stock_universe(
    layer: TushareDataLayer,
    explicit_universe: list[str] | None = None,
    included_markets: list[str] | tuple[str, ...] | None = None,
    list_status: str = 'L',
) -> list[str]:
    if explicit_universe:
        return sorted({str(symbol) for symbol in explicit_universe})

    frame = layer.load_dataset('stock_basic')
    if frame.empty or 'ts_code' not in frame.columns:
        return []

    data = frame.copy()
    if 'list_status' in data.columns:
        data = data[data['list_status'] == list_status]

    markets = tuple(included_markets or DEFAULT_INCLUDED_MARKETS)
    if 'market' in data.columns:
        data = data[data['market'].isin(markets)]

    return sorted(data['ts_code'].dropna().astype(str).unique().tolist())


def lean_daily_paths(lean_data_path: str | Path, ts_code: str) -> tuple[Path, Path]:
    ticker, market = ts_code_to_lean_parts(ts_code)
    directory = Path(lean_data_path) / 'equity' / market / 'daily'
    return directory / f'{ticker}.csv', directory / f'{ticker}.zip'


def load_rows(layer: TushareDataLayer, ts_code: str, start_date: str | None, end_date: str | None) -> list[str]:
    frame = layer.load_dataset(
        'daily',
        symbol=ts_code,
        start_date=start_date,
        end_date=end_date,
        fields=['open', 'high', 'low', 'close', 'vol'],
    )
    if frame.empty:
        return []
    return build_export_rows(frame, start_date=start_date, end_date=end_date)


def export_symbol(layer: TushareDataLayer, ts_code: str, lean_data_path: str | Path, start_date: str | None, end_date: str | None) -> bool:
    rows = load_rows(layer, ts_code, start_date, end_date)
    if not rows:
        return False

    csv_path, zip_path = lean_daily_paths(lean_data_path, ts_code)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    ticker, _ = ts_code_to_lean_parts(ts_code)

    csv_path.write_text('Date,Open,High,Low,Close,Volume\n' + '\n'.join(rows), encoding='utf-8')
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f'{ticker.lower()}.csv', '\n'.join(rows))

    validate_zip_rows(zip_path, ticker, rows)
    return True


def collect_coverage_report(
    layer: TushareDataLayer,
    universe: list[str],
    lean_data_path: str | Path,
    start_date: str | None,
    end_date: str | None,
) -> dict:
    missing_parquet = []
    missing_lean_export = []
    parquet_available = 0
    lean_export_count = 0

    for ts_code in universe:
        rows = load_rows(layer, ts_code, start_date, end_date)
        if rows:
            parquet_available += 1
        else:
            missing_parquet.append(ts_code)

        _, zip_path = lean_daily_paths(lean_data_path, ts_code)
        if zip_path.exists():
            lean_export_count += 1
        elif rows:
            missing_lean_export.append(ts_code)

    return {
        'stock_universe_count': len(universe),
        'parquet_available_count': parquet_available,
        'lean_export_count': lean_export_count,
        'missing_parquet_symbols': missing_parquet,
        'missing_lean_export_symbols': missing_lean_export,
        'start_date': start_date,
        'end_date': end_date,
    }


def export_stock_universe(config: dict) -> dict:
    layer = TushareDataLayer(config['tushare-data-path'], config['dataset-catalog'])
    universe = load_stock_universe(
        layer,
        explicit_universe=config.get('universe'),
        included_markets=config.get('included-markets'),
        list_status=config.get('list-status', 'L'),
    )

    exported_symbols = []
    for ts_code in universe:
        if export_symbol(layer, ts_code, config['lean-data-path'], config.get('start-date'), config.get('end-date')):
            exported_symbols.append(ts_code)

    report = collect_coverage_report(
        layer,
        universe,
        config['lean-data-path'],
        config.get('start-date'),
        config.get('end-date'),
    )
    report['exported_symbols'] = exported_symbols
    report['exported_count'] = len(exported_symbols)

    report_file = config.get('report-file')
    if report_file:
        report_path = Path(report_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Export A-share stock daily data to LEAN format')
    parser.add_argument('--config')
    parser.add_argument('--tushare-data-path')
    parser.add_argument('--dataset-catalog')
    parser.add_argument('--lean-data-path')
    parser.add_argument('--start-date')
    parser.add_argument('--end-date')
    parser.add_argument('--report-file')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    overrides = {
        'tushare-data-path': args.tushare_data_path,
        'dataset-catalog': args.dataset_catalog,
        'lean-data-path': args.lean_data_path,
        'start-date': args.start_date,
        'end-date': args.end_date,
        'report-file': args.report_file,
    }
    report = export_stock_universe(load_pipeline_config(args.config, overrides))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
