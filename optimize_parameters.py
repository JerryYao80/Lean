#!/usr/bin/env python3
"""
Parameter optimization script for ETF Momentum Strategy
Tests different parameter combinations and finds the optimal configuration
"""
import subprocess
import json
import re
from datetime import datetime
from pathlib import Path

# Parameter grid to test
PARAM_GRID = {
    'lookback_period': [10, 20, 30, 60],
    'rebalance_days': [1, 5, 10, 20],
    'top_n': [3, 5, 10]
}

# Results storage
results = []

def run_backtest(lookback, rebalance, topn):
    """Run a single backtest with given parameters"""
    print(f"\n{'='*80}")
    print(f"Testing: Lookback={lookback}, Rebalance={rebalance}, TopN={topn}")
    print(f"{'='*80}")

    # Modify the strategy file with new parameters
    strategy_file = Path('/home/project/hope/Lean/Algorithm.CSharp/ETFMomentumStrategyOptimized.cs')
    content = strategy_file.read_text()

    # Replace parameter values
    content = re.sub(r'private int _rebalanceDays = \d+;',
                     f'private int _rebalanceDays = {rebalance};', content)
    content = re.sub(r'private int _lookbackPeriod = \d+;',
                     f'private int _lookbackPeriod = {lookback};', content)
    content = re.sub(r'private int _topN = \d+;',
                     f'private int _topN = {topn};', content)

    strategy_file.write_text(content)

    # Recompile
    print("Compiling...")
    compile_result = subprocess.run(
        ['dotnet', 'build', 'Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj', '-c', 'Debug'],
        cwd='/home/project/hope/Lean',
        capture_output=True,
        text=True,
        timeout=120
    )

    if compile_result.returncode != 0:
        print(f"❌ Compilation failed!")
        return None

    # Update config to use optimized strategy
    config_file = Path('/home/project/hope/Lean/Launcher/config.json')
    config = json.loads(config_file.read_text())
    config['algorithm-type-name'] = 'ETFMomentumStrategyOptimized'
    config_file.write_text(json.dumps(config, indent=2))

    # Run backtest
    print("Running backtest...")
    try:
        backtest_result = subprocess.run(
            ['dotnet', 'QuantConnect.Lean.Launcher.dll'],
            cwd='/home/project/hope/Lean/Launcher/bin/Debug',
            capture_output=True,
            text=True,
            timeout=300
        )

        output = backtest_result.stdout + backtest_result.stderr

        # Parse results
        stats = {}
        for line in output.split('\n'):
            if 'STATISTICS::' in line:
                parts = line.split('STATISTICS::')[1].strip().split(' ', 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip()
                    stats[key] = value

        if stats:
            result = {
                'lookback_period': lookback,
                'rebalance_days': rebalance,
                'top_n': topn,
                'total_return': stats.get('Net', 'N/A'),
                'annual_return': stats.get('Compounding', 'N/A'),
                'sharpe_ratio': stats.get('Sharpe', 'N/A'),
                'max_drawdown': stats.get('Drawdown', 'N/A'),
                'win_rate': stats.get('Win', 'N/A'),
                'total_orders': stats.get('Total', 'N/A'),
                'timestamp': datetime.now().isoformat()
            }

            print(f"\n✅ Results:")
            print(f"   Total Return: {result['total_return']}")
            print(f"   Annual Return: {result['annual_return']}")
            print(f"   Sharpe Ratio: {result['sharpe_ratio']}")
            print(f"   Max Drawdown: {result['max_drawdown']}")

            return result
        else:
            print("❌ Failed to parse results")
            return None

    except subprocess.TimeoutExpired:
        print("❌ Backtest timeout")
        return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

def main():
    print("="*80)
    print("ETF Momentum Strategy - Parameter Optimization")
    print("="*80)
    print(f"\nParameter Grid:")
    print(f"  Lookback Period: {PARAM_GRID['lookback_period']}")
    print(f"  Rebalance Days: {PARAM_GRID['rebalance_days']}")
    print(f"  Top N: {PARAM_GRID['top_n']}")

    total_combinations = (len(PARAM_GRID['lookback_period']) *
                         len(PARAM_GRID['rebalance_days']) *
                         len(PARAM_GRID['top_n']))
    print(f"\nTotal combinations to test: {total_combinations}")
    print(f"Estimated time: {total_combinations * 5} minutes\n")

    input("Press Enter to start optimization...")

    # Run grid search
    count = 0
    for lookback in PARAM_GRID['lookback_period']:
        for rebalance in PARAM_GRID['rebalance_days']:
            for topn in PARAM_GRID['top_n']:
                count += 1
                print(f"\n[{count}/{total_combinations}] Testing combination...")

                result = run_backtest(lookback, rebalance, topn)
                if result:
                    results.append(result)

                    # Save intermediate results
                    with open('/home/project/hope/Lean/optimization_results.json', 'w') as f:
                        json.dump(results, f, indent=2)

    # Generate report
    print("\n" + "="*80)
    print("OPTIMIZATION COMPLETE")
    print("="*80)

    if results:
        # Sort by total return
        sorted_results = sorted(results,
                               key=lambda x: float(x['total_return'].replace('%', '')) if '%' in x['total_return'] else 0,
                               reverse=True)

        print("\n🏆 Top 5 Configurations by Total Return:")
        print("-"*80)
        for i, result in enumerate(sorted_results[:5], 1):
            print(f"\n{i}. Lookback={result['lookback_period']}, "
                  f"Rebalance={result['rebalance_days']}, "
                  f"TopN={result['top_n']}")
            print(f"   Total Return: {result['total_return']}")
            print(f"   Annual Return: {result['annual_return']}")
            print(f"   Sharpe Ratio: {result['sharpe_ratio']}")
            print(f"   Max Drawdown: {result['max_drawdown']}")

        # Save final report
        with open('/home/project/hope/Lean/optimization_report.json', 'w') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'total_tests': len(results),
                'top_configurations': sorted_results[:10],
                'all_results': sorted_results
            }, f, indent=2)

        print(f"\n✅ Full results saved to: optimization_results.json")
        print(f"✅ Report saved to: optimization_report.json")
    else:
        print("\n❌ No successful backtests completed")

if __name__ == '__main__':
    main()
