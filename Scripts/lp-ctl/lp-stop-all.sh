#!/usr/bin/env bash
# 一次停掉所有 running 的 live-paper 策略（释放内存；均为粘性 stop）
set -euo pipefail
LEAN_DIR="${LEAN_DIR:-/home/project/hope/Lean}"
TOKEN="$(cat "$LEAN_DIR/Results/soloquant/.strategy-api-token")"
curl -s "http://localhost:5000/api/strategies?group=soloquant-lp" \
 | python3 -c "import json,sys;[print(s['strategy_id']) for s in json.load(sys.stdin)['strategies'] if s['status']=='running']" \
 | while read -r sid; do
     curl -s -X POST "http://localhost:5000/api/strategies/$sid/stop" -H "Authorization: Bearer $TOKEN" >/dev/null
     echo "stopped $sid"
   done
