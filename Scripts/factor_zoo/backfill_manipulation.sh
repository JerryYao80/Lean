#!/bin/bash
# Backfill manipulation detection factors for a date range
# Usage: ./backfill_manipulation.sh 2024-01-01 2024-02-01

set -e

START_DATE=$1
END_DATE=$2
DATA_ROOT="/home/project/tushare-downloader/tushare_data_v2"
RESULT_ROOT="/home/project/hope/Lean/result"

echo "========================================"
echo "Backfill Manipulation Detection Factors"
echo "Period: $START_DATE to $END_DATE"
echo "========================================"

# Get trade dates from calendar
python3 << PYEOF
import pandas as pd
from pathlib import Path

cal = pd.read_parquet("$DATA_ROOT/trade_cal/data.parquet")
cal["cal_date"] = cal["cal_date"].astype(str)
cal = cal[cal["is_open"].isin({1, "1", "True", "true"})]
cal = cal[(cal["cal_date"] >= "$START_DATE".replace("-", "")) & 
          (cal["cal_date"] <= "$END_DATE".replace("-", ""))]
dates = sorted(cal["cal_date"].tolist())

with open("/tmp/backfill_dates.txt", "w") as f:
    for d in dates:
        f.write(f"{d[:4]}-{d[4:6]}-{d[6:]}\n")

print(f"Found {len(dates)} trade dates")
PYEOF

# Load CSI300 universe once
python3 << PYEOF
import sys
sys.path.insert(0, "/home/project/hope/Lean/data-source/tushare")
from barra_cne5_data_loader import BarraCNE5DataLoader

loader = BarraCNE5DataLoader("$DATA_ROOT")
# Use end date for universe
universe = loader.load_index_constituents(asof_date="${END_DATE//-}", index_code="000300.SH")

with open("/tmp/backfill_stocks.txt", "w") as f:
    for s in universe:
        f.write(f"{s}\n")

print(f"Loaded {len(universe)} stocks from CSI300")
PYEOF

# Build each factor
echo ""
echo "Building factors..."

for factor in turnover_anomaly amplitude_anomaly limit_behavior intraday_reversal; do
    echo ""
    echo "----------------------------------------"
    echo "Factor: $factor"
    echo "----------------------------------------"
    
    count=0
    total=$(wc -l < /tmp/backfill_dates.txt)
    
    while IFS= read -r date; do
        count=$((count + 1))
        echo -ne "  [$count/$total] $date ... "
        
        # Check if already exists
        parquet_file="$RESULT_ROOT/factor-zoo/$factor/$date.parquet"
        if [ -f "$parquet_file" ]; then
            echo "EXISTS"
            continue
        fi
        
        # Build factor
        python3 "/home/project/hope/Lean/data-source/tushare/factor_builders/${factor}_builder.py" \
            --date "$date" \
            --ts-codes $(cat /tmp/backfill_stocks.txt | head -50 | tr '\n' ' ') \
            --data-root "$DATA_ROOT" \
            --result-root "$RESULT_ROOT" \
            2>/dev/null | tail -1
            
        if [ $? -eq 0 ]; then
            echo "OK"
        else
            echo "FAIL"
        fi
    done < /tmp/backfill_dates.txt
    
    echo "  Factor $factor complete"
done

echo ""
echo "========================================"
echo "Backfill complete!"
echo "========================================"
