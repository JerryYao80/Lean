#!/bin/bash
echo "========================================"
echo "Backfill Progress Check"
echo "========================================"
echo ""

# Check running processes
echo "--- Running Processes ---"
ps aux | grep -E "(complete_backfill|factor_worker)" | grep -v grep | grep -v monitor || echo "No processes running"
echo ""

# Check factor data counts
echo "--- Factor Data Files ---"
for dir in turnover_anomaly amplitude_anomaly limit_behavior intraday_reversal margin_factors; do
    count=$(ls /home/project/hope/Lean/result/factor-zoo/$dir/*.parquet 2>/dev/null | wc -l)
    echo "$dir: $count files"
done
echo ""

# Check latest log
echo "--- Latest Log Output ---"
if [ -f /tmp/complete_backfill.log ]; then
    tail -5 /tmp/complete_backfill.log
fi
echo ""

echo "========================================"
echo "Commands:"
echo "  tail -f /tmp/complete_backfill.log"
echo "  ps aux | grep backfill"
echo "========================================"
