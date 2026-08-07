#!/bin/bash
# Setup cron job for factor_worker backfill

echo "Setting up cron job for factor_worker..."

# Create cron job that runs every day at 3:00 AM
(crontab -l 2>/dev/null || true; echo "0 3 * * * /root/miniconda3/bin/python3 /home/project/hope/Lean/data-source/tushare/factor_worker.py --once >> /var/log/factor_worker_cron.log 2>&1") | sort -u | crontab -

echo "Cron job installed."
echo ""
echo "Current crontab:"
crontab -l | grep factor_worker || echo "No factor_worker cron job found"
