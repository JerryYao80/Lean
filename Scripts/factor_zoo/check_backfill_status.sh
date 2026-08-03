#!/bin/bash
# Check backfill progress

echo "========================================"
echo "Backfill Status Check"
echo "========================================"
echo ""

# Check if process is running
PID=$(pgrep -f backfill_manipulation.sh | head -1)
if [ -n "$PID" ]; then
    echo "Status: RUNNING (PID: $PID)"
else
    echo "Status: NOT RUNNING"
fi

echo ""
echo "--- Recent Log Output ---"
if [ -f /tmp/backfill_2024q1.log ]; then
    tail -20 /tmp/backfill_2024q1.log
else
    echo "No log file found"
fi

echo ""
echo "--- Factor Data Count ---"
for factor in turnover_anomaly amplitude_anomaly limit_behavior intraday_reversal; do
    count=$(ls /home/project/hope/Lean/result/factor-zoo/$factor/*.parquet 2>/dev/null | wc -l)
    echo "$factor: $count files"
done

echo ""
echo "========================================"
echo "To monitor in real-time:"
echo "  tail -f /tmp/backfill_2024q1.log"
echo "========================================"
