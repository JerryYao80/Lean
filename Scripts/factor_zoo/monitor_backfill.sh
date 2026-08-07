#!/bin/bash
# Monitor backfill progress

echo "========================================"
echo "Backfill Monitor"
echo "========================================"
echo ""

# Check fast backfill
if [ -f /tmp/fast_backfill.log ]; then
    echo "--- Fast Backfill (Last 500 days) ---"
    tail -10 /tmp/fast_backfill.log
fi

echo ""
echo "--- Factor Data Count ---"
for factor in turnover_anomaly amplitude_anomaly limit_behavior intraday_reversal; do
    count=$(ls /home/project/hope/Lean/result/factor-zoo/$factor/*.parquet 2>/dev/null | wc -l)
    echo "$factor: $count files"
done

echo ""
echo "--- Running Processes ---"
ps aux | grep -E "(fast_backfill|factor_worker)" | grep -v grep | grep -v monitor || echo "No running processes"

echo ""
echo "========================================"
echo "Commands:"
echo "  tail -f /tmp/fast_backfill.log     # Monitor fast backfill"
echo "  ./monitor_backfill.sh              # Check status"
echo "  ps aux | grep backfill             # Check processes"
echo "========================================"
