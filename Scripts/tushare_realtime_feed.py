#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from tushare_data_layer import TushareDataLayer


class TushareRealtimeDataFeed:
    def __init__(self, data_root: str | Path, catalog: str | Path | dict | None = None):
        self.layer = TushareDataLayer(data_root, catalog)

    def get_latest_snapshot(self, symbols: list[str]) -> dict[str, dict]:
        snapshot = {}
        for symbol in symbols:
            daily = self.layer.load_dataset('daily', symbol=symbol, fields=['open', 'high', 'low', 'close', 'vol'])
            if daily.empty:
                continue

            latest = daily.iloc[-1].to_dict()
            trade_date = str(latest['trade_date'])
            record = {
                'trade_date': trade_date,
                'open': float(latest.get('open', 0.0) or 0.0),
                'high': float(latest.get('high', 0.0) or 0.0),
                'low': float(latest.get('low', 0.0) or 0.0),
                'close': float(latest.get('close', 0.0) or 0.0),
                'vol': float(latest.get('vol', 0.0) or 0.0),
            }

            if 'stk_limit' in self.layer.catalog:
                limits = self.layer.load_dataset(
                    'stk_limit',
                    symbol=symbol,
                    start_date=trade_date,
                    end_date=trade_date,
                    fields=['up_limit', 'down_limit'],
                )
                if not limits.empty:
                    limit_row = limits.iloc[-1].to_dict()
                    if 'up_limit' in limit_row:
                        record['up_limit'] = float(limit_row['up_limit'])
                    if 'down_limit' in limit_row:
                        record['down_limit'] = float(limit_row['down_limit'])

            snapshot[symbol] = record

        return snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Fetch latest local Tushare snapshots for A-share symbols')
    parser.add_argument('--data-root', required=True)
    parser.add_argument('--catalog')
    parser.add_argument('--symbols', nargs='+', required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    feed = TushareRealtimeDataFeed(args.data_root, args.catalog)
    snapshot = feed.get_latest_snapshot(args.symbols)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
