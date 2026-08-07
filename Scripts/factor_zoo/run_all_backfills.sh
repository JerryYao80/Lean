#!/bin/bash
# Run all backfills for manipulation and margin factors

set -e

echo "========================================"
echo "Starting All Factor Backfills"
echo "========================================"
echo ""

# 1. Manipulation detection factors (last 500 days)
echo "--- Phase 1: Manipulation Detection Factors ---"
python3 /tmp/run_fast_backfill.py > /tmp/fast_backfill.log 2>&1 &
MANIP_PID=$!
echo "  Started: PID=$MANIP_PID"

# 2. Margin factors (all historical data)
echo "--- Phase 2: Margin Trading Factors ---"
python3 /tmp/run_margin_backfill.py > /tmp/margin_backfill.log 2>&1 &
MARGIN_PID=$!
echo "  Started: PID=$MARGIN_PID"

# 3. Start factor_worker daemon
echo "--- Phase 3: Factor Worker Daemon ---"
python3 /home/project/hope/Lean/data-source/tushare/factor_worker.py --poll-seconds 3600 > /tmp/factor_worker_daemon.log 2>&1 &
WORKER_PID=$!
echo "  Started: PID=$WORKER_PID"

echo ""
echo "========================================"
echo "All processes started!"
echo "========================================"
echo ""
echo "Monitor commands:"
echo "  tail -f /tmp/fast_backfill.log     # Manipulation factors"
echo "  tail -f /tmp/margin_backfill.log   # Margin factors"
echo "  tail -f /tmp/factor_worker_daemon.log  # Factor worker"
echo ""
echo "Status check:"
echo "  /home/project/hope/Lean/Scripts/factor_zoo/monitor_backfill.sh"
echo ""
echo "PIDs:"
echo "  Manipulation: $MANIP_PID"
echo "  Margin:       $MARGIN_PID"
echo "  Worker:       $WORKER_PID"

# Save PIDs
save_pids() {
    cat > /tmp/backfill_pids.txt << PIDEOF
manipulation=$MANIP_PID
margin=$MARGIN_PID
worker=$WORKER_PID
PIDEOF
}

save_pids

echo ""
echo "PIDs saved to /tmp/backfill_pids.txt"
