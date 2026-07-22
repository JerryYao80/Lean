#!/usr/bin/env python3

import argparse
import json
from pathlib import Path


def load_backtest_result(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def render_markdown(summary: dict) -> str:
    return '\n'.join([
        '# A股 T+1 回测报告',
        '',
        '## 基本信息',
        f"- 回测区间: {summary.get('start_date', '')} 至 {summary.get('end_date', '')}",
        f"- 初始资金: {summary.get('initial_capital', 0):,.2f} CNY",
        f"- 股票池数量: {summary.get('universe_count', 0)}",
        f"- 有效标的数: {summary.get('loaded_symbol_count', 0)}",
        '',
        '## 收益指标',
        f"- 总收益率: {summary.get('total_return', 0):.2%}",
        f"- 期末权益: {summary.get('final_equity', 0):,.2f}",
        '',
        '## 交易统计',
        f"- 交易日数: {summary.get('trade_days', 0)}",
        f"- 完整交易次数: {summary.get('completed_trades', 0)}",
        f"- 未平仓持仓: {summary.get('open_positions', 0)}",
        '',
        '## 策略参数',
        f"- Lookback: {summary.get('lookback_period', '')}",
        f"- Entry Threshold: {summary.get('entry_threshold', '')}",
        f"- Exit Threshold: {summary.get('exit_threshold', '')}",
        f"- Max Positions: {summary.get('max_positions', '')}",
        f"- Position Size: {summary.get('position_size', 0):.2%}",
        f"- Fee Rate: {summary.get('fee_rate', 0):.4%}",
        '',
    ])


def write_report(summary: dict, output: str | Path) -> Path:
    output = Path(output)
    if output.suffix.lower() != '.md':
        output.mkdir(parents=True, exist_ok=True)
        output = output / 'ashare-t1-report.md'
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(summary), encoding='utf-8')
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Generate a markdown report from A-share T+1 backtest summary JSON')
    parser.add_argument('--backtest-result', required=True)
    parser.add_argument('--output', required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = load_backtest_result(args.backtest_result)
    report_path = write_report(summary, args.output)
    print(report_path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
