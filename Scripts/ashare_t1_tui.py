#!/usr/bin/env python3

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tushare_realtime_feed import TushareRealtimeDataFeed

PATH_KEYS = {
    'dataset-catalog',
    'tushare-data-path',
    'portfolio-snapshot-file',
    'signal-file',
    'daily-summary-file',
}


def load_tui_config(config_path: str | Path) -> dict:
    config_path = Path(config_path).resolve()
    raw_config = json.loads(config_path.read_text(encoding='utf-8'))
    parameters = raw_config.get('parameters') or {}
    config = {**raw_config, **parameters}
    base_dir = config_path.parent
    if 'algorithm-location' in raw_config or 'job-queue-handler' in raw_config:
        base_dir = config_path.parent.parent / 'bin' / 'Debug'

    for key in PATH_KEYS:
        value = config.get(key)
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            config[key] = str((base_dir / path).resolve())

    return config


def normalize_signal_record(record: dict) -> dict:
    return {
        'signal_id': record.get('signal_id') or record.get('SignalId') or '',
        'timestamp': record.get('timestamp') or record.get('Timestamp') or '',
        'action': record.get('action') or record.get('Action') or '',
        'symbol': record.get('symbol') or record.get('Symbol') or '',
        'name': record.get('name') or record.get('Name') or '',
        'price': record.get('price') if 'price' in record else record.get('Price', ''),
        'quantity': record.get('quantity') if 'quantity' in record else record.get('Quantity', ''),
        'reason': record.get('reason') or record.get('Reason') or '',
        'confidence': record.get('confidence') if 'confidence' in record else record.get('Confidence', ''),
        'urgency': record.get('urgency') or record.get('Urgency') or '',
    }


def load_runtime_state(portfolio_snapshot_path: str | Path, signal_path: str | Path) -> dict:
    portfolio_path = Path(portfolio_snapshot_path)
    signal_file = Path(signal_path)

    portfolio = json.loads(portfolio_path.read_text(encoding='utf-8')) if portfolio_path.exists() else {'cash': 0, 'positions': []}
    signals: list[dict] = []
    if signal_file.exists():
        if signal_file.suffix.lower() == '.json':
            signals = json.loads(signal_file.read_text(encoding='utf-8'))
        elif signal_file.suffix.lower() == '.jsonl':
            with signal_file.open('r', encoding='utf-8') as handle:
                signals = [json.loads(line) for line in handle if line.strip()]
        else:
            with signal_file.open('r', encoding='utf-8', newline='') as handle:
                signals = list(csv.DictReader(handle))

    signals = [normalize_signal_record(signal) for signal in signals]
    signals = sorted(signals, key=lambda row: row.get('timestamp', ''), reverse=True)
    return {'portfolio': portfolio, 'signals': signals}


def compute_position_rows(positions: list[dict]) -> list[dict]:
    rows = []
    for position in positions:
        quantity = float(position.get('quantity', 0) or 0)
        available_quantity = float(position.get('available_quantity', position.get('available', quantity)) or 0)
        cost_price = float(position.get('cost_price', position.get('cost', 0)) or 0)
        last_price = float(position.get('last_price', position.get('price', 0)) or 0)
        pnl = (last_price - cost_price) * quantity
        pnl_pct = (last_price / cost_price - 1) if cost_price else 0.0
        available_label = '0 (T+1)' if quantity > 0 and available_quantity <= 0 else f'{int(available_quantity) if available_quantity.is_integer() else available_quantity}'
        rows.append({
            'symbol': position.get('symbol', ''),
            'name': position.get('name', ''),
            'quantity': quantity,
            'available_quantity': available_quantity,
            'available_label': available_label,
            'cost_price': cost_price,
            'last_price': last_price,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
        })
    return rows


class AShareT1TUI:
    def __init__(self, config_path: str | Path):
        self.console = Console()
        self.config = load_tui_config(config_path)
        self.feed = None
        data_root = self.config.get('tushare-data-path')
        if data_root:
            self.feed = TushareRealtimeDataFeed(data_root, self.config.get('dataset-catalog'))
        self.portfolio_snapshot_path = Path(self.config['portfolio-snapshot-file'])
        self.signal_file_path = Path(self.config['signal-file'])
        self.refresh_seconds = float(self.config.get('refresh-seconds', 3))

    def create_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name='header', size=3),
            Layout(name='account', size=7),
            Layout(name='signals', size=8),
            Layout(name='positions'),
            Layout(name='footer', size=1),
        )
        return layout

    def render_header(self) -> Panel:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        text = Text()
        text.append('A股 T+1 Live-Paper TUI', style='bold cyan')
        text.append(f'  {now}', style='dim')
        return Panel(text, border_style='cyan')

    def render_account(self, portfolio: dict, position_rows: list[dict]) -> Panel:
        cash = float(portfolio.get('cash', 0) or 0)
        market_value = float(portfolio.get('market_value', sum(row['quantity'] * row['last_price'] for row in position_rows)) or 0)
        total_value = float(portfolio.get('total_value', cash + market_value) or (cash + market_value))
        total_pnl = float(portfolio.get('total_pnl', total_value - float(portfolio.get('initial_capital', total_value) or total_value)) or 0)
        total_return = float(portfolio.get('total_return', 0) or 0)
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column(style='cyan')
        table.add_column(style='white')
        table.add_row('总资产:', f'¥{total_value:,.2f}')
        table.add_row('可用资金:', f'¥{cash:,.2f}')
        table.add_row('持仓市值:', f'¥{market_value:,.2f}')
        table.add_row('累计收益:', f'¥{total_pnl:,.2f}')
        table.add_row('累计收益率:', f'{total_return:.2%}')
        table.add_row('持仓数量:', str(len(position_rows)))
        return Panel(table, title='账户总览', border_style='green')

    def render_signals(self, signals: list[dict]) -> Panel:
        table = Table(show_header=True, box=None)
        table.add_column('时间')
        table.add_column('操作', style='bold')
        table.add_column('代码')
        table.add_column('名称')
        table.add_column('价格', justify='right')
        table.add_column('数量', justify='right')
        for signal in signals[:10]:
            action = signal.get('action', '')
            action_style = 'green' if str(action).upper() == 'BUY' else 'red'
            table.add_row(
                signal.get('timestamp', ''),
                f'[{action_style}]{action}[/{action_style}]',
                signal.get('symbol', ''),
                signal.get('name', ''),
                str(signal.get('price', '')),
                str(signal.get('quantity', '')),
            )
        return Panel(table, title=f'交易信号 ({len(signals)})', border_style='yellow')

    def render_positions(self, rows: list[dict]) -> Panel:
        table = Table(show_header=True)
        table.add_column('代码')
        table.add_column('名称')
        table.add_column('数量', justify='right')
        table.add_column('可卖', justify='right')
        table.add_column('成本', justify='right')
        table.add_column('现价', justify='right')
        table.add_column('盈亏', justify='right')
        table.add_column('盈亏率', justify='right')
        for row in rows:
            pnl_color = 'green' if row['pnl'] >= 0 else 'red'
            table.add_row(
                row['symbol'],
                row['name'],
                f"{int(row['quantity']) if float(row['quantity']).is_integer() else row['quantity']}",
                row['available_label'],
                f"¥{row['cost_price']:.2f}",
                f"¥{row['last_price']:.2f}",
                f"[{pnl_color}]¥{row['pnl']:.2f}[/{pnl_color}]",
                f"[{pnl_color}]{row['pnl_pct']:.2%}[/{pnl_color}]",
            )
        return Panel(table, title='持仓明细', border_style='blue')

    def render_footer(self) -> Panel:
        return Panel(Text('Press Ctrl+C to exit', style='dim'), border_style='dim')

    def enrich_prices(self, portfolio: dict) -> dict:
        if not self.feed:
            return portfolio
        positions = portfolio.get('positions', [])
        symbols = [position.get('symbol') for position in positions if position.get('symbol')]
        if not symbols:
            return portfolio
        snapshot = self.feed.get_latest_snapshot(symbols)
        enriched = json.loads(json.dumps(portfolio, ensure_ascii=False))
        for position in enriched.get('positions', []):
            latest = snapshot.get(position.get('symbol'))
            if latest and 'close' in latest:
                position['last_price'] = latest['close']
        return enriched

    def run(self) -> None:
        layout = self.create_layout()
        with Live(layout, console=self.console, refresh_per_second=2) as live:
            while True:
                state = load_runtime_state(self.portfolio_snapshot_path, self.signal_file_path)
                portfolio = self.enrich_prices(state['portfolio'])
                rows = compute_position_rows(portfolio.get('positions', []))
                layout['header'].update(self.render_header())
                layout['account'].update(self.render_account(portfolio, rows))
                layout['signals'].update(self.render_signals(state['signals']))
                layout['positions'].update(self.render_positions(rows))
                layout['footer'].update(self.render_footer())
                live.refresh()
                time.sleep(self.refresh_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Render A-share T+1 live-paper state in a terminal UI')
    parser.add_argument('config')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    AShareT1TUI(args.config).run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
